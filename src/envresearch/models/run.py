"""Run manifest and report artifact contracts."""

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field, field_validator

from envresearch import __version__
from envresearch.models.enums import WorkflowStatus
from envresearch.models.finding import Finding


def utc_now() -> datetime:
    """Return the current UTC time for a new persisted artifact."""
    return datetime.now(UTC)


class RunManifest(BaseModel):
    """Inputs and version metadata for one workflow run."""

    run_id: str
    benchmark_id: str
    kernel_version: str = __version__
    schema_version: str = "1.0"
    created_at: datetime = Field(default_factory=utc_now)
    status: WorkflowStatus = WorkflowStatus.PENDING
    versions: dict[str, str] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Reject ambiguous timestamps in persisted run manifests."""
        if value.utcoffset() != timedelta(0):
            raise ValueError("timestamps must be UTC-aware")
        return value


class RunReport(BaseModel):
    """Record of a completed or interrupted workflow run."""

    run_id: str
    benchmark_id: str
    status: WorkflowStatus
    started_at: datetime
    finished_at: datetime | None = None
    findings: list[Finding] = Field(default_factory=list)
    completed_tasks: list[str] = Field(default_factory=list)
    output_comparisons: list[dict[str, object]] = Field(default_factory=list)

    @field_validator("started_at", "finished_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        """Reject ambiguous timestamps in persisted run reports."""
        if value is not None and value.utcoffset() != timedelta(0):
            raise ValueError("timestamps must be UTC-aware")
        return value
