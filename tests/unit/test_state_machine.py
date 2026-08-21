"""Tests for the exact legal workflow lifecycle transitions."""

from collections.abc import Iterator

import pytest

from envresearch.kernel.state import WorkflowStateMachine
from envresearch.models.enums import WorkflowStatus

EXPECTED_TRANSITIONS: dict[WorkflowStatus, set[WorkflowStatus]] = {
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


def allowed_edges() -> Iterator[tuple[WorkflowStatus, WorkflowStatus]]:
    """Yield each independent edge in the lifecycle specification."""
    for source, targets in EXPECTED_TRANSITIONS.items():
        for target in targets:
            yield source, target


def forbidden_edge(source: WorkflowStatus) -> tuple[WorkflowStatus, WorkflowStatus]:
    """Choose a target outside the source's independently specified edges."""
    target = next(
        status for status in WorkflowStatus if status not in EXPECTED_TRANSITIONS[source]
    )
    return source, target


@pytest.mark.parametrize(("current", "target"), list(allowed_edges()))
def test_state_machine_accepts_every_specified_edge(
    current: WorkflowStatus, target: WorkflowStatus
) -> None:
    """Every edge in the lifecycle specification remains executable."""
    assert WorkflowStateMachine().transition(current, target) is target


@pytest.mark.parametrize(("current", "target"), [forbidden_edge(status) for status in WorkflowStatus])
def test_state_machine_rejects_an_unspecified_edge_from_every_state(
    current: WorkflowStatus, target: WorkflowStatus
) -> None:
    """Every source state rejects a status not listed in its allowed edges."""
    with pytest.raises(ValueError, match="invalid transition"):
        WorkflowStateMachine().transition(current, target)
