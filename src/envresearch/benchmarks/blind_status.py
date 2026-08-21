"""Lifecycle-derived status for authenticated blind benchmark artifacts."""

from __future__ import annotations

from pathlib import Path

from envresearch.benchmarks.blind_artifacts import BlindArtifactLifecycle
from envresearch.benchmarks.blind_scoring import BlindScorer
from envresearch.benchmarks.blind_scoring_contracts import StrictScoringModel
from envresearch.models.benchmark_evaluation import ExpertScoreSheet
from envresearch.research.order_policy import (
    blind_scores_require_adjudication,
    blind_workflow_status,
    build_blind_graph,
)


class BlindCaseStatus(StrictScoringModel):
    case_id: str
    completed_nodes: tuple[str, ...]
    stale_nodes: tuple[str, ...]
    current_lineage: bool
    adjudication_required: bool
    third_score_locked: bool
    adjudication_completed: bool
    unresolved: bool
    gate_failures: tuple[str, ...]


def case_status(artifacts: BlindArtifactLifecycle, case_id: str) -> BlindCaseStatus:
    paths = artifacts.paths(case_id)
    third_locked = False
    gates: list[str] = []
    required = (
        (paths.source_sheet, "curator source sheet is required"),
        (paths.blinded_brief, "blinded brief is required"),
        (paths.claim_fact_map, "claim fact map is required"),
        (paths.leakage_report, "passing leakage report is required"),
        (paths.recommendation, "method recommendation is required"),
        (paths.citation_report, "citation integrity report is required"),
        (paths.expert_one, "expert score 1 is required"),
        (paths.expert_two, "expert score 2 is required"),
    )
    for path, message in required:
        if not _artifact_is_current(artifacts, path):
            gates.append(message)
    scores_ready = all(
        _artifact_is_current(artifacts, path)
        for path in (paths.expert_one, paths.expert_two)
    )
    adjudication_required = False
    adjudication_completed = False
    if scores_ready:
        artifacts.lineage.locked_score_refs(case_id)
        first, second = (
            artifacts.lifecycle.read_payload(path, ExpertScoreSheet)
            for path in (paths.expert_one, paths.expert_two)
        )
        adjudication_required = blind_scores_require_adjudication(first, second)
        final_current = _artifact_is_current(artifacts, paths.adjudication)
        if adjudication_required:
            if not _artifact_is_current(artifacts, paths.third_score):
                gates.append("blind third score is required")
            else:
                artifacts.lineage.locked_third_score_ref(case_id)
                third_locked = True
            if not final_current or not third_locked:
                gates.append("final adjudication is required")
            else:
                BlindScorer.from_case(artifacts, case_id).evaluate_case()
                adjudication_completed = True
        elif final_current:
            BlindScorer.from_case(artifacts, case_id).evaluate_case()
    if not _artifact_is_current(artifacts, paths.posthoc_comparison):
        gates.append("post-hoc comparison is required")
    workflow = blind_workflow_status(
        build_blind_graph(case_id),
        artifacts,
        case_id,
        third_score_locked=third_locked,
    )
    gates.extend(f"workflow node {node} is stale" for node in workflow.stale_nodes)
    return BlindCaseStatus(
        case_id=workflow.case_id,
        completed_nodes=workflow.completed_nodes,
        stale_nodes=workflow.stale_nodes,
        current_lineage=workflow.current_lineage,
        adjudication_required=adjudication_required,
        third_score_locked=third_locked,
        adjudication_completed=adjudication_completed,
        unresolved=bool(gates) or not workflow.current_lineage,
        gate_failures=tuple(gates),
    )


def _artifact_is_current(artifacts: BlindArtifactLifecycle, path: Path) -> bool:
    try:
        return (
            artifacts.lifecycle.current_envelope(path).validation_status.value
            == "validated"
        )
    except FileNotFoundError:
        return False
