"""Behavioral tests for independent blind-score aggregation and release gates."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from envresearch.benchmarks.blind_scoring import (
    CANONICAL_METHOD_FAMILIES,
    AdjudicationRecord,
    CaseForRelease,
    LockedThirdScore,
    ReleaseCohort,
    ReleaseEvaluator,
    SealedScoreArtifact,
    _evaluate_verified,
)
from envresearch.models.artifact import (
    ArtifactEnvelope,
    ArtifactRef,
    ProducerIdentity,
    ResearchArtifact,
    seal_artifact,
)
from envresearch.models.benchmark_evaluation import (
    AdjudicationVerdict,
    CriticalMethodFinding,
    DimensionScore,
    ExpertDimension,
    ExpertScoreSheet,
)
from envresearch.models.enums import ArtifactLifecycle
from envresearch.models.principal import (
    PrincipalAssignment,
    PrincipalKind,
    PrincipalVerification,
)

SHA256 = "a" * 64


def artifact_ref(artifact_id: str) -> ArtifactRef:
    """Create an immutable ref suitable for a scoring fixture."""
    return ArtifactRef(artifact_id=artifact_id, artifact_version=1, content_hash=SHA256)


def sealed_ref(artifact: ResearchArtifact[object]) -> ArtifactRef:
    """Return the content-addressed reference for a sealed fixture artifact."""
    assert artifact.envelope.content_hash is not None
    return ArtifactRef(
        artifact_id=artifact.envelope.artifact_id,
        artifact_version=artifact.envelope.artifact_version,
        content_hash=artifact.envelope.content_hash,
    )


def _raw_score_sheet(
    *scores: int,
    principal: str = "expert-one",
    recommendation: ArtifactRef | None = None,
    verdict: str = "pass",
    critical: bool = False,
) -> ExpertScoreSheet:
    """Build a complete independent expert sheet with hand-specified scores."""
    return ExpertScoreSheet(
        recommendation_ref=recommendation or artifact_ref("recommendation-001"),
        scores=tuple(
            DimensionScore(dimension=dimension, score=score, rationale="Reviewed.")
            for dimension, score in zip(ExpertDimension, scores, strict=True)
        ),
        critical_findings=(
            CriticalMethodFinding(
                finding_id="critical-001",
                description="A Critical method defect remains.",
            ),
        )
        if critical
        else (),
        verdict=verdict,  # type: ignore[arg-type]
        scorer_principal=principal,
    )


def score_sheet(
    *scores: int,
    principal: str = "expert-one",
    recommendation: ArtifactRef | None = None,
    verdict: str = "pass",
    critical: bool = False,
    case_id: str = "case-001",
    adjudicator: bool = False,
) -> SealedScoreArtifact:
    """Seal one exact score payload as a current authenticated submission."""
    payload = _raw_score_sheet(
        *scores,
        principal=principal,
        recommendation=recommendation,
        verdict=verdict,
        critical=critical,
    )
    kind = PrincipalKind.ADJUDICATOR if adjudicator else PrincipalKind.EXPERT
    order_id = "adjudicator-score" if adjudicator else f"expert-score-{principal}"
    producer = ProducerIdentity(component=principal, version="1")
    assignment = PrincipalAssignment(
        assignment_id=f"assignment-{principal}",
        principal_id=principal,
        kind=kind,
        producer=producer,
        verification=PrincipalVerification.PUBLIC_KEY_SIGNATURE,
        key_id=f"key-{principal}",
        public_key_sha256="b" * 64,
    )
    queue_inputs = (artifact_ref("blinded-brief-001"), payload.recommendation_ref)
    signed_ref = artifact_ref(f"signed-{case_id}-{principal}")
    artifact = seal_artifact(
        ResearchArtifact(
            envelope=ArtifactEnvelope(
                artifact_id=f"{order_id}-{case_id}",
                artifact_version=1,
                run_id=case_id,
                created_at=datetime.now(UTC),
                producer=producer,
                input_artifacts=(*queue_inputs, signed_ref),
                validation_status=ArtifactLifecycle.VALIDATED,
            ),
            payload=payload,
        )
    )
    ref = sealed_ref(artifact)
    return SealedScoreArtifact(
        case_id=case_id,
        score_sheet_ref=ref,
        artifact=artifact,
        current_ref=ref,
        validated_history_ref=ref,
        principal_assignment=assignment,
        queue_order_id=order_id,
        queue_input_artifacts=queue_inputs,
        signed_evidence_ref=signed_ref,
    )


def locked_adjudication(
    first: ExpertScoreSheet,
    second: ExpertScoreSheet,
    *,
    locked: bool = True,
    verdict: str = "accept",
    principal: str = "adjudicator-one",
) -> AdjudicationRecord:
    """Return a third independent score and an adjudication tied to the case."""
    third = score_sheet(
        3,
        3,
        3,
        3,
        3,
        principal=principal,
        recommendation=first.recommendation_ref,
        adjudicator=True,
    )
    return AdjudicationRecord(
        third_score=LockedThirdScore(score=third),
        final_order_inputs=(
            first.recommendation_ref,
            first.score_sheet_ref,
            second.score_sheet_ref,
            third.score_sheet_ref,
        ),
        verdict_ref=artifact_ref("adjudication-verdict"),
        signed_verdict_evidence_ref=artifact_ref("signed-adjudication-verdict"),
        verdict=AdjudicationVerdict(
            score_sheet_ref=first.score_sheet_ref,
            verdict=verdict,  # type: ignore[arg-type]
            rationale="The independently locked third review resolves the dispute.",
            adjudicator_principal=principal,
        ),
    )


def test_two_passing_close_scores_use_decimal_dimension_means() -> None:
    """Averaging a wrong dimension or using float arithmetic changes this result."""
    first = score_sheet(3, 3, 3, 3, 3)
    second = score_sheet(4, 3, 3, 3, 3, principal="expert-two")

    result = _evaluate_verified(first, second)

    assert result.requires_adjudication is False
    assert result.dimension_means[ExpertDimension.IDENTIFICATION_FIT] == Decimal("3.5")
    assert result.weighted_score == Decimal("3.15")
    assert result.passed is True
    assert result.original_score_sheets == (first.score_sheet, second.score_sheet)


@pytest.mark.parametrize("reason", ("verdict", "core_gap", "critical"))
def test_disagreement_requires_a_third_adjudicator(reason: str) -> None:
    """Removing any approved disagreement trigger must block case completion."""
    first = score_sheet(3, 3, 3, 3, 3)
    if reason == "verdict":
        second = score_sheet(3, 3, 3, 3, 3, principal="expert-two", verdict="fail")
    elif reason == "core_gap":
        second = score_sheet(1, 3, 3, 3, 3, principal="expert-two", verdict="fail")
    else:
        second = score_sheet(
            3, 3, 3, 3, 3, principal="expert-two", verdict="fail", critical=True
        )

    with pytest.raises(ValueError, match="adjudication is required"):
        _evaluate_verified(first, second)


def test_adjudication_requires_locked_third_score_and_retains_all_originals() -> None:
    """Accepting an unlocked third score would let comparison influence that score."""
    first = score_sheet(3, 3, 3, 3, 3)
    second = score_sheet(1, 3, 3, 3, 3, principal="expert-two", verdict="fail")
    adjudication = locked_adjudication(first, second)

    result = _evaluate_verified(first, second, adjudication)

    assert result.passed is True
    assert result.weighted_score == Decimal("3.00")
    assert result.original_score_sheets == (
        first.score_sheet,
        second.score_sheet,
        adjudication.third_score.score.score_sheet,
    )


def test_dual_expert_principals_and_recommendation_must_be_distinct_and_match() -> None:
    """Reusing a reviewer or cross-case recommendation defeats independent review."""
    first = score_sheet(3, 3, 3, 3, 3)

    with pytest.raises(ValueError, match="distinct expert principals"):
        _evaluate_verified(first, score_sheet(3, 3, 3, 3, 3))
    with pytest.raises(ValueError, match="recommendation refs must match"):
        _evaluate_verified(
            first,
            score_sheet(
                3,
                3,
                3,
                3,
                3,
                principal="expert-two",
                recommendation=artifact_ref("recommendation-002"),
            ),
        )


def test_adjudication_rejects_fake_or_mismatched_final_record() -> None:
    """A final adjudication must be from the locked third reviewer for this case."""
    first = score_sheet(3, 3, 3, 3, 3)
    second = score_sheet(1, 3, 3, 3, 3, principal="expert-two", verdict="fail")
    adjudication = locked_adjudication(first, second)

    with pytest.raises(ValueError, match="adjudicator principal"):
        _evaluate_verified(
            first,
            second,
                adjudication.model_copy(
                    update={"verdict": adjudication.verdict.model_copy(
                        update={"adjudicator_principal": "forged-adjudicator"}
                    )},
                ),
        )
    with pytest.raises(ValueError, match="original expert score"):
        _evaluate_verified(
            first,
            second,
                adjudication.model_copy(
                    update={"verdict": adjudication.verdict.model_copy(
                        update={"score_sheet_ref": artifact_ref("foreign-score-sheet")}
                    )},
                ),
        )


def released_case(
    case_id: str,
    family: str,
    *,
    passed: bool,
    cohort: ReleaseCohort = ReleaseCohort.HELD_OUT,
) -> CaseForRelease:
    """Create a release input with a real completed independent review."""
    verdict = "pass" if passed else "fail"
    first = score_sheet(
        3,
        3,
        3,
        3,
        3,
        recommendation=artifact_ref(f"recommendation-{case_id}"),
        verdict=verdict,
        case_id=case_id,
    )
    second = score_sheet(
        3,
        3,
        3,
        3,
        3,
        principal=f"expert-{case_id}",
        recommendation=first.recommendation_ref,
        verdict=verdict,
        case_id=case_id,
    )
    return CaseForRelease(
        case_id=case_id,
        method_family=family,
        recommendation_ref=first.recommendation_ref,
        evaluation=_evaluate_verified(first, second),
        cohort=cohort,
        leakage_passed=True,
        citation_passed=True,
        unresolved=False,
    )


def release_cases(*, passing: int = 14) -> tuple[CaseForRelease, ...]:
    """Build sixteen held-out cases, two in each of eight method families."""
    failed_indexes = set(range(2, 2 + (16 - passing) * 2, 2))
    return tuple(
        released_case(
            f"case-{index:02d}",
            tuple(sorted(CANONICAL_METHOD_FAMILIES))[(index - 1) // 2],
            passed=index not in failed_indexes,
        )
        for index in range(1, 17)
    )


def test_release_requires_held_out_family_coverage_and_fourteen_passes() -> None:
    """Caller-built cases cannot substitute for authority-enrolled disk evidence."""
    report = ReleaseEvaluator().evaluate(release_cases())

    assert report.released is False
    assert "verified authority enrollment is required" in report.blockers
    assert report.passed_cases == 14
    assert report.held_out_cases == 16

    blocked = ReleaseEvaluator().evaluate(release_cases(passing=13))
    assert blocked.released is False
    assert "requires at least 14 passed cases" in blocked.blockers


@pytest.mark.parametrize("field", ("leakage_passed", "citation_passed", "unresolved"))
def test_release_fails_closed_for_integrity_or_unresolved_case(field: str) -> None:
    """Any unresolved leakage or citation condition blocks the entire release."""
    cases = list(release_cases())
    cases[0] = cases[0].model_copy(update={field: field == "unresolved"})

    assert ReleaseEvaluator().evaluate(tuple(cases)).released is False


def test_pilot_calibration_and_duplicate_or_mismatched_cases_cannot_release() -> None:
    """Calibration cases, duplicate IDs, and cross-case refs must never be released."""
    cases = list(release_cases())
    cases[0] = cases[0].model_copy(update={"cohort": ReleaseCohort.PILOT})
    assert ReleaseEvaluator().evaluate(tuple(cases)).released is False

    duplicate = (*release_cases()[:-1], release_cases()[0])
    with pytest.raises(ValueError, match="duplicate case IDs"):
        ReleaseEvaluator().evaluate(duplicate)

    mismatched = release_cases()[0].model_copy(
        update={"recommendation_ref": artifact_ref("foreign")}
    )
    with pytest.raises(ValueError, match="recommendation ref"):
        ReleaseEvaluator().evaluate((mismatched, *release_cases()[1:]))


def test_release_rejects_one_family_and_missing_or_extra_canonical_families() -> None:
    one_family = tuple(
        case.model_copy(update={"method_family": "rct"}) for case in release_cases()
    )
    with pytest.raises(ValueError, match="canonical method families"):
        ReleaseEvaluator().evaluate(one_family)

    missing = release_cases()[:-2]
    with pytest.raises(ValueError, match="canonical method families"):
        ReleaseEvaluator().evaluate(missing)

    extra = (
        *release_cases()[:-1],
        release_cases()[-1].model_copy(update={"method_family": "extra"}),
    )
    with pytest.raises(ValueError, match="canonical method families"):
        ReleaseEvaluator().evaluate(extra)
    assert len(CANONICAL_METHOD_FAMILIES) == 8
def test_score_artifact_rejects_forged_refs_or_unlocked_provenance() -> None:
    first = score_sheet(3, 3, 3, 3, 3)
    with pytest.raises(Exception, match="Extra inputs are not permitted"):
        SealedScoreArtifact(score_sheet=first, fake_lock=True)  # type: ignore[call-arg]
    forged = first.model_copy(update={"score_sheet_ref": artifact_ref("forged-score")})
    with pytest.raises(ValueError, match="score refs must match"):
        _evaluate_verified(forged, score_sheet(3, 3, 3, 3, 3, principal="two"))


def test_unrequested_adjudication_is_rejected_after_exact_core_gap_boundary() -> None:
    first = score_sheet(3, 3, 3, 3, 3)
    second = score_sheet(4, 3, 3, 3, 3, principal="expert-two")
    with pytest.raises(ValueError, match="not allowed"):
        _evaluate_verified(first, second, locked_adjudication(first, second))
