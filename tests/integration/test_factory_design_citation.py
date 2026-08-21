"""Strict-citation V0.2 handoff integration for the research factory."""

from __future__ import annotations

from pathlib import Path

from factory_fixtures import PLAN_PATH, final_context_ref
from orchestrator_fixtures import approve
from paper_draft_integration_fixtures import build_stack

from envresearch.factory.design_resolver import V02ApprovedDesignResolver
from envresearch.research.orchestrator import ResearchRunPhase


def test_resolver_builds_a_completed_claim_verified_design(tmp_path: Path) -> None:
    """Catch the handoff dropping the citation report from terminal inputs."""
    stack = build_stack(tmp_path)
    orchestrator = stack.orchestrator
    try:
        assert orchestrator.advance().pending_gate_ids == ("final-gate",)
        approve(orchestrator, "final-gate", accepted_major_ids=[])
        assert orchestrator.advance().phase is ResearchRunPhase.COMPLETE
        resolver = V02ApprovedDesignResolver(orchestrator, tmp_path / "factory")
        plan_ref = orchestrator.lifecycle.artifact_ref(PLAN_PATH)

        handoff_ref = resolver.build(plan_ref, final_context_ref(orchestrator))
        handoff = resolver.resolve(handoff_ref)

        citation_key = (
            f"{stack.report_ref.artifact_id}@{stack.report_ref.artifact_version}"
        )
        assert handoff.terminal_checkpoint.input_hashes[citation_key] == (
            stack.report_ref.content_hash
        )
        assert handoff.final_context.artifact_refs[-1] == stack.report_ref
    finally:
        orchestrator.close()
