"""Mutation boundary for applying one approved, artifact-bound Final Gate."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from envresearch.models.design import DesignReviewPayload
from envresearch.models.enums import ArtifactLifecycle, GateStatus
from envresearch.research.citation_gate import citation_node_inputs
from envresearch.research.final_binding import terminal_refs
from envresearch.research.gate_policy import GATE_NAMES
from envresearch.research.principal_policy import require_gate_principal
from envresearch.research.review_policy import ReviewPolicy
from envresearch.research.submission_policy import accepted_major_ids

if TYPE_CHECKING:
    from envresearch.research.orchestrator import ResearchOrchestrator


def apply_final_gate(orchestrator: ResearchOrchestrator) -> None:
    """Promote the reviewed plan and checkpoint its exact terminal lineage."""
    if orchestrator.bound_gates.active_context("final-gate") is None:
        return
    gate = orchestrator._gate("final-gate")
    if gate is None or gate.status is not GateStatus.APPROVED:
        return
    context = orchestrator.bound_gates.require_approved(
        "final-gate", GATE_NAMES["final-gate"], orchestrator._gate_refs("final-gate")
    )
    if context is None:
        return
    plan_path = Path("artifacts/analysis-plan.yaml")
    current = orchestrator.lifecycle.read_artifact(plan_path)
    assert gate.decision is not None
    require_gate_principal(orchestrator, "final-gate", gate)
    accepted = accepted_major_ids(gate.decision.conditions)
    orchestrator.audit.verify_method_profiles(orchestrator.semantics.registry)
    orchestrator.semantics.validate_final()
    review = orchestrator.lifecycle.read_payload(
        Path("artifacts/design-review-findings.json"), DesignReviewPayload
    )
    if not ReviewPolicy.final_gate_eligible(review.findings, accepted):
        raise ValueError("final-gate conditions do not close current review findings")
    compose = orchestrator._nodes["compose-plan"]
    compose_inputs = orchestrator.lifecycle.input_refs(compose)
    compose_checkpoint = orchestrator.workspace / "node-checkpoints/compose-plan.json"
    if current.envelope.validation_status is ArtifactLifecycle.VALIDATED:
        if compose_checkpoint.exists():
            orchestrator.checkpoints.invalidate(
                orchestrator.graph, "compose-plan", reason="final gate approval"
            )
    elif current.envelope.validation_status is not ArtifactLifecycle.APPROVED:
        raise ValueError("final gate requires a validated analysis plan")
    assert context.context_hash is not None
    orchestrator.lifecycle.promote_status(
        plan_path,
        ArtifactLifecycle.APPROVED,
        "human-final-gate",
        predecessor_ref=context.artifact_refs[1],
        predecessor_component=orchestrator.lifecycle.read_history(
            plan_path, context.artifact_refs[1].artifact_version
        ).envelope.producer,
        expected_inputs=compose_inputs,
        gate_context_hash=context.context_hash,
    )
    compose_inputs = orchestrator.lifecycle.input_refs(compose)
    if compose_checkpoint.exists() and not orchestrator.checkpoints.verify(
        compose, compose_inputs
    ):
        orchestrator.checkpoints.invalidate(
            orchestrator.graph,
            "compose-plan",
            reason="reconcile final gate approval",
        )
    if not orchestrator.checkpoints.verify(compose, compose_inputs):
        orchestrator.checkpoints.publish(compose, compose_inputs, compose.output_paths)
    if orchestrator.config.require_claim_verified_citations:
        citation = orchestrator._nodes["validate-citations"]
        citation_inputs = citation_node_inputs(orchestrator.lifecycle)
        if not orchestrator.checkpoints.verify(citation, citation_inputs):
            orchestrator.checkpoints.publish(
                citation, citation_inputs, citation.output_paths
            )
    final = orchestrator._nodes["final-approval"]
    final_inputs = terminal_refs(
        orchestrator.lifecycle.artifact_ref(plan_path),
        context.artifact_refs[1],
        context.context_hash,
        context.revision,
        (
            orchestrator.lifecycle.artifact_ref(
                Path("artifacts/citation-integrity-report.json")
            )
            if orchestrator.config.require_claim_verified_citations
            else None
        ),
    )
    if not orchestrator.checkpoints.verify(final, final_inputs):
        orchestrator.checkpoints.publish(final, final_inputs, ())
