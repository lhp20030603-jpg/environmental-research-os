"""Enumerations shared by workflow artifact contracts."""

from enum import StrEnum


class ArtifactLifecycle(StrEnum):
    """Lifecycle states for a versioned research artifact."""

    PRODUCED = "produced"
    VALIDATED = "validated"
    APPROVED = "approved"
    BLOCKED = "blocked"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class WorkflowStatus(StrEnum):
    """Lifecycle states for a workflow run."""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    REPAIR_PENDING = "repair_pending"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class FindingSeverity(StrEnum):
    """Severity levels assigned to workflow findings."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class GateStatus(StrEnum):
    """Decision states for a human approval gate."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
