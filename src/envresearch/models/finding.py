"""Immutable finding artifacts and their independent resolution rule."""

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, field_validator

from envresearch.models.enums import FindingSeverity


class Finding(BaseModel):
    """An auditable issue produced during a workflow run."""

    model_config = ConfigDict(frozen=True)

    id: str
    code: str
    severity: FindingSeverity
    message: str
    producer: str
    evidence: tuple[str, ...]
    resolved_by: str | None = None
    resolved_at: datetime | None = None

    @field_validator("resolved_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        """Reject ambiguous timestamps in persisted audit artifacts."""
        if value is not None and value.utcoffset() != timedelta(0):
            raise ValueError("timestamps must be UTC-aware")
        return value

    def resolve(self, resolver: str) -> "Finding":
        """Return a resolved copy, enforcing independent critical resolution."""
        if self.severity is FindingSeverity.CRITICAL and resolver == self.producer:
            raise ValueError("critical finding requires an independent resolver")
        return self.model_copy(
            update={
                "resolved_by": resolver,
                "resolved_at": datetime.now(UTC),
            }
        )
