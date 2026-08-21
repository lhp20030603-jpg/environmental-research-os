"""Tests for durable, independently decided human approval gates."""

import json
import multiprocessing
import os
from datetime import UTC, datetime, timedelta, timezone
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Event
from pathlib import Path

import pytest
from pydantic import ValidationError

from envresearch.kernel.events import EventLog
from envresearch.kernel.gates import GateDecision, GateRequest, GateStore, _GateLock
from envresearch.models.enums import GateStatus, WorkflowStatus
from envresearch.storage.artifacts import ArtifactStore


def gate_store(tmp_path: Path) -> GateStore:
    """Create a real durable gate store and its event log."""
    return GateStore(ArtifactStore(tmp_path), EventLog(tmp_path / "events.jsonl"))


def decide_concurrently(
    root: str,
    status: str,
    start: Event,
    outcomes: Queue,
) -> None:
    """Attempt one conflicting decision after both processes are ready."""
    gates = GateStore(ArtifactStore(Path(root)), EventLog(Path(root) / "events.jsonl"))
    start.wait()
    try:
        gates.decide(
            "gate-design",
            GateDecision(
                status=GateStatus(status),
                decided_by=f"reviewer-{status}",
                rationale=f"{status} decision",
            ),
        )
    except ValueError:
        outcomes.put("rejected")
    else:
        outcomes.put(status)


def request_concurrently(
    root: str,
    requested_by: str,
    start: Event,
    outcomes: Queue,
) -> None:
    """Attempt one duplicate request after both processes are ready."""
    gates = GateStore(ArtifactStore(Path(root)), EventLog(Path(root) / "events.jsonl"))
    start.wait()
    try:
        gates.request(
            GateRequest(
                id="gate-design", name="Research design", requested_by=requested_by
            )
        )
    except ValueError:
        outcomes.put("duplicate")
    else:
        outcomes.put("requested")


def crash_while_holding_gate_lock(root: str, ready: Event) -> None:
    """Acquire the cross-process mutex, notify the parent, then terminate."""
    lock_path = Path(root) / "gates" / ".locks" / "gate-design.filelock"
    with _GateLock(lock_path):
        ready.set()
        os._exit(0)


def test_pending_gate_blocks_execution(tmp_path: Path) -> None:
    """A pending request must prevent a gated workflow step from running."""
    gates = gate_store(tmp_path)
    gates.request(GateRequest(id="gate-design", name="Research design", requested_by="agent-a"))

    with pytest.raises(PermissionError, match="gate-design is not approved"):
        gates.require_approved("gate-design")


def test_requester_cannot_approve_their_own_gate(tmp_path: Path) -> None:
    """Approval would be non-independent if it came from the requester."""
    gates = gate_store(tmp_path)
    gates.request(GateRequest(id="gate-design", name="Research design", requested_by="agent-a"))

    with pytest.raises(ValueError, match="gate requires an independent decision maker"):
        gates.decide(
            "gate-design",
            GateDecision(
                status=GateStatus.APPROVED,
                decided_by="agent-a",
                rationale="Looks sound.",
            ),
        )


@pytest.mark.parametrize("decided_by", ["agent-a ", "AGENT-A", "   "])
def test_equivalent_or_blank_decision_maker_cannot_approve_gate(
    tmp_path: Path, decided_by: str
) -> None:
    """Identity spelling differences must not bypass independent approval."""
    gates = gate_store(tmp_path)
    gates.request(GateRequest(id="gate-design", name="Research design", requested_by="agent-a"))

    with pytest.raises(ValueError):
        gates.decide(
            "gate-design",
            GateDecision(
                status=GateStatus.APPROVED,
                decided_by=decided_by,
                rationale="Looks sound.",
            ),
        )


def test_rejected_gate_cannot_be_changed_to_approved(tmp_path: Path) -> None:
    """A rejection is terminal, so approval requires a distinct new request."""
    gates = gate_store(tmp_path)
    gates.request(GateRequest(id="gate-design", name="Research design", requested_by="agent-a"))
    gates.decide(
        "gate-design",
        GateDecision(
            status=GateStatus.REJECTED,
            decided_by="reviewer-b",
            rationale="Specify the sampling frame.",
        ),
    )

    with pytest.raises(ValueError, match="rejected gate requires a new GateRequest"):
        gates.decide(
            "gate-design",
            GateDecision(
                status=GateStatus.APPROVED,
                decided_by="reviewer-c",
                rationale="Now acceptable.",
            ),
        )


def test_decision_persists_and_records_exact_gate_events(tmp_path: Path) -> None:
    """The durable request and audit history must agree on an approval decision."""
    gates = gate_store(tmp_path)
    requested_at = datetime(2026, 8, 4, 9, 0, tzinfo=UTC)
    decided_at = datetime(2026, 8, 4, 9, 5, tzinfo=UTC)
    gates.request(
        GateRequest(
            id="gate-design",
            name="Research design",
            requested_by="agent-a",
            requested_at=requested_at,
        )
    )

    decided = gates.decide(
        "gate-design",
        GateDecision(
            status=GateStatus.APPROVED,
            decided_by="reviewer-b",
            rationale="Design is ready.",
            decided_at=decided_at,
        ),
    )

    assert decided.status is GateStatus.APPROVED
    assert decided.decision is not None
    assert decided.decision.decided_at == decided_at
    assert decided.decision.conditions == {}
    assert ArtifactStore(tmp_path).read_json(Path("gates/gate-design.json")) == {
        "decision": {
            "decided_at": "2026-08-04T09:05:00Z",
            "decided_by": "reviewer-b",
            "rationale": "Design is ready.",
            "status": "approved",
        },
        "id": "gate-design",
        "name": "Research design",
        "requested_at": "2026-08-04T09:00:00Z",
        "requested_by": "agent-a",
        "status": "approved",
    }
    assert [event.event_type for event in EventLog(tmp_path / "events.jsonl").read_all()] == [
        "gate.requested",
        "gate.approved",
    ]
    assert [event.from_status for event in EventLog(tmp_path / "events.jsonl").read_all()] == [
        WorkflowStatus.PENDING,
        WorkflowStatus.PENDING,
    ]
    assert [event.to_status for event in EventLog(tmp_path / "events.jsonl").read_all()] == [
        WorkflowStatus.PENDING,
        WorkflowStatus.APPROVED,
    ]


def test_gate_decision_preserves_selected_candidate(tmp_path: Path) -> None:
    """Gate decisions retain the selected charter for downstream work."""
    gates = gate_store(tmp_path)
    gates.request(
        GateRequest(id="gate-1", name="Charter selection", requested_by="orchestrator")
    )

    decided = gates.decide(
        "gate-1",
        GateDecision(
            status=GateStatus.APPROVED,
            decided_by="human-owner",
            rationale="Best feasible contribution.",
            conditions={"selected_candidate_id": "charter-air"},
        ),
    )

    assert decided.decision is not None
    assert decided.decision.conditions == {"selected_candidate_id": "charter-air"}
    assert ArtifactStore(tmp_path).read_json(Path("gates/gate-1.json"))["decision"][
        "conditions"
    ] == {"selected_candidate_id": "charter-air"}


def test_gate_conditions_are_isolated_from_caller_mutation(tmp_path: Path) -> None:
    """Persisted gate metadata cannot be changed through a caller-owned dict."""
    conditions = {"residual_risks": ["coverage gap"]}
    gates = gate_store(tmp_path)
    gates.request(
        GateRequest(id="gate-final", name="Final review", requested_by="agent-a")
    )
    decision = GateDecision(
        status=GateStatus.APPROVED,
        decided_by="reviewer-b",
        rationale="Accept residual risks.",
        conditions=conditions,
    )
    conditions["residual_risks"].append("measurement error")

    decided = gates.decide("gate-final", decision)

    assert decided.decision is not None
    assert decided.decision.conditions == {"residual_risks": ["coverage gap"]}


@pytest.mark.parametrize(
    ("request_update", "decision_update"),
    [
        ({"requested_by": "   "}, None),
        (None, {"decided_by": "agent-a"}),
        (None, {"decided_by": "   "}),
    ],
)
def test_gate_store_revalidates_copied_actor_models(
    tmp_path: Path,
    request_update: dict[str, object] | None,
    decision_update: dict[str, object] | None,
) -> None:
    """Copied models cannot bypass nonblank or independent-human gate checks."""
    gates = gate_store(tmp_path)
    request = GateRequest(
        id="gate-design", name="Research design", requested_by="agent-a"
    )
    if request_update is not None:
        with pytest.raises(ValidationError):
            gates.request(request.model_copy(update=request_update))
        return
    gates.request(request)
    decision = GateDecision(
        status=GateStatus.APPROVED,
        decided_by="reviewer-b",
        rationale="Looks sound.",
    )

    with pytest.raises((ValidationError, ValueError)):
        gates.decide("gate-design", decision.model_copy(update=decision_update))


@pytest.mark.parametrize(
    "conditions",
    [
        {"score": float("nan")},
        {"score": float("inf")},
        {"items": {"bad": float("-inf")}},
        {1: "not a string key"},
    ],
)
def test_gate_store_revalidates_forged_decision_conditions(
    tmp_path: Path, conditions: dict[object, object]
) -> None:
    """Persistence boundaries reject invalid metadata even after model mutation."""
    gates = gate_store(tmp_path)
    gates.request(
        GateRequest(id="gate-design", name="Research design", requested_by="agent-a")
    )
    decision = GateDecision(
        status=GateStatus.APPROVED,
        decided_by="reviewer-b",
        rationale="Looks sound.",
    )
    object.__setattr__(decision, "conditions", conditions)

    with pytest.raises(ValidationError):
        gates.decide("gate-design", decision)


def test_decision_before_request_is_rejected(tmp_path: Path) -> None:
    """A durable decision cannot predate the gate it closes."""
    gates = gate_store(tmp_path)
    gates.request(
        GateRequest(
            id="gate-design",
            name="Research design",
            requested_by="agent-a",
            requested_at=datetime(2026, 8, 4, 9, 0, tzinfo=UTC),
        )
    )

    with pytest.raises(ValueError, match="decision cannot predate the gate request"):
        gates.decide(
            "gate-design",
            GateDecision(
                status=GateStatus.APPROVED,
                decided_by="reviewer-b",
                rationale="Looks sound.",
                decided_at=datetime(2026, 8, 4, 8, 59, tzinfo=UTC),
            ),
        )


def test_request_retry_repairs_event_append_failure(tmp_path: Path) -> None:
    """Retrying a persisted request must restore its missing audit event."""
    artifacts = ArtifactStore(tmp_path)
    failed_event_path = tmp_path / "failed-events"
    failed_event_path.mkdir()
    gate = GateRequest(id="gate-design", name="Research design", requested_by="agent-a")

    with pytest.raises(IsADirectoryError):
        GateStore(artifacts, EventLog(failed_event_path)).request(gate)

    recovered_events = EventLog(tmp_path / "events.jsonl")
    GateStore(artifacts, recovered_events).request(gate)

    assert [event.event_type for event in recovered_events.read_all()] == ["gate.requested"]


def test_decision_retry_repairs_event_append_failure(tmp_path: Path) -> None:
    """Retrying an already persisted decision must restore its audit event."""
    artifacts = ArtifactStore(tmp_path)
    events = EventLog(tmp_path / "events.jsonl")
    gates = GateStore(artifacts, events)
    gates.request(GateRequest(id="gate-design", name="Research design", requested_by="agent-a"))
    failed_event_path = tmp_path / "failed-events"
    failed_event_path.mkdir()
    decision = GateDecision(
        status=GateStatus.APPROVED,
        decided_by="reviewer-b",
        rationale="Looks sound.",
    )

    with pytest.raises(IsADirectoryError):
        GateStore(artifacts, EventLog(failed_event_path)).decide("gate-design", decision)

    gates.decide("gate-design", decision)

    assert [event.event_type for event in events.read_all()] == [
        "gate.requested",
        "gate.approved",
    ]


@pytest.mark.parametrize(
    ("requested_at", "decided_at"),
    [
        (datetime(2026, 8, 4, 9, 0), None),  # noqa: DTZ001
        (datetime(2026, 8, 4, 9, 0, tzinfo=timezone(timedelta(hours=8))), None),
        (None, datetime(2026, 8, 4, 9, 0)),  # noqa: DTZ001
        (None, datetime(2026, 8, 4, 9, 0, tzinfo=timezone(timedelta(hours=8)))),
    ],
)
def test_gate_timestamps_require_canonical_utc(
    requested_at: datetime | None, decided_at: datetime | None
) -> None:
    """Gate timestamps must not accept naïve or offset-local representations."""
    if requested_at is not None:
        with pytest.raises(ValidationError, match="UTC"):
            GateRequest(
                id="gate-design",
                name="Research design",
                requested_by="agent-a",
                requested_at=requested_at,
            )
    if decided_at is not None:
        with pytest.raises(ValidationError, match="UTC"):
            GateDecision(
                status=GateStatus.APPROVED,
                decided_by="reviewer-b",
                rationale="Design is ready.",
                decided_at=decided_at,
            )


def test_gate_decision_only_accepts_terminal_decision_statuses() -> None:
    """A decision cannot leave a durable gate pending."""
    with pytest.raises(ValidationError):
        GateDecision(
            status=GateStatus.PENDING,
            decided_by="reviewer-b",
            rationale="Not a decision.",
        )


def test_rejected_gate_has_a_rejection_event(tmp_path: Path) -> None:
    """A rejection must be distinguishable from an approval in the audit log."""
    gates = gate_store(tmp_path)
    gates.request(GateRequest(id="gate-design", name="Research design", requested_by="agent-a"))
    gates.decide(
        "gate-design",
        GateDecision(
            status=GateStatus.REJECTED,
            decided_by="reviewer-b",
            rationale="Specify the sampling frame.",
        ),
    )

    events = EventLog(tmp_path / "events.jsonl").read_all()
    assert [event.event_type for event in events] == ["gate.requested", "gate.rejected"]
    assert json.loads((tmp_path / "events.jsonl").read_text().splitlines()[1])["event_type"] == "gate.rejected"


@pytest.mark.parametrize("gate_id", ["a/b", r"a\b", ".", "..", "/absolute"])
def test_gate_ids_must_be_safe_single_filename_segments(
    tmp_path: Path, gate_id: str
) -> None:
    """Traversal-like IDs must not choose an artifact path outside gates/."""
    with pytest.raises(ValidationError, match="safe filename segment"):
        GateRequest(id=gate_id, name="Research design", requested_by="agent-a")

    with pytest.raises(ValueError, match="safe filename segment"):
        gate_store(tmp_path).decide(
            gate_id,
            GateDecision(
                status=GateStatus.APPROVED,
                decided_by="reviewer-b",
                rationale="Looks sound.",
            ),
        )


def test_concurrent_conflicting_decisions_leave_one_terminal_event(tmp_path: Path) -> None:
    """Only one concurrent human decision may close a pending gate."""
    gates = gate_store(tmp_path)
    gates.request(GateRequest(id="gate-design", name="Research design", requested_by="agent-a"))
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    outcomes = context.Queue()
    processes = [
        context.Process(target=decide_concurrently, args=(str(tmp_path), status, start, outcomes))
        for status in ("approved", "rejected")
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=10)

    assert [process.exitcode for process in processes] == [0, 0]
    assert sorted(outcomes.get(timeout=2) for _ in processes) in (
        ["approved", "rejected"],
        ["rejected", "rejected"],
    )
    events = EventLog(tmp_path / "events.jsonl").read_all()
    assert len(events) == 2
    assert {event.event_type for event in events[1:]} <= {"gate.approved", "gate.rejected"}
    assert GateRequest.model_validate(
        ArtifactStore(tmp_path).read_json(Path("gates/gate-design.json"))
    ).status in (GateStatus.APPROVED, GateStatus.REJECTED)


def test_concurrent_duplicate_requests_write_one_gate_and_one_event(tmp_path: Path) -> None:
    """Only one concurrent request may create a gate ID and its audit event."""
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=request_concurrently,
            args=(str(tmp_path), f"agent-{index}", start, outcomes),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=10)

    assert [process.exitcode for process in processes] == [0, 0]
    assert sorted(outcomes.get(timeout=2) for _ in processes) == ["duplicate", "requested"]
    assert [event.event_type for event in EventLog(tmp_path / "events.jsonl").read_all()] == [
        "gate.requested"
    ]


def test_gate_lock_is_released_when_a_lock_holder_crashes(tmp_path: Path) -> None:
    """A hard process exit must not permanently prevent later gate operations."""
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(target=crash_while_holding_gate_lock, args=(str(tmp_path), ready))
    process.start()
    assert ready.wait(timeout=10)
    process.join(timeout=10)
    assert process.exitcode == 0

    gate_store(tmp_path).request(
        GateRequest(id="gate-design", name="Research design", requested_by="agent-a")
    )

    assert [event.event_type for event in EventLog(tmp_path / "events.jsonl").read_all()] == [
        "gate.requested"
    ]


def test_legacy_directory_lock_does_not_block_new_advisory_lock(tmp_path: Path) -> None:
    """A persisted directory from the retired lock implementation is ignored."""
    (tmp_path / "gates" / ".locks" / "gate-design.lock").mkdir(parents=True)

    gate_store(tmp_path).request(
        GateRequest(id="gate-design", name="Research design", requested_by="agent-a")
    )

    assert [event.event_type for event in EventLog(tmp_path / "events.jsonl").read_all()] == [
        "gate.requested"
    ]
