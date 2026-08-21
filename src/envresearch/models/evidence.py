"""Strict evidence and feasibility contracts for research acquisition planning."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
)

from envresearch.models.literature import (
    AttachmentMetadata,
    EvidenceRow,
    LiteratureMapPayload,
    SourceRecord,
)

__all__ = [
    "AcquisitionBudget",
    "AcquisitionBudgetExceeded",
    "AcquisitionDecision",
    "AcquisitionPolicy",
    "AttachmentMetadata",
    "DataFeasibilityPayload",
    "DataProvenancePayload",
    "DatasetCandidate",
    "EvidenceRow",
    "LiteratureMapPayload",
    "SourceRecord",
]

if TYPE_CHECKING:
    from envresearch.connectors.contracts import ConnectorReceipt


class _StrictModel(BaseModel):
    """Base contract that rejects extra fields and primitive coercion."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _require_nonblank(value: str) -> str:
    """Keep persisted evidence fields meaningful and reviewable."""
    if not value.strip():
        raise ValueError("must not be blank")
    return value


def _require_finite_nonnegative_decimal(value: Decimal) -> Decimal:
    """Reject costs that cannot represent a bounded real-world measurement."""
    if not value.is_finite() or value < 0:
        raise ValueError("must be finite and nonnegative")
    return value


class DatasetCandidate(_StrictModel):
    """A data option with access, licensing, suitability, and budget estimates."""

    dataset_id: str
    source: str
    public_access: StrictBool
    requires_credentials: StrictBool
    clear_license: StrictBool
    license: str
    estimated_download_bytes: StrictInt = Field(ge=0)
    estimated_local_storage_bytes: StrictInt = Field(ge=0)
    estimated_api_calls: StrictInt = Field(ge=0)
    estimated_external_cost: Decimal
    estimated_elapsed_seconds: StrictInt = Field(ge=0)
    suitable_for_design: StrictBool
    suitability_reason: str
    access_reason: str
    data_structures: tuple[str, ...] = ()
    available_features: tuple[str, ...] = ()

    @field_validator(
        "dataset_id", "source", "license", "suitability_reason", "access_reason"
    )
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        """Keep every acquisition decision tied to explicit evidence."""
        return _require_nonblank(value)

    @field_validator("data_structures", "available_features")
    @classmethod
    def require_unique_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Keep machine-checked design capabilities canonical and unambiguous."""
        if any(not item.strip() or item != item.strip() for item in value):
            raise ValueError("data capabilities must contain canonical nonblank values")
        if len(value) != len(set(value)):
            raise ValueError("data capabilities must not contain duplicates")
        return value

    @field_validator("estimated_external_cost")
    @classmethod
    def require_valid_external_cost(cls, value: Decimal) -> Decimal:
        """Require a finite Decimal cost estimate with no implicit coercion."""
        return _require_finite_nonnegative_decimal(value)


class DataFeasibilityPayload(_StrictModel):
    """Candidate datasets and the evidence-backed feasibility conclusion."""

    research_design: str
    candidates: tuple[DatasetCandidate, ...]
    recommendation: str
    evidence_reason: str

    @field_validator("research_design", "recommendation", "evidence_reason")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        """Require a reviewable data-feasibility conclusion."""
        return _require_nonblank(value)


class DataProvenancePayload(_StrictModel):
    """Provenance for one receipt that passed acquisition policy checks."""

    dataset_id: str
    connector_id: str
    connector_version: str
    source: str
    acquired_at: datetime
    license: str
    bytes: StrictInt = Field(ge=0)
    local_storage_bytes: StrictInt = Field(ge=0)
    api_calls: StrictInt = Field(ge=0)
    external_cost: Decimal
    elapsed_seconds: StrictInt = Field(ge=0)
    sha256: str

    @field_validator(
        "dataset_id", "connector_id", "connector_version", "source", "license", "sha256"
    )
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        """Keep persisted provenance attributable to one verified acquisition."""
        return _require_nonblank(value)

    @field_validator("acquired_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """Reject ambiguous provenance timestamps."""
        if value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
            raise ValueError("timestamps must be UTC-aware")
        return value

    @field_validator("external_cost")
    @classmethod
    def require_valid_external_cost(cls, value: Decimal) -> Decimal:
        """Keep provenance cost measurements finite and nonnegative."""
        return _require_finite_nonnegative_decimal(value)

    @classmethod
    def from_receipt(
        cls, dataset_id: str, receipt: ConnectorReceipt
    ) -> DataProvenancePayload:
        """Promote only an in-budget, non-quarantined connector receipt."""
        if receipt.quarantined:
            raise ValueError(
                "quarantined receipt cannot be promoted to data provenance"
            )
        return cls(
            dataset_id=dataset_id,
            connector_id=receipt.connector_id,
            connector_version=receipt.connector_version,
            source=receipt.source,
            acquired_at=receipt.acquired_at,
            license=receipt.license,
            bytes=receipt.bytes,
            local_storage_bytes=receipt.local_storage_bytes,
            api_calls=receipt.api_calls,
            external_cost=receipt.external_cost,
            elapsed_seconds=receipt.elapsed_seconds,
            sha256=receipt.sha256,
        )


class _ActualUsage(Protocol):
    """Minimal receipt-shaped measurements used by post-acquisition checks."""

    bytes: int
    local_storage_bytes: int
    api_calls: int
    external_cost: Decimal
    elapsed_seconds: int


class AcquisitionBudgetExceeded(ValueError):
    """Raised when one declared or actual budget limit is exceeded."""

    def __init__(self, reasons: tuple[str, ...]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class AcquisitionBudget(_StrictModel):
    """Finite upper limits applied before and after connector acquisition."""

    max_download_bytes: StrictInt = Field(ge=0)
    max_local_storage_bytes: StrictInt = Field(ge=0)
    max_api_calls: StrictInt = Field(ge=0)
    max_external_cost: Decimal
    max_elapsed_seconds: StrictInt = Field(ge=0)

    @field_validator("max_external_cost")
    @classmethod
    def require_valid_external_cost(cls, value: Decimal) -> Decimal:
        """Require a finite, nonnegative Decimal budget limit."""
        return _require_finite_nonnegative_decimal(value)

    def estimate_reasons(self, candidate: DatasetCandidate) -> tuple[str, ...]:
        """Return all pre-acquisition estimates that exceed this budget."""
        return self._reasons(
            candidate.estimated_download_bytes,
            candidate.estimated_local_storage_bytes,
            candidate.estimated_api_calls,
            candidate.estimated_external_cost,
            candidate.estimated_elapsed_seconds,
            prefix="estimated",
        )

    def verify_estimate(self, candidate: DatasetCandidate) -> None:
        """Raise with all exceeded limits before a connector is invoked."""
        reasons = self.estimate_reasons(candidate)
        if reasons:
            raise AcquisitionBudgetExceeded(reasons)

    def actual_reasons(self, receipt: _ActualUsage) -> tuple[str, ...]:
        """Return all measured connector usage that exceeds this budget."""
        return self._reasons(
            receipt.bytes,
            receipt.local_storage_bytes,
            receipt.api_calls,
            receipt.external_cost,
            receipt.elapsed_seconds,
            prefix="actual",
        )

    def verify_actual(self, receipt: _ActualUsage) -> None:
        """Raise with all exceeded limits after a connector is invoked."""
        reasons = self.actual_reasons(receipt)
        if reasons:
            raise AcquisitionBudgetExceeded(reasons)

    def _reasons(
        self,
        download_bytes: int,
        local_storage_bytes: int,
        api_calls: int,
        external_cost: Decimal,
        elapsed_seconds: int,
        *,
        prefix: str,
    ) -> tuple[str, ...]:
        """Compare one set of measurements to every declared limit."""
        reasons: list[str] = []
        if download_bytes > self.max_download_bytes:
            reasons.append(f"{prefix} download bytes exceed budget")
        if local_storage_bytes > self.max_local_storage_bytes:
            reasons.append(f"{prefix} local storage bytes exceed budget")
        if api_calls > self.max_api_calls:
            reasons.append(f"{prefix} api calls exceed budget")
        if external_cost > self.max_external_cost:
            reasons.append(f"{prefix} external cost exceeds budget")
        if elapsed_seconds > self.max_elapsed_seconds:
            reasons.append(f"{prefix} elapsed seconds exceed budget")
        return tuple(reasons)


class AcquisitionDecision(_StrictModel):
    """One transparent acquisition-policy outcome and its evidence reasons."""

    action: Literal["auto_acquire", "gate_required", "planning_only"]
    reasons: tuple[str, ...] = Field(min_length=1)

    @field_validator("reasons")
    @classmethod
    def require_nonblank_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Ensure a decision can be reviewed without implicit policy logic."""
        if any(not reason.strip() for reason in value):
            raise ValueError("reasons must not contain blank values")
        return value


class AcquisitionPolicy:
    """Classify candidates as automatic, conditional, or planning-only."""

    def evaluate(
        self, candidate: DatasetCandidate, budget: AcquisitionBudget
    ) -> AcquisitionDecision:
        """Apply the approved progressive acquisition decision table."""
        if not candidate.suitable_for_design:
            return AcquisitionDecision(
                action="planning_only",
                reasons=("dataset is unsuitable for the approved research design",),
            )

        reasons: list[str] = []
        if not candidate.public_access:
            reasons.append("source is not public")
        if candidate.requires_credentials:
            reasons.append("source requires credentials")
        if not candidate.clear_license:
            reasons.append("license is not clear")
        reasons.extend(budget.estimate_reasons(candidate))
        if reasons:
            return AcquisitionDecision(action="gate_required", reasons=tuple(reasons))
        return AcquisitionDecision(
            action="auto_acquire",
            reasons=(
                "public, clearly licensed, suitable, and within estimated budget",
            ),
        )
