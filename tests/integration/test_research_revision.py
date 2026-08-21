"""Durable local recomputation for rejected gates and design findings."""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator_fixtures import (
    approve,
    broad_brief,
    candidate_payload,
    config,
    estimand,
    gate_capability,
    literature,
    memo_candidate,
    plan,
    ready_for_final_gate,
    restricted_feasibility,
    revision_capability,
    submit,
)
from typer.testing import CliRunner

from envresearch.cli import app
from envresearch.kernel.gates import GateDecision
from envresearch.models.design import (
    DesignFinding,
    DesignReviewPayload,
    ReviewSeverity,
)
from envresearch.models.enums import ArtifactLifecycle, GateStatus
from envresearch.models.intake import ResearchIntakeMode
from envresearch.research.orchestrator import ResearchOrchestrator

CLI = CliRunner()


def _reject_gate_one(orchestrator: ResearchOrchestrator) -> None:
    orchestrator.advance()
    context = orchestrator.bound_gates.active_context("gate-1")
    assert context is not None
    orchestrator.decide_gate(
        "gate-1",
        GateDecision(
            status=GateStatus.REJECTED,
            decided_by="human-reviewer",
            rationale="The framing must be revised.",
            conditions={
                "gate_context": context.model_dump(mode="json"),
                "selected_candidate_id": "charter-air",
            },
        ),
        gate_capability(orchestrator),
    )


def test_gate_one_rejection_can_revise_and_reissue_entry(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    unrelated = tmp_path / "node-checkpoints" / "frame-charters.json"
    assert unrelated.exists()
    _reject_gate_one(orchestrator)

    revision = orchestrator.request_revision(
        "frame-charters",
        reason="Address Gate 1 rejection",
        actor="human-reviewer",
        principal_capability=revision_capability(orchestrator),
    )

    assert revision.node_id == "frame-charters"
    assert not unrelated.exists()
    assert (tmp_path / "work-orders/frame-charters.json").exists()
    current = orchestrator.lifecycle.read_artifact(
        Path("artifacts/candidate-charters.json")
    )
    assert current.envelope.validation_status is ArtifactLifecycle.SUPERSEDED
    assert current.envelope.provenance["revision_id"] == revision.revision_id
    journal = [
        json.loads(line)
        for line in (tmp_path / "revisions/journal.jsonl").read_text().splitlines()
    ]
    assert journal[0]["event"] == "revision_intent"
    assert journal[-1]["event"] == "revision_completed"


def test_same_revision_retry_is_idempotent_and_conflict_fails(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())

    first = orchestrator.request_revision(
        "frame-charters",
        reason="Improve framing",
        actor="researcher",
        principal_capability=revision_capability(orchestrator),
    )
    retry = orchestrator.request_revision(
        "frame-charters",
        reason="Improve framing",
        actor="researcher",
        principal_capability=revision_capability(orchestrator),
    )

    assert retry == first
    try:
        orchestrator.request_revision(
            "frame-charters",
            reason="A conflicting reason",
            actor="researcher",
            principal_capability=revision_capability(orchestrator),
        )
    except RuntimeError as error:
        assert "conflicting revision" in str(error)
    else:  # pragma: no cover - assertion message is clearer than pytest internals
        raise AssertionError("conflicting revision was accepted")


def test_public_cli_requests_revision_and_returns_bound_order(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    initialized = CLI.invoke(
        app,
        [
            "research",
            "init",
            "benchmarks/design/fixtures/structured-brief/brief.yaml",
            "--config",
            "configs/research-default.yaml",
            "--run-root",
            str(run_root),
            "--json",
        ],
    )
    assert initialized.exit_code == 0, initialized.output
    candidate = run_root / "incoming/research-brief.yaml"
    candidate.parent.mkdir()
    candidate.write_bytes(
        Path(
            "benchmarks/design/fixtures/structured-brief/research-brief.yaml"
        ).read_bytes()
    )
    submitted = CLI.invoke(
        app,
        [
            "research",
            "submit",
            str(run_root),
            "normalize-brief",
            str(candidate),
            "--order-hash",
            json.loads((run_root / "work-orders/normalize-brief.json").read_text())[
                "order_hash"
            ],
            "--producer-context",
            "revision-cli-context",
            "--json",
        ],
    )
    assert submitted.exit_code == 0, submitted.output

    revised = CLI.invoke(
        app,
        [
            "research",
            "revise",
            str(run_root),
            "normalize-brief",
            "--actor",
            "human-reviewer",
            "--reason",
            "Refine the structured question",
            "--principal-capability-file",
            str(
                run_root.parent
                / ".run.worker-queue-control/principals/revision.capability"
            ),
            "--json",
        ],
    )

    assert revised.exit_code == 0, revised.output
    body = json.loads(revised.output)
    assert body["node_id"] == "normalize-brief"
    order = json.loads((run_root / "work-orders/normalize-brief.json").read_text())
    assert body["revision_id"] in order["node_version"]


def test_rejected_final_gate_reissues_revision_bound_plan(tmp_path: Path) -> None:
    orchestrator = ready_for_final_gate(tmp_path)
    first = orchestrator.bound_gates.active_context("final-gate")
    assert first is not None
    orchestrator.decide_gate(
        "final-gate",
        GateDecision(
            status=GateStatus.REJECTED,
            decided_by="human-reviewer",
            rationale="Strengthen the robustness plan.",
            conditions={
                "gate_context": first.model_dump(mode="json"),
                "accepted_major_ids": [],
            },
        ),
        gate_capability(orchestrator),
    )
    token = orchestrator.lifecycle.read_payload(
        Path("artifacts/analysis-plan.yaml"), type(plan())
    ).estimand_ref

    revision = orchestrator.request_revision(
        "compose-plan",
        reason="Address Final Gate",
        actor="human-reviewer",
        principal_capability=revision_capability(orchestrator),
    )
    submit(orchestrator, "compose-plan", plan(token))
    summary = orchestrator.advance()
    revised = orchestrator.bound_gates.active_context("final-gate")

    assert revision.affected_nodes == ("compose-plan",)
    assert summary.pending_gate_ids == ("final-gate-r2",)
    assert revised is not None and revised.revision == 2
    assert revised.artifact_refs[1].artifact_version == 5
    approve(orchestrator, "final-gate", accepted_major_ids=[])
    assert orchestrator.advance().phase.value == "complete"


def test_blocking_finding_can_be_closed_by_local_review_revision(
    tmp_path: Path,
) -> None:
    orchestrator = ready_for_final_gate(tmp_path)
    orchestrator.request_revision(
        "review-design",
        reason="Introduce adversarial finding",
        actor="critic",
        principal_capability=revision_capability(orchestrator),
    )
    blocking = DesignFinding(
        finding_id="blocking-design",
        severity=ReviewSeverity.BLOCKING,
        resolved=False,
        finding="The comparison design is not yet credible.",
        evidence_refs=("evidence-1",),
        remediation="Revise the comparison design.",
    )
    submit(
        orchestrator,
        "review-design",
        DesignReviewPayload(review_id="review-blocked", findings=(blocking,)),
    )
    assert orchestrator.advance().phase.value == "blocked"

    second = orchestrator.request_revision(
        "review-design",
        reason="Close blocking finding",
        actor="critic",
        principal_capability=revision_capability(orchestrator),
    )
    resolved = blocking.model_copy(
        update={
            "resolved": True,
            "remediation": None,
            "resolution": "Comparison design and diagnostics were revised.",
        }
    )
    submit(
        orchestrator,
        "review-design",
        DesignReviewPayload(review_id="review-resolved", findings=(resolved,)),
    )
    advanced = orchestrator.advance()

    assert second.generation == 2
    assert "compose-plan" in advanced.pending_work_order_nodes
    assert advanced.phase.value == "waiting_for_agent"


def test_markdown_node_revision_reissues_and_revalidates_memo(tmp_path: Path) -> None:
    orchestrator = ready_for_final_gate(tmp_path)
    token = orchestrator.lifecycle.read_payload(
        Path("artifacts/analysis-plan.yaml"), type(plan())
    ).estimand_ref

    revision = orchestrator.request_revision(
        "draft-identification",
        reason="Clarify identification",
        actor="researcher",
        principal_capability=revision_capability(orchestrator),
    )
    envelope, _ = orchestrator.lifecycle.store.read_markdown(
        Path("artifacts/identification-memo.md")
    )
    assert envelope.validation_status is ArtifactLifecycle.SUPERSEDED
    submit(orchestrator, "draft-identification", memo_candidate(token))
    advanced = orchestrator.advance()

    assert revision.node_id == "draft-identification"
    assert "review-design" in advanced.pending_work_order_nodes


def test_map_revision_versions_csv_and_preserves_parallel_data_checkpoint(
    tmp_path: Path,
) -> None:
    orchestrator = ready_for_final_gate(tmp_path)
    inspect_before = (tmp_path / "node-checkpoints/inspect-data.json").read_bytes()

    revision = orchestrator.request_revision(
        "map-literature",
        reason="Refresh evidence map",
        actor="researcher",
        principal_capability=revision_capability(orchestrator),
    )
    metadata = orchestrator.lifecycle.current_envelope(
        Path("artifacts/evidence-matrix.csv")
    )
    assert metadata.validation_status is ArtifactLifecycle.SUPERSEDED
    assert (
        tmp_path / "node-checkpoints/inspect-data.json"
    ).read_bytes() == inspect_before
    submit(orchestrator, "map-literature", literature())
    advanced = orchestrator.advance()

    assert "inspect-data" not in revision.affected_nodes
    assert "define-estimand" in advanced.pending_work_order_nodes
    assert (
        orchestrator.lifecycle.current_envelope(
            Path("artifacts/evidence-matrix.csv")
        ).artifact_version
        == 5
    )


def test_estimand_revision_preserves_unrelated_data_gate_context(
    tmp_path: Path,
) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    orchestrator.advance()
    approve(orchestrator, "gate-1", selected_candidate_id="charter-air")
    orchestrator.advance()
    submit(orchestrator, "map-literature", literature())
    submit(orchestrator, "inspect-data", restricted_feasibility())
    orchestrator.advance()
    approve(orchestrator, "data-gate")
    orchestrator.advance()
    submit(orchestrator, "define-estimand", estimand())
    before = orchestrator.bound_gates.active_context("data-gate")
    assert before is not None

    revision = orchestrator.request_revision(
        "define-estimand",
        reason="Refine estimand",
        actor="researcher",
        principal_capability=revision_capability(orchestrator),
    )

    assert "inspect-data" not in revision.affected_nodes
    assert orchestrator.bound_gates.active_context("data-gate") == before
    assert not (tmp_path / "gate-contexts/data-gate/superseded").exists()
