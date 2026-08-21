"""Crash recovery for owner-authenticated research gate decisions."""

from __future__ import annotations

from pathlib import Path

import pytest
from orchestrator_fixtures import (
    broad_brief,
    candidate_payload,
    config,
    gate_capability,
)

from envresearch.kernel.gates import GateDecision, GateRequest
from envresearch.models.enums import GateStatus
from envresearch.models.intake import ResearchIntakeMode
from envresearch.research.orchestrator import ResearchOrchestrator


def test_gate_api_recovers_public_decision_before_control_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash after public decision publication must remain retryable."""
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    source = tmp_path / "incoming/candidate-charters.json"
    source.parent.mkdir()
    source.write_text(candidate_payload().model_dump_json(), encoding="utf-8")
    order = orchestrator.queue.read_order("frame-charters")
    orchestrator.queue.submit(
        "frame-charters", source, expected_order_hash=order.order_hash
    )
    orchestrator.accept_submission("frame-charters")
    orchestrator.advance()
    conditions = {
        **orchestrator.bound_gates.decision_conditions("gate-1"),
        "selected_candidate_id": "charter-air",
    }

    def crash(_gate: object) -> None:
        raise RuntimeError("crash before protected gate record")

    monkeypatch.setattr(orchestrator.principals, "record_gate_decision", crash)
    with pytest.raises(RuntimeError, match="protected gate record"):
        _decide(orchestrator, conditions)
    monkeypatch.undo()

    recovered = _decide(orchestrator, conditions)
    assert recovered.decision is not None
    assert orchestrator.advance().phase.value == "waiting_for_agent"


def _decide(
    orchestrator: ResearchOrchestrator, conditions: dict[str, object]
) -> GateRequest:
    return orchestrator.decide_gate(
        "gate-1",
        GateDecision(
            status=GateStatus.APPROVED,
            decided_by="human-reviewer",
            rationale="Approve current charter.",
            conditions=conditions,
        ),
        gate_capability(orchestrator),
    )
