"""Gateway-owned acquisition usage counters and verification state."""

from __future__ import annotations

import math
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from envresearch.models.evidence import AcquisitionBudget, DatasetCandidate

VerificationState = Literal["gateway_measured", "trusted_evidence", "unverified"]
Clock = Callable[[], float]
UsageEvidenceProvider = Callable[[object, DatasetCandidate, str], object]


class _LegacyConnector(Protocol):
    def acquire(self, candidate: DatasetCandidate, target: Path) -> object: ...


class UsageVerification(BaseModel):
    """Independent verification source for every governed usage dimension."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    bytes: VerificationState
    local_storage_bytes: VerificationState
    api_calls: VerificationState
    external_cost: VerificationState
    elapsed_seconds: VerificationState

    @model_validator(mode="after")
    def require_dimension_specific_sources(self) -> UsageVerification:
        """Reject impossible provenance labels even when every field is nonempty."""
        for field in ("bytes", "local_storage_bytes", "elapsed_seconds"):
            if getattr(self, field) not in {"gateway_measured", "unverified"}:
                raise ValueError(f"{field} must use gateway measurement")
        for field in ("api_calls", "external_cost"):
            if getattr(self, field) not in {"trusted_evidence", "unverified"}:
                raise ValueError(f"{field} must use trusted evidence")
        return self


class VerifiedUsage(BaseModel):
    """Measured values with an explicit verification state per dimension."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    bytes: StrictInt | None = Field(default=None, ge=0)
    local_storage_bytes: StrictInt | None = Field(default=None, ge=0)
    api_calls: StrictInt | None = Field(default=None, ge=0)
    external_cost: Decimal | None = None
    elapsed_seconds: StrictInt | None = Field(default=None, ge=0)
    verification: UsageVerification

    @model_validator(mode="after")
    def require_values_exactly_when_verified(self) -> VerifiedUsage:
        """Prevent an unverified connector assertion from looking measured."""
        for field in (
            "bytes",
            "local_storage_bytes",
            "api_calls",
            "external_cost",
            "elapsed_seconds",
        ):
            value = getattr(self, field)
            state = getattr(self.verification, field)
            if (value is None) != (state == "unverified"):
                raise ValueError(f"{field} value and verification state disagree")
        if self.external_cost is not None and (
            not self.external_cost.is_finite() or self.external_cost < 0
        ):
            raise ValueError("external_cost must be finite and nonnegative")
        return self

    @property
    def fully_verified(self) -> bool:
        """Return whether every governed dimension has independent evidence."""
        return all(
            getattr(self.verification, field) != "unverified"
            for field in (
                "bytes",
                "local_storage_bytes",
                "api_calls",
                "external_cost",
                "elapsed_seconds",
            )
        )

    def with_file_measurement(
        self, *, bytes_count: int, local_storage_bytes: int
    ) -> VerifiedUsage:
        """Attach descriptor-derived file measurements to this call usage."""
        return type(self).model_validate(
            self.model_dump(mode="python")
            | {
                "bytes": bytes_count,
                "local_storage_bytes": local_storage_bytes,
                "verification": self.verification.model_dump(mode="python")
                | {
                    "bytes": "gateway_measured",
                    "local_storage_bytes": "gateway_measured",
                },
            }
        )

    def matches_receipt_values(
        self,
        *,
        bytes_count: int,
        local_storage_bytes: int,
        api_calls: int,
        external_cost: Decimal,
        elapsed_seconds: int,
    ) -> bool:
        """Bind persisted usage evidence to the receipt consumed by policy."""
        return (
            self.bytes,
            self.local_storage_bytes,
            self.api_calls,
            self.external_cost,
            self.elapsed_seconds,
        ) == (
            bytes_count,
            local_storage_bytes,
            api_calls,
            external_cost,
            elapsed_seconds,
        )


class TrustedUsageEvidence(BaseModel):
    """API and billing evidence supplied outside the connector trust boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    api_calls: StrictInt = Field(ge=0)
    external_cost: Decimal

    @model_validator(mode="after")
    def require_safe_cost(self) -> TrustedUsageEvidence:
        if not self.external_cost.is_finite() or self.external_cost < 0:
            raise ValueError("external_cost must be finite and nonnegative")
        return self


class GatewayUsageSession:
    """Measure one connector invocation, including exceptional exits."""

    def __init__(
        self,
        clock: Clock,
        *,
        clock_is_trusted: bool,
        evidence_provider: UsageEvidenceProvider | None,
    ) -> None:
        self._clock = clock
        self._clock_is_trusted = clock_is_trusted
        self._evidence_provider = evidence_provider
        self._usage = _unverified_usage()

    def invoke(
        self,
        connector: object,
        candidate: DatasetCandidate,
        target: Path,
        request_id: str,
    ) -> object:
        """Invoke a connector and finalize independent evidence on every exit."""
        start, start_error = _read_clock(self._clock)
        if start_error is not None:
            raise start_error
        connector_failed = False
        try:
            legacy = cast(_LegacyConnector, connector)
            return legacy.acquire(candidate, target)
        except BaseException:
            connector_failed = True
            raise
        finally:
            end, end_error = _read_clock(self._clock)
            elapsed = _elapsed_seconds(start, end)
            evidence, evidence_error = _read_evidence(
                self._evidence_provider, connector, candidate, request_id
            )
            evidence_state: VerificationState = (
                "trusted_evidence" if evidence is not None else "unverified"
            )
            elapsed_is_verified = self._clock_is_trusted and elapsed is not None
            self._usage = VerifiedUsage(
                api_calls=evidence.api_calls if evidence is not None else None,
                external_cost=evidence.external_cost if evidence is not None else None,
                elapsed_seconds=elapsed if elapsed_is_verified else None,
                verification=UsageVerification(
                    bytes="unverified",
                    local_storage_bytes="unverified",
                    api_calls=evidence_state,
                    external_cost=evidence_state,
                    elapsed_seconds=(
                        "gateway_measured" if elapsed_is_verified else "unverified"
                    ),
                ),
            )
            finalization_error = end_error or evidence_error
            if finalization_error is not None and not connector_failed:
                raise finalization_error

    @property
    def usage(self) -> VerifiedUsage:
        """Return the completed measurement, including after an exception."""
        return self._usage


def actual_usage_reasons(
    budget: AcquisitionBudget, usage: VerifiedUsage
) -> tuple[str, ...]:
    """Fail closed for unverified or over-budget actual dimensions."""
    reasons: list[str] = []
    dimensions = (
        ("download bytes", usage.bytes, budget.max_download_bytes, "exceed"),
        (
            "local storage bytes",
            usage.local_storage_bytes,
            budget.max_local_storage_bytes,
            "exceed",
        ),
        ("api calls", usage.api_calls, budget.max_api_calls, "exceed"),
        ("external cost", usage.external_cost, budget.max_external_cost, "exceeds"),
        (
            "elapsed seconds",
            usage.elapsed_seconds,
            budget.max_elapsed_seconds,
            "exceed",
        ),
    )
    for label, value, limit, exceed_verb in dimensions:
        if value is None:
            verb = "are" if label.endswith("s") else "is"
            reasons.append(f"actual {label} {verb} unverified")
        elif value > limit:
            reasons.append(f"actual {label} {exceed_verb} budget")
    return tuple(reasons)


def _read_clock(clock: Clock) -> tuple[float | None, BaseException | None]:
    try:
        value = clock()
    except BaseException as error:  # noqa: BLE001 - finalize before re-raising
        return None, error
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, None
    converted = float(value)
    return (converted if math.isfinite(converted) else None), None


def _read_evidence(
    provider: UsageEvidenceProvider | None,
    connector: object,
    candidate: DatasetCandidate,
    request_id: str,
) -> tuple[TrustedUsageEvidence | None, BaseException | None]:
    if provider is None:
        return None, None
    try:
        evidence = provider(connector, candidate, request_id)
        if not isinstance(evidence, TrustedUsageEvidence):
            return None, None
        validated = TrustedUsageEvidence.model_validate(evidence.model_dump(mode="python"))
        return validated, None
    except BaseException as error:  # noqa: BLE001 - finalize before re-raising
        return None, error


def _elapsed_seconds(start: float | None, end: float | None) -> int | None:
    if start is None or end is None or end < start:
        return None
    return math.ceil(end - start)


def _unverified_usage() -> VerifiedUsage:
    return VerifiedUsage(
        verification=UsageVerification(
            bytes="unverified",
            local_storage_bytes="unverified",
            api_calls="unverified",
            external_cost="unverified",
            elapsed_seconds="unverified",
        )
    )
