"""Tests for strict blind recommendation and release evaluation contracts."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_evaluation import (
    CaseEvaluation,
    CriticalMethodFinding,
    DimensionScore,
    ExpertDimension,
    ExpertScoreSheet,
    MethodRecommendationPayload,
)
from envresearch.models.design import (
    MethodCandidate,
    MethodCandidateRole,
    MethodCandidatesPayload,
)

SHA256 = "a" * 64


def artifact_ref(artifact_id: str) -> ArtifactRef:
    """Return an immutable artifact reference for evaluation contract tests."""
    return ArtifactRef(
        artifact_id=artifact_id,
        artifact_version=1,
        content_hash=SHA256,
    )


def method_candidates() -> MethodCandidatesPayload:
    """Return a complete ranked candidate set for recommendation tests."""
    primary = MethodCandidate(
        method_profile_ref="did-profile-v1",
        role=MethodCandidateRole.PRIMARY,
        rank=1,
        estimand_compatible=True,
        required_assumption_refs=("parallel-trends",),
        required_data_structure=("panel",),
        diagnostics=("event study",),
        fallback_or_limitations=("Use synthetic control if appropriate.",),
    )
    alternative = primary.model_copy(
        update={
            "method_profile_ref": "matching-profile-v1",
            "role": MethodCandidateRole.ALTERNATIVE,
            "rank": 2,
        }
    )
    return MethodCandidatesPayload(estimand_ref="estimand-001", candidates=(primary, alternative))


def recommendation_payload() -> dict[str, object]:
    """Return a recommendation with evidence links separate from reasoning."""
    return {
        "blinded_brief_ref": artifact_ref("blinded-brief"),
        "leakage_report_ref": artifact_ref("leakage-report"),
        "method_profile_registry_sha256": SHA256,
        "estimand_interpretation": "Estimate the policy assignment effect.",
        "method_candidates": method_candidates(),
        "fact_refs": ("fact-001",),
        "diagnostics": ("Check balance across assignment groups",),
        "falsification_tests": ("Test a placebo assignment date",),
        "robustness_plan": ("Vary the eligible sample definition",),
        "data_gaps": ("Confirm follow-up coverage",),
        "decision_boundaries": ("Do not infer beyond eligible units",),
        "recommender_principal": "recommender-001",
    }


def expert_scores() -> tuple[DimensionScore, ...]:
    """Return one score for each required expert-review dimension."""
    return tuple(
        DimensionScore(dimension=dimension, score=3, rationale="Supported.")
        for dimension in ExpertDimension
    )


def expert_sheet(*, scores: tuple[DimensionScore, ...]) -> ExpertScoreSheet:
    """Build a blind score sheet from the supplied rubric dimension scores."""
    return ExpertScoreSheet(
        recommendation_ref=artifact_ref("recommendation"),
        scores=scores,
        critical_findings=(),
        verdict="pass",
        scorer_principal="expert-001",
    )


def test_score_sheet_requires_all_five_dimensions_once() -> None:
    """A persisted score cannot omit or duplicate any rubric dimension."""
    scores = expert_scores()[:-1]

    with pytest.raises(ValidationError, match="exactly the five expert dimensions"):
        expert_sheet(scores=scores)


def test_recommendation_forbids_undeclared_fact_ids_in_analytical_prose() -> None:
    """An undeclared fact ID cannot enter blind analytical prose."""
    payload = recommendation_payload()
    payload["diagnostics"] = ("Check balance using fact-undisclosed",)

    with pytest.raises(ValidationError, match="fact IDs only in fact_refs"):
        MethodRecommendationPayload.model_validate(payload)


def test_recommendation_forbids_declared_fact_ids_in_analytical_prose() -> None:
    """Fact IDs belong only in fact_refs, never in analytical prose fields."""
    payload = recommendation_payload()
    payload["diagnostics"] = ("Check balance using fact-001",)

    with pytest.raises(ValidationError, match="fact IDs only in fact_refs"):
        MethodRecommendationPayload.model_validate(payload)


def test_score_sheet_uses_decimal_weights_and_blocks_critical_passes() -> None:
    """Weighted blind scores remain exact and cannot pass with critical defects."""
    sheet = expert_sheet(scores=expert_scores())
    assert sheet.weighted_score() == Decimal("3.00")

    with pytest.raises(ValidationError, match="PASS score sheet cannot contain critical"):
        ExpertScoreSheet(
            recommendation_ref=artifact_ref("recommendation"),
            scores=expert_scores(),
            critical_findings=(
                CriticalMethodFinding(
                    finding_id="critical-001",
                    severity="critical",
                    description="Identification is invalid.",
                ),
            ),
            verdict="pass",
            scorer_principal="expert-001",
        )


def case_evaluation_payload() -> dict[str, object]:
    """Return a case bundle with both independently authored score sheets."""
    return {
        "case_id": "pilot-001",
        "recommendation_ref": artifact_ref("recommendation"),
        "expert_score_sheet_refs": (
            artifact_ref("expert-score-sheet-a"),
            artifact_ref("expert-score-sheet-b"),
        ),
        "adjudication_ref": artifact_ref("adjudication"),
        "citation_integrity_report_ref": artifact_ref("citation-report"),
    }


def test_case_evaluation_requires_two_unique_expert_score_sheets() -> None:
    """A case keeps both original experts' sheets for auditable adjudication."""
    payload = case_evaluation_payload()
    evaluation = CaseEvaluation.model_validate(payload)
    assert len(evaluation.expert_score_sheet_refs) == 2
    assert evaluation.adjudication_ref == artifact_ref("adjudication")

    for refs in (
        (artifact_ref("expert-score-sheet-a"),),
        (
            artifact_ref("expert-score-sheet-a"),
            artifact_ref("expert-score-sheet-b"),
            artifact_ref("expert-score-sheet-c"),
        ),
        (artifact_ref("expert-score-sheet-a"), artifact_ref("expert-score-sheet-a")),
    ):
        with pytest.raises(
            ValidationError, match="exactly two unique expert score sheet refs"
        ):
            CaseEvaluation.model_validate({**payload, "expert_score_sheet_refs": refs})
