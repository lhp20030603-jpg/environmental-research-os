"""Core integration tests for the bounded Discover and Design orchestrator."""

from __future__ import annotations

from pathlib import Path

import pytest
from orchestrator_fixtures import (
    approve,
    broad_brief,
    candidate_payload,
    charter,
    config,
    gate_capability,
    literature,
    restricted_feasibility,
    safe_feasibility,
    structured_brief,
    submit,
)

from envresearch.kernel.gates import GateDecision
from envresearch.models.enums import GateStatus
from envresearch.models.intake import (
    ResearchBriefPayload,
    ResearchCharterPayload,
    ResearchIntakeMode,
)
from envresearch.research.orchestrator import ResearchOrchestrator, ResearchRunPhase
from envresearch.research.workflow import build_research_graph
from envresearch.workers.contracts import WorkOrder


def test_graph_declares_two_entry_branches_and_shared_parallel_work() -> None:
    broad = build_research_graph(ResearchIntakeMode.BROAD_TOPIC)
    structured = build_research_graph(ResearchIntakeMode.STRUCTURED_BRIEF)

    assert broad.nodes[0].node_id == "frame-charters"
    assert structured.nodes[0].node_id == "normalize-brief"
    for graph in (broad, structured):
        nodes = {node.node_id: node for node in graph.nodes}
        assert nodes["map-literature"].dependencies == ("approve-charter",)
        assert nodes["inspect-data"].dependencies == ("approve-charter",)
        assert nodes["final-approval"].required_gate == "final-gate"
        assert all(
            "results" not in path.name
            for node in graph.nodes
            for path in node.output_paths
        )


def test_strict_graph_inserts_citation_validation_before_final_approval() -> None:
    """Enabling strict citations must add one report-producing DAG boundary."""
    default = build_research_graph(ResearchIntakeMode.BROAD_TOPIC)
    strict = build_research_graph(
        ResearchIntakeMode.BROAD_TOPIC,
        require_claim_verified_citations=True,
    )
    default_ids = tuple(node.node_id for node in default.nodes)
    strict_nodes = {node.node_id: node for node in strict.nodes}

    assert "validate-citations" not in default_ids
    assert tuple(node.node_id for node in strict.nodes) == (
        *default_ids[:-1],
        "validate-citations",
        "final-approval",
    )
    assert strict_nodes["validate-citations"].dependencies == ("compose-plan",)
    assert strict_nodes["validate-citations"].input_paths == (
        Path("artifacts/analysis-plan.yaml"),
    )
    assert strict_nodes["validate-citations"].output_paths == (
        Path("artifacts/citation-integrity-report.json"),
    )
    assert strict_nodes["final-approval"].dependencies == ("validate-citations",)
    assert (
        Path("artifacts/citation-integrity-report.json")
        in strict_nodes["final-approval"].input_paths
    )


def test_broad_topic_pauses_for_gate_one_then_issues_parallel_orders(
    tmp_path: Path,
) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())

    waiting = orchestrator.advance()
    assert waiting.phase is ResearchRunPhase.WAITING_FOR_GATE
    assert waiting.pending_gate_ids == ("gate-1",)

    approve(orchestrator, "gate-1", selected_candidate_id="charter-air")
    ready = orchestrator.advance()
    assert set(ready.pending_work_order_nodes) == {"map-literature", "inspect-data"}


def test_structured_brief_still_requires_gate_one(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.STRUCTURED_BRIEF), structured_brief()
    )
    draft = ResearchCharterPayload(
        brief=structured_brief(),
        charter=charter(
            "charter-structured",
            "Do clean-air zones reduce PM2.5?",
            ("comparison-a", "comparison-b"),
        ),
    )
    submit(orchestrator, "normalize-brief", draft)

    assert orchestrator.advance().pending_gate_ids == ("gate-1",)
    approve(orchestrator, "gate-1", selected_candidate_id="charter-structured")
    summary = orchestrator.advance()

    assert set(summary.pending_work_order_nodes) == {"map-literature", "inspect-data"}


@pytest.mark.parametrize(
    ("mode", "brief", "node_id"),
    (
        (ResearchIntakeMode.BROAD_TOPIC, broad_brief(), "frame-charters"),
        (ResearchIntakeMode.STRUCTURED_BRIEF, structured_brief(), "normalize-brief"),
    ),
)
def test_entry_order_hash_binds_exact_persisted_intake(
    tmp_path: Path,
    mode: ResearchIntakeMode,
    brief: ResearchBriefPayload,
    node_id: str,
) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(config(tmp_path, mode), brief)

    order = WorkOrder.model_validate_json(
        (tmp_path / f"work-orders/{node_id}.json").read_bytes()
    )
    assert len(order.input_artifacts) == 1
    assert order.input_artifacts == orchestrator.lifecycle.input_refs(
        orchestrator.graph.nodes[0]
    )


def test_data_gate_is_conditional_and_blocks_only_risky_data(tmp_path: Path) -> None:
    safe = ResearchOrchestrator()
    safe.initialize(
        config(tmp_path / "safe", ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(safe, "frame-charters", candidate_payload())
    safe.advance()
    approve(safe, "gate-1", selected_candidate_id="charter-air")
    safe.advance()
    submit(safe, "map-literature", literature())
    submit(safe, "inspect-data", safe_feasibility())
    safe_ready = safe.advance()
    assert "data-gate" not in safe_ready.pending_gate_ids
    assert safe_ready.pending_work_order_nodes == ("define-estimand",)

    risky = ResearchOrchestrator()
    risky.initialize(
        config(tmp_path / "risky", ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(risky, "frame-charters", candidate_payload())
    risky.advance()
    approve(risky, "gate-1", selected_candidate_id="charter-air")
    risky.advance()
    submit(risky, "map-literature", literature())
    submit(risky, "inspect-data", restricted_feasibility())
    risky_waiting = risky.advance()
    assert risky_waiting.pending_gate_ids == ("data-gate",)
    assert "define-estimand" not in risky_waiting.pending_work_order_nodes


def test_gate_one_rejects_unknown_candidate_condition(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    orchestrator.advance()
    approve(orchestrator, "gate-1", selected_candidate_id="not-a-candidate")

    with pytest.raises(ValueError, match="selected_candidate_id"):
        orchestrator.advance()


def test_gate_decision_must_echo_exact_requested_artifact_context(
    tmp_path: Path,
) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    orchestrator.advance()
    orchestrator.decide_gate(
        "gate-1",
        GateDecision(
            status=GateStatus.APPROVED,
            decided_by="human-reviewer",
            rationale="Decision omitted its immutable review context.",
            conditions={"selected_candidate_id": "charter-air"},
        ),
        gate_capability(orchestrator),
    )

    with pytest.raises(ValueError, match="decision context"):
        orchestrator.advance()


def test_changed_gate_inputs_create_a_new_pending_revision(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    orchestrator.advance()
    approve(orchestrator, "gate-1", selected_candidate_id="charter-air")
    first = orchestrator.bound_gates.active_context("gate-1")
    assert first is not None
    changed = (first.artifact_refs[0].model_copy(update={"content_hash": "f" * 64}),)

    revised = orchestrator.bound_gates.ensure("gate-1", "Research charter", changed)

    assert revised.gate_id == "gate-1-r2"
    assert revised.supersedes_gate_id == "gate-1"
    assert orchestrator.bound_gates.active_context("gate-1") == revised
    active = orchestrator.bound_gates.active_gate("gate-1")
    assert active is not None
    assert active.status is GateStatus.PENDING
