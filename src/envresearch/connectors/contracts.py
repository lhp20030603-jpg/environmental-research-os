"""I/O-free connector interfaces and immutable acquisition receipts."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol, TypeAlias, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from envresearch.connectors.usage_meter import VerifiedUsage
from envresearch.models.evidence import DatasetCandidate, SourceRecord

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DOI_QUERY_PREFIX = re.compile(
    r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE
)
_CANONICAL_DOI = re.compile(r"^10\.\d{1,9}/[^\s\x00-\x1f\x7f]+$", re.IGNORECASE)
DEGRADABLE_CONNECTOR_UNAVAILABLE_REASON_CODES = frozenset(
    {"EXPORT_MISSING", "EXPORT_UNREADABLE", "EXPORT_MALFORMED"}
)
DataRisk: TypeAlias = Literal["public", "sensitive", "private"]


def _require_nonblank(value: str) -> str:
    """Reject receipt identity fields that cannot be audited."""
    if not value.strip():
        raise ValueError("must not be blank")
    return value


def _require_finite_nonnegative_cost(value: Decimal) -> Decimal:
    """Reject an unsafe external-cost measurement."""
    if not value.is_finite() or value < 0:
        raise ValueError("must be finite and nonnegative")
    return value


class ConnectorReceipt(BaseModel):
    """Measured, hash-bound result of one connector acquisition attempt."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )

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
    quarantined: StrictBool = False
    quarantine_reasons: tuple[str, ...] = ()

    @field_validator("connector_id", "connector_version", "source", "license")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        """Keep every receipt attributable to one connector and source."""
        return _require_nonblank(value)

    @field_validator("acquired_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """Reject ambiguous acquisition timestamps."""
        if value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
            raise ValueError("timestamps must be UTC-aware")
        return value

    @field_validator("external_cost")
    @classmethod
    def require_valid_external_cost(cls, value: Decimal) -> Decimal:
        """Keep actual connector costs finite and nonnegative."""
        return _require_finite_nonnegative_cost(value)

    @field_validator("sha256")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        """Require a canonical hash before data may enter provenance."""
        if not _SHA256.fullmatch(value):
            raise ValueError("sha256 must be a 64-character lowercase SHA-256")
        return value

    @field_validator("quarantine_reasons")
    @classmethod
    def require_nonblank_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Keep quarantine decisions reviewable and actionable."""
        if any(not reason.strip() for reason in value):
            raise ValueError("quarantine_reasons must not contain blank values")
        return value

    @model_validator(mode="after")
    def require_consistent_quarantine_state(self) -> ConnectorReceipt:
        """Require reasons exactly when a receipt has been quarantined."""
        if self.quarantined != bool(self.quarantine_reasons):
            raise ValueError("quarantine state must match quarantine reasons")
        return self

    def quarantine(self, reasons: tuple[str, ...]) -> ConnectorReceipt:
        """Return this receipt marked ineligible for provenance promotion."""
        if not reasons:
            raise ValueError("quarantine requires at least one reason")
        return type(self).model_validate(
            self.model_dump(mode="python")
            | {"quarantined": True, "quarantine_reasons": reasons}
        )


class MeasuredAcquisition(BaseModel):
    """Measurements from one descriptor-pinned regular output inode."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    relative_target: str
    bytes: StrictInt = Field(ge=0)
    local_storage_bytes: StrictInt = Field(ge=0)
    sha256: str
    device: StrictInt = Field(ge=0)
    inode: StrictInt = Field(ge=0)

    @field_validator("relative_target")
    @classmethod
    def require_target(cls, value: str) -> str:
        """Keep the measurement attributable to one confined target."""
        return _require_nonblank(value)

    @field_validator("sha256")
    @classmethod
    def require_measured_sha256(cls, value: str) -> str:
        """Require a canonical content identity for the measured inode."""
        if not _SHA256.fullmatch(value):
            raise ValueError("sha256 must be a 64-character lowercase SHA-256")
        return value


class AcquisitionAuditRecord(BaseModel):
    """Durable accepted or fail-closed result for one acquisition attempt."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )

    request_id: str
    request_fingerprint: str
    connector_id: str
    connector_version: str
    source: str
    license: str
    data_risk: DataRisk
    recorded_at: datetime
    status: Literal["accepted", "quarantined"]
    target: str
    reasons: tuple[str, ...] = ()
    claimed_receipt: ConnectorReceipt | None = None
    verified_receipt: ConnectorReceipt | None = None
    measured: MeasuredAcquisition | None = None
    verified_usage: VerifiedUsage | None = None

    @field_validator(
        "request_id",
        "request_fingerprint",
        "connector_id",
        "connector_version",
        "source",
        "license",
        "target",
    )
    @classmethod
    def require_audit_text(cls, value: str) -> str:
        """Reject audit records with ambiguous identity fields."""
        return _require_nonblank(value)

    @field_validator("recorded_at")
    @classmethod
    def require_audit_utc(cls, value: datetime) -> datetime:
        """Require a portable UTC audit timestamp."""
        if value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
            raise ValueError("timestamps must be UTC-aware")
        return value

    @field_validator("reasons")
    @classmethod
    def require_audit_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Keep every quarantine reason actionable."""
        if any(not reason.strip() for reason in value):
            raise ValueError("reasons must not contain blank values")
        return value

    @model_validator(mode="after")
    def require_auditable_status(self) -> AcquisitionAuditRecord:
        """Bind accepted state to verified measurements and failures to reasons."""
        if self.status == "accepted":
            receipt = self.verified_receipt
            measured = self.measured
            usage = self.verified_usage
            if (
                self.reasons
                or receipt is None
                or measured is None
                or usage is None
                or not usage.fully_verified
            ):
                raise ValueError(
                    "accepted acquisition requires verified measurement only"
                )
            if receipt.quarantined:
                raise ValueError("accepted acquisition receipt cannot be quarantined")
            if not usage.matches_receipt_values(
                bytes_count=receipt.bytes,
                local_storage_bytes=receipt.local_storage_bytes,
                api_calls=receipt.api_calls,
                external_cost=receipt.external_cost,
                elapsed_seconds=receipt.elapsed_seconds,
            ):
                raise ValueError("verified usage must match the accepted receipt")
            if (
                measured.bytes != receipt.bytes
                or measured.local_storage_bytes != receipt.local_storage_bytes
                or measured.sha256 != receipt.sha256
            ):
                raise ValueError("file measurement must match the accepted receipt")
        elif not self.reasons:
            raise ValueError("quarantined acquisition requires reasons")
        return self


class LiteratureQuery(BaseModel):
    """A strict, connector-neutral request for public literature metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    text: str | None = None
    title: str | None = None
    author: str | None = None
    tag: str | None = None
    collection: str | None = None
    doi: str | None = None
    year: StrictInt | None = Field(default=None, ge=1)

    @field_validator("text", "title", "author", "tag", "collection")
    @classmethod
    def require_nonblank_optional_text(cls, value: str | None) -> str | None:
        """Reject whitespace-only query fields at the public boundary."""
        if value is not None:
            return _require_nonblank(value)
        return value

    @field_validator("doi")
    @classmethod
    def require_canonical_doi(cls, value: str | None) -> str | None:
        """Canonicalize one DOI filter or reject a value that could match absence."""
        if value is None:
            return None
        normalized = _DOI_QUERY_PREFIX.sub("", _require_nonblank(value).strip())
        normalized = normalized.strip().casefold()
        if not _CANONICAL_DOI.fullmatch(normalized):
            raise ValueError("doi must be a valid DOI")
        return normalized

    @model_validator(mode="after")
    def require_search_term(self) -> LiteratureQuery:
        """Prevent an accidental unbounded scan from an empty query object."""
        if all(
            value is None
            for value in (
                self.text,
                self.title,
                self.author,
                self.tag,
                self.collection,
                self.doi,
                self.year,
            )
        ):
            raise ValueError("at least one literature query field is required")
        return self


class ConnectorUnavailable(RuntimeError):
    """Known connector boundary failure that may safely degrade coverage."""

    def __init__(self, *, connector_id: str, reason_code: str, diagnostic: str) -> None:
        for name, value in (
            ("connector_id", connector_id),
            ("reason_code", reason_code),
            ("diagnostic", diagnostic),
        ):
            if not isinstance(value, str) or not value.strip():
                raise TypeError(f"{name} must be a nonblank string")
        super().__init__(diagnostic)
        self.connector_id = connector_id
        self.reason_code = reason_code
        self.diagnostic = diagnostic

    def model_dump(self) -> dict[str, str]:
        """Return a deliberate, serializable public outage diagnostic."""
        return {
            "connector_id": self.connector_id,
            "reason_code": self.reason_code,
            "diagnostic": self.diagnostic,
        }

    @property
    def is_degradable(self) -> bool:
        """Return whether this is one declared availability or format failure."""
        return self.reason_code in DEGRADABLE_CONNECTOR_UNAVAILABLE_REASON_CODES


class ConnectorCoverage(BaseModel):
    """Serializable literature coverage outcome, including safe degradation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    connector_id: str
    connector_version: str
    status: Literal["complete", "degraded"]
    records: tuple[SourceRecord, ...]
    reason_code: str | None = None
    connector_reason_code: str | None = None
    diagnostic: str | None = None

    @field_validator("connector_id", "connector_version")
    @classmethod
    def require_nonblank_identity(cls, value: str) -> str:
        """Keep coverage attributable to the connector actually queried."""
        return _require_nonblank(value)

    @field_validator("reason_code", "connector_reason_code", "diagnostic")
    @classmethod
    def require_nonblank_optional_text(cls, value: str | None) -> str | None:
        """Do not serialize ambiguous blank coverage diagnostics."""
        if value is not None:
            return _require_nonblank(value)
        return value

    @model_validator(mode="after")
    def require_consistent_status(self) -> ConnectorCoverage:
        """Make complete and degraded coverage states unambiguous to callers."""
        details = (self.reason_code, self.connector_reason_code, self.diagnostic)
        if self.status == "complete" and any(value is not None for value in details):
            raise ValueError("complete coverage must not include a failure reason")
        if self.status == "degraded":
            if self.records:
                raise ValueError("degraded coverage must not include records")
            if self.reason_code != "CONNECTOR_UNAVAILABLE":
                raise ValueError(
                    "degraded coverage reason_code must be CONNECTOR_UNAVAILABLE"
                )
            if self.connector_reason_code is None or self.diagnostic is None:
                raise ValueError("degraded coverage requires connector failure details")
        return self


@runtime_checkable
class LiteratureConnector(Protocol):
    """A structurally testable interface for literature discovery only."""

    connector_id: str
    connector_version: str

    def search(self, query: LiteratureQuery) -> tuple[SourceRecord, ...]:
        """Return source metadata without prescribing a transport."""


@runtime_checkable
class DataConnector(Protocol):
    """A structurally testable interface for data inspection and acquisition."""

    connector_id: str
    connector_version: str

    def inspect(self, source: str) -> DatasetCandidate:
        """Inspect one source without defining a real I/O implementation."""

    def acquire(self, candidate: DatasetCandidate, target: Path) -> ConnectorReceipt:
        """Acquire one already-approved candidate into the supplied target."""
