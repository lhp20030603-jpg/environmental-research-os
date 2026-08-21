"""Real V0.2 fixtures for the factory approved-design handoff."""

from __future__ import annotations

from pathlib import Path

from orchestrator_fixtures import approve, ready_for_final_gate

from envresearch.models.artifact import ArtifactRef
from envresearch.research.orchestrator import ResearchOrchestrator, ResearchRunPhase

PLAN_PATH = Path("artifacts/analysis-plan.yaml")


def factory_root(tmp_path: Path) -> Path:
    """Return a sibling root physically separate from the research workspace."""
    return tmp_path.parent / f"{tmp_path.name}-factory"


def completed_orchestrator(tmp_path: Path) -> ResearchOrchestrator:
    """Create one independently approved V0.2 final state."""
    orchestrator = ready_for_final_gate(tmp_path)
    approve(orchestrator, "final-gate", accepted_major_ids=[])
    assert orchestrator.advance().phase is ResearchRunPhase.COMPLETE
    return orchestrator


def final_context_ref(orchestrator: ResearchOrchestrator) -> ArtifactRef:
    """Return the caller-supplied exact final gate context reference."""
    context = orchestrator.bound_gates.active_context("final-gate")
    assert context is not None and context.context_hash is not None
    return ArtifactRef(
        artifact_id="final-gate-context",
        artifact_version=context.revision,
        content_hash=context.context_hash,
    )
