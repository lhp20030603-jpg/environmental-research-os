"""Journal and audit-consistency regressions for approval gates."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from envresearch.kernel.events import EventLog, EventRecord
from envresearch.kernel.gates import GateDecision, GateRequest, GateStore
from envresearch.models.enums import GateStatus, WorkflowStatus
from envresearch.storage.artifacts import ArtifactStore


def _store(root: Path) -> GateStore:
    return GateStore(ArtifactStore(root), EventLog(root / "events.jsonl"))


def _pending_gate(store: GateStore) -> None:
    store.request(
        GateRequest(
            id="gate-design",
            name="Research design",
            requested_by="agent-a",
            requested_at=datetime(2026, 8, 4, 9, 0, tzinfo=UTC),
        )
    )


def _approval() -> GateDecision:
    return GateDecision(
        status=GateStatus.APPROVED,
        decided_by="reviewer-b",
        rationale="Design is ready.",
        decided_at=datetime(2026, 8, 4, 9, 5, tzinfo=UTC),
    )


def test_fresh_store_recovers_decision_event_before_authorizing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery must not require the caller to reconstruct a decision timestamp."""
    store = _store(tmp_path)
    _pending_gate(store)
    original_append = store.events.append

    def fail_approval(event: EventRecord) -> None:
        if event.event_type == "gate.approved":
            raise OSError("approval event publication failed")
        original_append(event)

    monkeypatch.setattr(store.events, "append", fail_approval)

    with pytest.raises(OSError, match="approval event publication failed"):
        store.decide("gate-design", _approval())

    recovered = _store(tmp_path)
    recovered.require_approved("gate-design")
    events = recovered.events.read_all()

    assert [event.event_type for event in events] == [
        "gate.requested",
        "gate.approved",
    ]
    assert events[-1].timestamp == _approval().decided_at


def test_gate_recovery_rejects_same_event_id_with_different_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An event identity collision must not be mistaken for the journaled decision."""
    store = _store(tmp_path)
    _pending_gate(store)
    original_append = store.events.append

    def fail_approval(event: EventRecord) -> None:
        if event.event_type == "gate.approved":
            raise OSError("approval event publication failed")
        original_append(event)

    monkeypatch.setattr(store.events, "append", fail_approval)
    with pytest.raises(OSError):
        store.decide("gate-design", _approval())

    original_append(
        EventRecord(
            event_id="gate-design.approved",
            run_id="gate-design",
            event_type="gate.approved",
            actor="reviewer-c",
            timestamp=datetime(2026, 8, 4, 9, 6, tzinfo=UTC),
            from_status=WorkflowStatus.PENDING,
            to_status=WorkflowStatus.APPROVED,
            payload={"gate_id": "gate-design", "rationale": "Different."},
        )
    )

    with pytest.raises(RuntimeError, match="identity collision"):
        _store(tmp_path).require_approved("gate-design")


def test_approved_gate_without_matching_event_fails_closed(tmp_path: Path) -> None:
    """A terminal gate artifact alone must never authorize execution."""
    store = _store(tmp_path)
    _pending_gate(store)
    store.decide("gate-design", _approval())
    lines = (tmp_path / "events.jsonl").read_bytes().splitlines(keepends=True)
    (tmp_path / "events.jsonl").write_bytes(lines[0])

    with pytest.raises(PermissionError, match="matching approval event"):
        _store(tmp_path).require_approved("gate-design")


def test_decision_repairs_missing_request_event_before_transition(tmp_path: Path) -> None:
    """A terminal transition must leave a complete exact request/decision audit."""
    failed_event_path = tmp_path / "failed-events"
    failed_event_path.mkdir()
    gate = GateRequest(
        id="gate-design",
        name="Research design",
        requested_by="agent-a",
        requested_at=datetime(2026, 8, 4, 9, 0, tzinfo=UTC),
    )
    with pytest.raises(IsADirectoryError):
        GateStore(ArtifactStore(tmp_path), EventLog(failed_event_path)).request(gate)

    store = _store(tmp_path)
    store.decide("gate-design", _approval())

    assert [event.event_type for event in store.events.read_all()] == [
        "gate.requested",
        "gate.approved",
    ]
