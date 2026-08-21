"""Minimum-score behavior for blind review and release aggregation."""

from __future__ import annotations

from decimal import Decimal

from test_blind_scoring import (
    locked_adjudication,
    release_cases,
    score_sheet,
)

from envresearch.benchmarks.blind_release import ReleaseEvaluator
from envresearch.benchmarks.blind_scoring import _evaluate_verified
from envresearch.benchmarks.blind_scoring_contracts import LockedThirdScore


def test_non_adjudicated_subthreshold_agreement_does_not_pass() -> None:
    """Removing the threshold would let weak-but-valid PASS verdicts pass."""
    first = score_sheet(3, 1, 1, 1, 1)
    second = score_sheet(3, 1, 1, 1, 1, principal="expert-two")

    result = _evaluate_verified(first, second)

    assert result.weighted_score == Decimal("1.60")
    assert result.passed is False


def test_adjudicated_subthreshold_acceptance_does_not_pass() -> None:
    """An accepted third review still needs a score of at least 3.0."""
    first = score_sheet(3, 3, 3, 3, 3)
    second = score_sheet(3, 3, 3, 3, 3, principal="expert-two", verdict="fail")
    third = score_sheet(
        3,
        1,
        1,
        1,
        1,
        principal="adjudicator-one",
        recommendation=first.score_sheet.recommendation_ref,
        adjudicator=True,
    )
    adjudication = locked_adjudication(first, second).model_copy(
        update={
            "third_score": LockedThirdScore(score=third),
            "final_order_inputs": (
                first.score_sheet.recommendation_ref,
                first.score_sheet_ref,
                second.score_sheet_ref,
                third.score_sheet_ref,
            ),
        }
    )

    result = _evaluate_verified(first, second, adjudication)

    assert result.weighted_score == Decimal("1.60")
    assert result.passed is False


def test_release_does_not_count_subthreshold_case_with_stale_pass_flag() -> None:
    """Removing release-side validation would trust a stale stored pass flag."""
    cases = list(release_cases(passing=16))
    cases[0] = cases[0].model_copy(
        update={
            "evaluation": cases[0].evaluation.model_copy(
                update={"weighted_score": Decimal("2.90"), "passed": True}
            )
        }
    )

    report = ReleaseEvaluator().evaluate(tuple(cases))

    assert report.passed_cases == 15
    assert "family did_event_study mean must be at least 3.0" in report.blockers
