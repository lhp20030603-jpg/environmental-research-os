"""Strict scoring and release gates for independently sealed blind reviews."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from envresearch.benchmarks.blind_release import (
    CANONICAL_METHOD_FAMILIES,
    CaseForRelease,
    ReleaseCohort,
    ReleaseEvaluator,
    ReleaseReadinessReport,
)
from envresearch.benchmarks.blind_scoring_contracts import (
    AdjudicationRecord,
    CaseEvaluation,
    DimensionMean,
    LockedThirdScore,
    SealedScoreArtifact,
)
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_evaluation import (
    EXPERT_WEIGHTS,
    ExpertDimension,
)
from envresearch.models.principal import PrincipalKind

if TYPE_CHECKING:
    from envresearch.benchmarks.blind_artifacts import BlindArtifactLifecycle

__all__ = ["CANONICAL_METHOD_FAMILIES", "CORE_DIMENSIONS", "AdjudicationRecord", "BlindScorer", "CaseEvaluation", "CaseForRelease", "DimensionMean", "LockedThirdScore", "ReleaseCohort", "ReleaseEvaluator", "ReleaseReadinessReport", "SealedScoreArtifact", "requires_adjudication"]

CORE_DIMENSIONS = frozenset(
    {
        ExpertDimension.IDENTIFICATION_FIT,
        ExpertDimension.ASSUMPTIONS_THREATS,
        ExpertDimension.DATA_COMPATIBILITY,
    }
)
_PASS_THRESHOLD = Decimal("3.0")


def _score_map(record: SealedScoreArtifact) -> dict[ExpertDimension, Decimal]:
    return {item.dimension: Decimal(item.score) for item in record.score_sheet.scores}


def requires_adjudication(
    first: SealedScoreArtifact, second: SealedScoreArtifact
) -> bool:
    """Apply the approved verdict, Critical, and core-gap trigger policy."""
    one, two = first.score_sheet, second.score_sheet
    if one.verdict != two.verdict or one.critical_findings or two.critical_findings:
        return True
    first_scores, second_scores = _score_map(first), _score_map(second)
    return any(
        abs(first_scores[dimension] - second_scores[dimension]) > 1
        for dimension in CORE_DIMENSIONS
    )


class BlindScorer:
    """Score only evidence reconstructed from current authenticated state."""

    def __init__(self, artifacts: BlindArtifactLifecycle, case_id: str) -> None:
        from envresearch.benchmarks.blind_scoring_evidence import (
            BlindScoringEvidenceReader,
        )

        self._reader = BlindScoringEvidenceReader(artifacts, case_id)

    @classmethod
    def from_case(
        cls, artifacts: BlindArtifactLifecycle, case_id: str
    ) -> BlindScorer:
        return cls(artifacts, case_id)

    def evaluate_case(self) -> CaseEvaluation:
        expert_one, expert_two = self._reader.expert_scores()
        triggered = requires_adjudication(expert_one, expert_two)
        adjudication = (
            self._reader.adjudication(expert_one, expert_two)
            if triggered or self._reader.has_final_state()
            else None
        )
        return _evaluate_verified(expert_one, expert_two, adjudication)


def _evaluate_verified(
    expert_one: SealedScoreArtifact,
    expert_two: SealedScoreArtifact,
    adjudication: AdjudicationRecord | None = None,
) -> CaseEvaluation:
    expert_one = SealedScoreArtifact.model_validate(expert_one.model_dump())
    expert_two = SealedScoreArtifact.model_validate(expert_two.model_dump())
    if adjudication is not None:
        adjudication = AdjudicationRecord.model_validate(adjudication.model_dump())
    _validate_experts(expert_one, expert_two)
    triggered = requires_adjudication(expert_one, expert_two)
    if triggered and adjudication is None:
        raise ValueError("adjudication is required for this disagreement")
    if not triggered and adjudication is not None:
        raise ValueError("adjudication is not allowed without a disagreement trigger")
    if adjudication is None:
        records = (expert_one, expert_two)
        return _evaluation(
            expert_one.score_sheet.recommendation_ref,
            records,
            _dimension_means(records),
            all(item.score_sheet.verdict == "pass" for item in records),
            False,
        )
    _validate_adjudication(expert_one, expert_two, adjudication)
    adjudicated_records = (expert_one, expert_two, adjudication.third_score.score)
    third = adjudication.third_score.score.score_sheet
    return _evaluation(
        expert_one.score_sheet.recommendation_ref,
        adjudicated_records,
        _dimension_means((adjudication.third_score.score,)),
        adjudication.verdict.verdict == "accept" and third.verdict == "pass",
        True,
        adjudication,
    )

def _validate_experts(
    first: SealedScoreArtifact, second: SealedScoreArtifact
) -> None:
    if first.case_id != second.case_id:
        raise ValueError("expert case IDs must match")
    if first.score_sheet_ref == second.score_sheet_ref:
        raise ValueError("expert score refs must be distinct")
    if (first.principal_assignment.kind is not PrincipalKind.EXPERT
            or second.principal_assignment.kind is not PrincipalKind.EXPERT):
        raise ValueError("dual scores require expert assignments")
    if first.score_sheet.scorer_principal == second.score_sheet.scorer_principal:
        raise ValueError("distinct expert principals are required")
    if first.score_sheet.recommendation_ref != second.score_sheet.recommendation_ref:
        raise ValueError("expert recommendation refs must match")

def _validate_adjudication(
    first: SealedScoreArtifact,
    second: SealedScoreArtifact,
    adjudication: AdjudicationRecord,
) -> None:
    third = adjudication.third_score.score
    if third.case_id != first.case_id:
        raise ValueError("third score case ID must match the expert case")
    if third.score_sheet.recommendation_ref != first.score_sheet.recommendation_ref:
        raise ValueError("adjudication recommendation ref must match the expert case")
    if third.score_sheet.scorer_principal in {
        first.score_sheet.scorer_principal,
        second.score_sheet.scorer_principal,
    }:
        raise ValueError("adjudicator principal must be distinct from both experts")
    if adjudication.verdict.adjudicator_principal != third.score_sheet.scorer_principal:
        raise ValueError("adjudicator principal must match the locked third score")
    expected = (first.score_sheet.recommendation_ref, first.score_sheet_ref,
                second.score_sheet_ref, third.score_sheet_ref)
    if adjudication.final_order_inputs != expected:
        raise ValueError("final adjudication order must bind the locked third score")
    if adjudication.verdict.score_sheet_ref not in {
        first.score_sheet_ref,
        second.score_sheet_ref,
    }:
        raise ValueError("adjudication verdict must reference an original expert score")


def _dimension_means(
    records: tuple[SealedScoreArtifact, ...],
) -> tuple[DimensionMean, ...]:
    count = Decimal(len(records))
    return tuple(
        DimensionMean(
            dimension=dimension,
            score=sum((_score_map(item)[dimension] for item in records), Decimal(0))
            / count,
        )
        for dimension in ExpertDimension
    )


def _evaluation(
    recommendation_ref: ArtifactRef,
    records: tuple[SealedScoreArtifact, ...],
    means: tuple[DimensionMean, ...],
    passed: bool,
    requires_adjudication: bool,
    adjudication: AdjudicationRecord | None = None,
) -> CaseEvaluation:
    mean_map = {item.dimension: item.score for item in means}
    weighted_score = sum(
        (mean_map[dimension] * EXPERT_WEIGHTS[dimension] for dimension in ExpertDimension),
        Decimal(0),
    )
    return CaseEvaluation(
        recommendation_ref=recommendation_ref,
        original_score_artifacts=records,
        dimension_scores=means,
        weighted_score=weighted_score,
        passed=passed and weighted_score >= _PASS_THRESHOLD,
        requires_adjudication=requires_adjudication,
        adjudication=adjudication,
    )
