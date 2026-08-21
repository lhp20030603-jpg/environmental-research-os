"""Trusted completion boundary for the strict citation-validation graph node."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from envresearch.benchmarks.claim_report import (
    CitationIntegrityReport,
    payload_leaf_hashes,
    report_binding_is_valid,
    report_from_payload,
    report_input_refs,
)
from envresearch.models.artifact import ArtifactRef
from envresearch.models.enums import ArtifactLifecycle
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.citation_attestations import (
    ProtectedCitationAttestations,
    SourceGenerationAnchor,
)

if TYPE_CHECKING:
    from envresearch.models.benchmark_evaluation import AcceptedArtifactClaims
    from envresearch.research.orchestrator import ResearchOrchestrator

REPORT_PATH = Path("artifacts/citation-integrity-report.json")


def record_citation_integrity(
    orchestrator: ResearchOrchestrator,
    *,
    case_roots: tuple[Path, ...],
    artifacts: tuple[AcceptedArtifactClaims, ...],
) -> ArtifactRef:
    """Recompute, seal, and checkpoint the current strict citation report."""
    with orchestrator.queue.control.transaction_lock("mutation"):
        return _record_citation_integrity(
            orchestrator, case_roots=case_roots, artifacts=artifacts
        )


def _record_citation_integrity(
    orchestrator: ResearchOrchestrator,
    *,
    case_roots: tuple[Path, ...],
    artifacts: tuple[AcceptedArtifactClaims, ...],
) -> ArtifactRef:
    if not orchestrator.config.require_claim_verified_citations:
        raise ValueError("citation reports are disabled for this planning run")
    node = orchestrator._nodes.get("validate-citations")
    if node is None:
        raise ValueError("strict citation-validation node is not configured")
    completed = orchestrator.checkpoints.completed_nodes(orchestrator.graph)
    if "compose-plan" not in completed:
        raise ValueError("accepted artifacts are not ready for citation validation")
    current_refs = citation_node_inputs(orchestrator.lifecycle)
    supplied_refs = tuple(item.artifact_ref for item in artifacts)
    if supplied_refs != current_refs:
        raise ValueError("citation report does not cover the current accepted artifact")
    for path, accepted in zip(node.input_paths, artifacts, strict=True):
        current = orchestrator.lifecycle.read_artifact(path)
        if current.payload != accepted.payload:
            raise ValueError(
                "citation report payload is not the current accepted artifact"
            )

    def before_persist(
        source_generation: SourceGenerationAnchor,
        report: CitationIntegrityReport,
        source_changed: bool,
    ) -> None:
        if not (orchestrator.workspace / REPORT_PATH).exists():
            return
        current = orchestrator.lifecycle.read_artifact(REPORT_PATH)
        if current.envelope.validation_status is ArtifactLifecycle.SUPERSEDED:
            return
        existing = report_from_payload(current.payload)
        if source_changed or (
            existing.source_sheet_refs != report.source_sheet_refs
            or existing.claim_fact_map_refs != report.claim_fact_map_refs
            or existing.blinded_brief_refs != report.blinded_brief_refs
        ):
            _retire_report(orchestrator, source_generation)

    report_ref, _ = orchestrator.citation_attestations.validate_and_seal(
        lifecycle=orchestrator.lifecycle,
        case_roots=case_roots,
        artifacts=artifacts,
        before_persist=before_persist,
    )
    checkpoint_inputs = citation_node_inputs(orchestrator.lifecycle)
    if not orchestrator.checkpoints.verify(node, checkpoint_inputs):
        orchestrator.checkpoints.publish(node, checkpoint_inputs, node.output_paths)
    return report_ref


def require_current_citation_report(
    lifecycle: ResearchArtifactLifecycle,
    attestations: ProtectedCitationAttestations,
) -> ArtifactRef:
    """Verify the sealed report, its exact inputs, and current accepted payload."""
    workspace = lifecycle.workspace
    if not (workspace / REPORT_PATH).exists():
        raise ValueError("a current passing citation integrity report is required")
    artifact = lifecycle.read_artifact(REPORT_PATH)
    if artifact.envelope.validation_status is not ArtifactLifecycle.VALIDATED:
        raise ValueError("citation integrity report is not currently validated")
    if artifact.envelope.producer.component != "citation-integrity-validator":
        raise ValueError("citation integrity report producer is not trusted")
    report = report_from_payload(artifact.payload)
    if (
        not report.passed
        or report.findings
        or report.validator_version != "claim-integrity-v1"
        or not report_binding_is_valid(report)
    ):
        raise ValueError("citation integrity report binding is invalid")
    if artifact.envelope.input_artifacts != report_input_refs(report):
        raise ValueError("citation integrity report inputs are not current")
    report_ref = lifecycle.artifact_ref(REPORT_PATH)
    attestations.require_current_report(report_ref, report)
    plan_ref = lifecycle.validated_history_ref(Path("artifacts/analysis-plan.yaml"))
    if report.accepted_artifact_refs != (plan_ref,):
        raise ValueError("citation integrity report does not bind the current plan")
    plan = lifecycle.read_artifact(Path("artifacts/analysis-plan.yaml"))
    if report.accepted_artifact_bindings[0].payload_leaf_hashes != payload_leaf_hashes(
        plan.payload
    ):
        raise ValueError("citation integrity report payload binding is not current")
    return report_ref


def citation_node_inputs(
    lifecycle: ResearchArtifactLifecycle,
) -> tuple[ArtifactRef, ...]:
    """Bind validation to the reviewed plan generation across approval promotion."""
    return (lifecycle.validated_history_ref(Path("artifacts/analysis-plan.yaml")),)


def _retire_report(
    orchestrator: ResearchOrchestrator,
    source_generation: SourceGenerationAnchor,
) -> None:
    """Invalidate the old citation node before publishing a newer source binding."""
    completed = orchestrator.checkpoints.completed_nodes(orchestrator.graph)
    if "validate-citations" in completed:
        orchestrator.checkpoints.invalidate(
            orchestrator.graph,
            "validate-citations",
            reason=f"citation source generation {source_generation.generation}",
        )
    orchestrator.lifecycle.supersede(
        REPORT_PATH,
        revision_id=f"citation-source-{source_generation.generation}",
        reason="registered citation source generation changed",
        actor="citation-integrity-validator",
    )
