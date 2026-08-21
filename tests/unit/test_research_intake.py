"""Tests for strict research-intake payloads and charter ranking."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from envresearch.models.intake import (
    CandidateCharter,
    CandidateChartersPayload,
    CharterScore,
    DistinctnessClaim,
    ResearchBriefPayload,
    ResearchCharterPayload,
    ScoreUncertainty,
)
from envresearch.research.ranking import CharterRanker, CharterRankingPolicy

SCORE_FIELDS = (
    "contribution_potential",
    "literature_gap",
    "data_feasibility",
    "identification_plausibility",
    "policy_relevance",
    "scope_manageability",
)


def _claims(candidate_id: str) -> tuple[DistinctnessClaim, DistinctnessClaim]:
    """Return declarations against the two other canonical test candidates."""
    targets = tuple(item for item in ("air", "water", "soil") if item != candidate_id)
    return tuple(
        DistinctnessClaim(
            other_candidate_id=target,
            different_exposure_or_policy=True,
            different_outcome_or_mechanism=False,
            explanation=f"{candidate_id} studies a different policy from {target}.",
        )
        for target in targets
    )  # type: ignore[return-value]


def candidate(candidate_id: str, score: int) -> CandidateCharter:
    """Build one complete candidate for ranker tests."""
    questions = {
        "air": "Does clean air improve health?",
        "water": "Does water pricing change household conservation?",
        "soil": "Do soil subsidies change farm erosion?",
    }
    return CandidateCharter(
        candidate_id=candidate_id,
        research_question=questions[candidate_id],
        scores={
            field: CharterScore(
                score=score,
                evidence_refs=(f"evidence:{candidate_id}:{field}",),
                uncertainty=ScoreUncertainty.MEDIUM,
            )
            for field in SCORE_FIELDS
        },
        distinctness_claims=_claims(candidate_id),
    )


def test_ranker_requires_three_distinct_candidates_and_equal_default_weights() -> None:
    """Three valid candidates receive transparent equal-weight ordering."""
    ranker = CharterRanker(CharterRankingPolicy())

    ranked = ranker.rank((candidate("air", 80), candidate("water", 70), candidate("soil", 60)))

    assert [item.candidate_id for item in ranked] == ["air", "water", "soil"]
    assert ranked[0].total_score == 80.0
    assert CharterRankingPolicy().weights == {field: 1 / 6 for field in SCORE_FIELDS}


def test_ranker_blocks_reworded_duplicates() -> None:
    """Question fingerprints reject only presentation-normalized duplicates."""
    duplicate = candidate("water", 70).model_copy(
        update={
            "candidate_id": "air-2",
            "research_question": "  does   clean air improve health? ",
            "distinctness_claims": (
                DistinctnessClaim(
                    other_candidate_id="air",
                    different_exposure_or_policy=True,
                    different_outcome_or_mechanism=False,
                    explanation="The policy differs.",
                ),
                DistinctnessClaim(
                    other_candidate_id="soil",
                    different_exposure_or_policy=True,
                    different_outcome_or_mechanism=False,
                    explanation="The policy differs.",
                ),
            ),
        }
    )

    with pytest.raises(ValueError, match="three substantively distinct"):
        CharterRanker(CharterRankingPolicy()).rank(
            (candidate("air", 80), duplicate, candidate("soil", 60))
        )


def test_ranker_recomputes_scores_and_breaks_ties_by_candidate_id() -> None:
    """Caller-provided totals cannot override deterministic weighted totals."""
    air = candidate("air", 80).model_copy(update={"total_score": 0.0})
    water = candidate("water", 80).model_copy(update={"total_score": 100.0})

    ranked = CharterRanker(CharterRankingPolicy()).rank(
        (water, candidate("soil", 60), air)
    )

    assert [(item.candidate_id, item.total_score) for item in ranked] == [
        ("air", 80.0),
        ("water", 80.0),
        ("soil", 60.0),
    ]


def test_policy_rejects_incomplete_negative_or_non_normalized_weights() -> None:
    """Ranking weights must cover exactly the six nonnegative dimensions."""
    integer_weights = {
        field: int(field == "contribution_potential") for field in SCORE_FIELDS
    }
    assert CharterRankingPolicy(weights=integer_weights).weights == integer_weights
    with pytest.raises(ValidationError, match="exactly"):
        CharterRankingPolicy(weights={"contribution_potential": 1.0})
    with pytest.raises(ValidationError, match="nonnegative"):
        CharterRankingPolicy(weights={field: -0.1 if field == SCORE_FIELDS[0] else 0.22 for field in SCORE_FIELDS})
    with pytest.raises(ValidationError, match="sum to 1"):
        CharterRankingPolicy(weights={field: 0.1 for field in SCORE_FIELDS})


def test_frozen_models_reject_post_validation_score_and_weight_mutation() -> None:
    """Validated ranking inputs cannot be altered through shallowly mutable maps."""
    charter = candidate("air", 80)
    policy = CharterRankingPolicy()

    with pytest.raises(TypeError):
        charter.scores["contribution_potential"] = CharterScore(
            score=0,
            evidence_refs=("evidence:replacement",),
            uncertainty=ScoreUncertainty.LOW,
        )
    with pytest.raises(TypeError):
        policy.weights["contribution_potential"] = 1.0


def test_candidate_rejects_incomplete_distinctness_claims_and_scores() -> None:
    """Candidate declarations cover both peers and every score dimension."""
    payload = candidate("air", 80).model_dump()
    payload["scores"].pop("scope_manageability")
    with pytest.raises(ValidationError, match="exactly"):
        CandidateCharter.model_validate(payload)

    payload = candidate("air", 80).model_dump()
    payload["distinctness_claims"] = payload["distinctness_claims"][:1]
    with pytest.raises(ValidationError):
        CandidateCharter.model_validate(payload)


def test_score_requires_evidence_and_strict_uncertainty() -> None:
    """Scores are bounded, evidenced, and use the explicit uncertainty enum."""
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        CharterScore(score=-1, evidence_refs=("evidence:one",), uncertainty="low")
    with pytest.raises(ValidationError, match="at least 1 item"):
        CharterScore(score=50, evidence_refs=(), uncertainty="low")
    with pytest.raises(ValidationError):
        CharterScore(score=50, evidence_refs=("evidence:one",), uncertainty="unknown")


@pytest.mark.parametrize("score", ["80", True])
def test_score_rejects_coercive_string_and_boolean_values(score: object) -> None:
    """Ranking scores accept numeric values without coercing wire primitives."""
    with pytest.raises(ValidationError):
        CharterScore(
            score=score,
            evidence_refs=("evidence:one",),
            uncertainty=ScoreUncertainty.LOW,
        )


def test_distinctness_claim_rejects_string_boolean_values() -> None:
    """A claimed difference cannot be certified by a coercive string boolean."""
    with pytest.raises(ValidationError):
        DistinctnessClaim(
            other_candidate_id="water",
            different_exposure_or_policy="yes",
            different_outcome_or_mechanism=False,
            explanation="The policy differs.",
        )


def test_policy_rejects_string_weight_values() -> None:
    """Ranking policies do not coerce wire-format strings into weights."""
    with pytest.raises(ValidationError):
        CharterRankingPolicy(weights={field: "0.16666666666666666" for field in SCORE_FIELDS})


def test_broad_and_structured_intakes_are_explicit_and_preserve_gate_one() -> None:
    """Both intake modes are modeled without allowing Gate 1 bypass."""
    broad = ResearchBriefPayload(intake_mode="broad_topic", broad_topic="air pollution")
    candidates = CandidateChartersPayload(brief=broad, candidates=(candidate("air", 80), candidate("water", 70), candidate("soil", 60)))
    structured = ResearchBriefPayload(
        intake_mode="structured_brief",
        structured_brief="Estimate the health effects of clean-air policy.",
    )
    charter = ResearchCharterPayload(brief=structured, charter=candidate("air", 80))

    assert candidates.gate_one_required is True
    assert charter.gate_one_required is True
    with pytest.raises(ValidationError, match="broad_topic"):
        ResearchBriefPayload(intake_mode="broad_topic", structured_brief="not allowed")
    with pytest.raises(ValidationError, match="structured_brief"):
        ResearchBriefPayload(intake_mode="structured_brief", broad_topic="not allowed")
