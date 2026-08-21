"""Workflow lifecycle transition rules."""

from envresearch.models.enums import WorkflowStatus

ALLOWED_TRANSITIONS: dict[WorkflowStatus, set[WorkflowStatus]] = {
    WorkflowStatus.PENDING: {WorkflowStatus.RUNNING, WorkflowStatus.REJECTED},
    WorkflowStatus.RUNNING: {
        WorkflowStatus.PASSED,
        WorkflowStatus.FAILED,
        WorkflowStatus.REVIEW_REQUIRED,
    },
    WorkflowStatus.FAILED: {
        WorkflowStatus.REPAIR_PENDING,
        WorkflowStatus.REVIEW_REQUIRED,
        WorkflowStatus.REJECTED,
    },
    WorkflowStatus.REPAIR_PENDING: {WorkflowStatus.RUNNING, WorkflowStatus.REJECTED},
    WorkflowStatus.REVIEW_REQUIRED: {
        WorkflowStatus.APPROVED,
        WorkflowStatus.REJECTED,
    },
    WorkflowStatus.APPROVED: {WorkflowStatus.RUNNING, WorkflowStatus.SUPERSEDED},
    WorkflowStatus.PASSED: {WorkflowStatus.SUPERSEDED},
    WorkflowStatus.REJECTED: {WorkflowStatus.SUPERSEDED},
    WorkflowStatus.SUPERSEDED: set(),
}


class WorkflowStateMachine:
    """Validate and apply the fixed workflow lifecycle graph."""

    def transition(
        self, current: WorkflowStatus, target: WorkflowStatus
    ) -> WorkflowStatus:
        """Return a legal target status or reject an illegal transition."""
        if target not in ALLOWED_TRANSITIONS[current]:
            raise ValueError(f"invalid transition: {current} -> {target}")
        return target
