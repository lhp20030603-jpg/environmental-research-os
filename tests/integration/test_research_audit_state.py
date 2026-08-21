"""Durable research run-manifest and decision-ledger integration tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import MappingProxyType

import pytest
from orchestrator_fixtures import (
    approve,
    broad_brief,
    candidate_payload,
    config,
    estimand,
    gate_capability,
    literature,
    memo_candidate,
    methods,
    plan,
    ready_for_final_gate,
    safe_feasibility,
    submit,
)

from envresearch.kernel.gates import GateDecision
from envresearch.methods.registry import MethodProfileRegistry
from envresearch.models.artifact import ResearchArtifact, seal_artifact
from envresearch.models.design import DesignFinding, DesignReviewPayload, ReviewSeverity
from envresearch.models.enums import ArtifactLifecycle, GateStatus
from envresearch.models.intake import ResearchIntakeMode
from envresearch.research.orchestrator import ResearchOrchestrator


def test_initialization_creates_recoverable_manifest_and_decision_log(
    tmp_path: Path,
) -> None:
    """Removing either mandatory audit artifact must fail this test."""
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )

    manifest = json.loads((tmp_path / "research-run-manifest.json").read_text())
    assert manifest["run_id"] == "run-orchestration"
    assert manifest["schema_version"] == "1.1"
    assert manifest["intake_artifact"]["artifact_version"] == 2
    assert manifest["method_profiles"]["did-event-study"] == "0.2.0"
    assert len(manifest["method_profile_sha256"]["did-event-study"]) == 64
    assert (tmp_path / "decision-log.jsonl").exists()

    before = (tmp_path / "research-run-manifest.json").read_bytes()
    orchestrator.close()
    recovered = ResearchOrchestrator()
    recovered.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    assert (tmp_path / "research-run-manifest.json").read_bytes() == before


def test_manifest_binds_method_profile_content_not_only_version(tmp_path: Path) -> None:
    """Changing scientific rules without a version bump invalidates run recovery."""
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    registry = orchestrator.semantics.registry
    profiles = dict(registry.profiles)
    original = profiles["did-event-study"]
    changed = original.model_copy(
        update={
            "identifying_assumptions": (
                *original.identifying_assumptions,
                "Changed scientific rule without a version bump.",
            )
        }
    )
    profiles[original.profile_id] = changed
    altered = MethodProfileRegistry(profiles=MappingProxyType(profiles))

    with pytest.raises(ValueError, match="method profile|manifest"):
        orchestrator.audit.verify_method_profiles(altered)

    expected = hashlib.sha256(
        json.dumps(
            {
                **original.model_dump(mode="json"),
                "compatible_estimands": sorted(original.compatible_estimands),
                "required_data_structures": sorted(original.required_data_structures),
                "required_features": sorted(original.required_features),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    manifest = json.loads((tmp_path / "research-run-manifest.json").read_text())
    assert manifest["method_profile_sha256"][original.profile_id] == expected


def test_recovery_rejects_a_changed_research_run_manifest(tmp_path: Path) -> None:
    """Overwriting a changed manifest during recovery must fail this test."""
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    orchestrator.close()
    path = tmp_path / "research-run-manifest.json"
    payload = json.loads(path.read_text())
    payload["run_id"] = "foreign-run"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest"):
        ResearchOrchestrator().initialize(
            config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
        )


def test_gate_request_and_decision_are_idempotent_artifact_bound_audit_entries(
    tmp_path: Path,
) -> None:
    """Dropping gate audit synchronization must fail this durable replay check."""
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    orchestrator.advance()
    approve(orchestrator, "gate-1", selected_candidate_id="charter-air")
    orchestrator.advance()

    path = tmp_path / "decision-log.jsonl"
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert [entry["decision_kind"] for entry in entries].count("gate_request") == 1
    assert [entry["decision_kind"] for entry in entries].count("gate_decision") == 1
    assert entries[-1]["metadata"]["artifact_refs"][0]["content_hash"]

    before = path.read_bytes()
    orchestrator.advance()
    assert path.read_bytes() == before


def test_disagreement_risk_acceptance_and_terminal_approval_are_audited(
    tmp_path: Path,
) -> None:
    """Dropping scientific decision kinds must fail the final ledger inventory."""
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    orchestrator.advance()
    approve(orchestrator, "gate-1", selected_candidate_id="charter-air")
    orchestrator.advance()
    submit(orchestrator, "map-literature", literature())
    submit(orchestrator, "inspect-data", safe_feasibility())
    orchestrator.advance()
    submit(orchestrator, "define-estimand", estimand())
    orchestrator.advance()
    ref = orchestrator.lifecycle.artifact_ref(Path("artifacts/estimand-spec.yaml"))
    token = (
        f"artifact:{ref.artifact_id}@{ref.artifact_version}#sha256:{ref.content_hash}"
    )
    submit(orchestrator, "rank-methods", methods(token))
    orchestrator.advance()
    submit(orchestrator, "draft-identification", memo_candidate(token))
    orchestrator.advance()
    finding = DesignFinding(
        finding_id="major-spillover",
        severity=ReviewSeverity.MAJOR,
        resolved=False,
        finding="Spillover boundaries remain uncertain.",
        evidence_refs=("evidence-1",),
        residual_risk="Residual cross-boundary exposure may remain.",
    )
    submit(
        orchestrator,
        "review-design",
        DesignReviewPayload(review_id="review-risk", findings=(finding,)),
    )
    orchestrator.advance()
    submit(orchestrator, "compose-plan", plan(token))
    orchestrator.advance()
    approve(orchestrator, "final-gate", accepted_major_ids=["major-spillover"])
    orchestrator.advance()

    entries = [
        json.loads(line)
        for line in (tmp_path / "decision-log.jsonl").read_text().splitlines()
    ]
    kinds = {entry["decision_kind"] for entry in entries}
    assert {"agent_disagreement", "risk_acceptance", "terminal_approval"} <= kinds


def test_rejected_and_superseding_gates_append_revision_requests(
    tmp_path: Path,
) -> None:
    """Recording only gate_decision must fail this revision audit contract."""
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    orchestrator.advance()
    context = orchestrator.bound_gates.active_context("gate-1")
    assert context is not None
    orchestrator.decide_gate(
        "gate-1",
        GateDecision(
            status=GateStatus.REJECTED,
            decided_by="human-reviewer",
            rationale="Revise the charter scope.",
            conditions=orchestrator.bound_gates.decision_conditions("gate-1"),
        ),
        gate_capability(orchestrator),
    )
    changed = (context.artifact_refs[0].model_copy(update={"content_hash": "f" * 64}),)
    revised = orchestrator.bound_gates.ensure("gate-1", "Research charter", changed)
    assert revised.revision == 2
    orchestrator.audit.sync()

    entries = [
        json.loads(line)
        for line in (tmp_path / "decision-log.jsonl").read_text().splitlines()
    ]
    revisions = [
        entry for entry in entries if entry["decision_kind"] == "revision_request"
    ]
    assert {entry["status"] for entry in revisions} == {"rejected", "requested"}
    assert any(entry["metadata"]["revision"] == 2 for entry in revisions)


def test_terminal_audit_uses_active_revised_final_gate_context(tmp_path: Path) -> None:
    """Preferring final-gate r1 must fail this recovered r2 terminal attribution."""
    orchestrator = ready_for_final_gate(tmp_path)
    approve(orchestrator, "final-gate", accepted_major_ids=[])
    orchestrator.advance()
    first = orchestrator.bound_gates.active_context("final-gate")
    assert first is not None
    changed = (
        first.artifact_refs[0].model_copy(update={"content_hash": "e" * 64}),
        first.artifact_refs[1],
    )
    revised = orchestrator.bound_gates.ensure("final-gate", "Research design", changed)
    conditions = orchestrator.bound_gates.decision_conditions("final-gate")
    orchestrator.decide_gate(
        "final-gate",
        GateDecision(
            status=GateStatus.APPROVED,
            decided_by="human-reviewer",
            rationale="Approved the revised artifact context.",
            conditions={**conditions, "accepted_major_ids": []},
        ),
        gate_capability(orchestrator),
    )
    path = Path("artifacts/analysis-plan.yaml")
    current = orchestrator.lifecycle.read_artifact(path)
    rebound = seal_artifact(
        ResearchArtifact(
            envelope=current.envelope.model_copy(
                update={
                    "content_hash": None,
                    "validation_status": ArtifactLifecycle.APPROVED,
                    "provenance": {
                        **current.envelope.provenance,
                        "gate_context_hash": revised.context_hash,
                    },
                }
            ),
            payload=current.payload,
        )
    )
    orchestrator.lifecycle.store.write_structured(path, rebound)

    orchestrator.audit.sync()

    entries = [
        json.loads(line)
        for line in (tmp_path / "decision-log.jsonl").read_text().splitlines()
    ]
    terminal = [
        entry for entry in entries if entry["decision_kind"] == "terminal_approval"
    ]
    assert terminal[-1]["actor"] == "human-reviewer"
    assert terminal[-1]["event_id"].endswith(revised.gate_id)
