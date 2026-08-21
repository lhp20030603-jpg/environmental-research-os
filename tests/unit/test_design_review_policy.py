"""Tests for strict, method-agnostic research-design contracts."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from envresearch.models.artifact import (
    ArtifactEnvelope,
    ProducerIdentity,
    ResearchArtifact,
    seal_artifact,
)
from envresearch.models.design import (
    AnalysisPlanPayload,
    ClaimMode,
    DesignFinding,
    DesignReviewPayload,
    EstimandSpecPayload,
    EstimandType,
    IdentificationMemoMetadata,
    MethodCandidate,
    MethodCandidateRole,
    MethodCandidatesPayload,
    ResearchQualityScores,
    ReviewSeverity,
)
from envresearch.research.review_policy import ResearchQualityPolicy, ReviewPolicy
from envresearch.storage.research_artifacts import ResearchArtifactStore


@pytest.mark.parametrize(
    ("module", "symbol"),
    (
        ("envresearch.models.design_plan", "AnalysisPlanPayload"),
        ("envresearch.models.design_review", "DesignReviewPayload"),
        ("envresearch.models.design", "AnalysisPlanPayload"),
    ),
)
def test_split_design_modules_import_directly_in_fresh_process(
    module: str, symbol: str
) -> None:
    """Public split modules must not depend on importing design first."""
    result = subprocess.run(
        [sys.executable, "-c", f"from {module} import {symbol}"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def estimand(
    estimand_type: EstimandType = EstimandType.CAUSAL,
) -> EstimandSpecPayload:
    """Build an evidence-backed estimand without choosing a method profile."""
    return EstimandSpecPayload(
        estimand_id="estimand:policy-effect",
        estimand_type=estimand_type,
        population="Households exposed to the policy area",
        unit="household-year",
        exposure_or_treatment="Policy eligibility",
        outcome="Annual energy consumption",
        comparison_or_counterfactual=(
            "Eligible households compared with comparable non-eligible households"
            if estimand_type is EstimandType.CAUSAL
            else None
        ),
        time_horizon="Twelve months after eligibility",
        target_parameter="Average outcome contrast"
        if estimand_type is EstimandType.CAUSAL
        else "Mean annual energy consumption",
        evidence_refs=("evidence:policy",),
        assumption_refs=("assumption:exchangeability",)
        if estimand_type is EstimandType.CAUSAL
        else (),
    )


def design_finding(
    finding_id: str,
    severity: ReviewSeverity,
    *,
    resolved: bool,
    residual_risk: str | None = None,
) -> DesignFinding:
    """Build a reviewed finding with the metadata needed for its state."""
    return DesignFinding(
        finding_id=finding_id,
        severity=severity,
        resolved=resolved,
        finding="The design requires reviewer attention.",
        evidence_refs=("evidence:review",),
        remediation=None if resolved else "Revise the affected design artifact.",
        resolution="The artifact was revised and independently checked."
        if resolved
        else None,
        residual_risk=residual_risk,
    )


def method_candidate(
    profile_ref: str,
    role: MethodCandidateRole,
    rank: int,
) -> MethodCandidate:
    """Build a method-agnostic candidate with reviewable requirements."""
    return MethodCandidate(
        method_profile_ref=profile_ref,
        role=role,
        rank=rank,
        estimand_compatible=True,
        required_assumption_refs=("assumption:exchangeability",),
        required_data_structure=("unit-level observations",),
        diagnostics=("Assess sensitivity to unmeasured confounding.",),
        fallback_or_limitations=("Use the alternative profile if assumptions fail.",),
    )


def analysis_plan(
    *,
    claim_mode: ClaimMode | str = ClaimMode.CAUSAL,
    estimand_type: EstimandType | str = EstimandType.CAUSAL,
) -> AnalysisPlanPayload:
    """Build the terminal planning artifact without executing an estimator."""
    return AnalysisPlanPayload(
        estimand_ref="estimand:policy-effect",
        estimand_type=estimand_type,
        primary_method_profile_ref="profile:primary",
        alternative_method_profile_refs=("profile:alternative",),
        data_boundaries=("Use approved household records only.",),
        assumptions=("Eligibility is conditionally exchangeable.",),
        diagnostics=("Inspect covariate balance.",),
        exclusion_rules=("Exclude records missing the outcome.",),
        robustness_plan=("Vary the comparison specification.",),
        fallback_rules=("Use the alternative profile if diagnostics fail.",),
        claim_mode=claim_mode,
    )


def test_blocking_finding_prevents_plan_composition() -> None:
    """An open blocking finding is never composable."""
    finding = design_finding("f1", ReviewSeverity.BLOCKING, resolved=False)

    assert ReviewPolicy.can_compose((finding,)) is False


def test_major_may_reach_final_gate_only_with_residual_risk() -> None:
    """Human acceptance cannot waive an open major finding without its risk."""
    finding = design_finding("f2", ReviewSeverity.MAJOR, resolved=False)

    assert ReviewPolicy.final_gate_eligible((finding,), frozenset()) is False
    assert ReviewPolicy.final_gate_eligible((finding,), frozenset({"f2"})) is False

    risk_recorded = design_finding(
        "f2",
        ReviewSeverity.MAJOR,
        resolved=False,
        residual_risk="Unmeasured confounding may remain after diagnostics.",
    )
    assert ReviewPolicy.final_gate_eligible((risk_recorded,), frozenset({"f2"})) is True


def test_descriptive_estimand_rejects_causal_claim_mode() -> None:
    """A plan cannot use causal language for a descriptive estimand."""
    with pytest.raises(ValueError, match="causal claim requires a causal estimand"):
        analysis_plan(claim_mode="causal", estimand_type="descriptive")


def test_quality_rubric_requires_all_six_dimensions_at_least_three() -> None:
    """One low quality dimension fails the benchmark research-quality gate."""
    scores = ResearchQualityScores(
        contribution_clarity=4,
        evidence_coverage=4,
        data_feasibility=3,
        estimand_precision=5,
        identification_credibility=2,
        uncertainty_disclosure=4,
    )

    assert ResearchQualityPolicy.passes(scores, findings=()) is False


def test_quality_policy_requires_explicit_major_risk_acceptance() -> None:
    """An accepted open major remains non-passing until risk metadata is recorded."""
    scores = ResearchQualityScores(
        contribution_clarity=3,
        evidence_coverage=3,
        data_feasibility=3,
        estimand_precision=3,
        identification_credibility=3,
        uncertainty_disclosure=3,
    )
    finding = design_finding("major-1", ReviewSeverity.MAJOR, resolved=False)

    assert (
        ResearchQualityPolicy.passes(
            scores, (finding,), accepted_major_ids=frozenset({"major-1"})
        )
        is False
    )


@pytest.mark.parametrize("forged_value", [3.5, 6, True, "3", 0])
def test_quality_policy_revalidates_forged_score_copies(
    forged_value: object,
) -> None:
    """Unchecked model copies cannot bypass strict integer score bounds."""
    valid_scores = ResearchQualityScores(
        contribution_clarity=3,
        evidence_coverage=3,
        data_feasibility=3,
        estimand_precision=3,
        identification_credibility=3,
        uncertainty_disclosure=3,
    )
    forged_scores = valid_scores.model_copy(
        update={"contribution_clarity": forged_value}
    )

    with pytest.raises(ValidationError):
        ResearchQualityPolicy.passes(forged_scores, findings=())


def test_review_policy_rejects_duplicate_and_invalid_accepted_ids() -> None:
    """Review closure never silently ignores inconsistent acceptance records."""
    duplicate_ids = (
        design_finding("same", ReviewSeverity.MINOR, resolved=False),
        design_finding("same", ReviewSeverity.ADVISORY, resolved=False),
    )
    blocking = design_finding("blocking", ReviewSeverity.BLOCKING, resolved=False)

    with pytest.raises(ValueError, match="duplicate finding_id"):
        ReviewPolicy.can_compose(duplicate_ids)
    with pytest.raises(ValueError, match="only unresolved major"):
        ReviewPolicy.final_gate_eligible((blocking,), frozenset({"blocking"}))
    with pytest.raises(ValueError, match="unknown"):
        ReviewPolicy.final_gate_eligible((), frozenset({"missing"}))


def test_final_gate_revalidates_findings_before_applying_severity() -> None:
    """An unchecked model copy cannot turn an open major into a gate bypass."""
    minor = design_finding("copied", ReviewSeverity.MINOR, resolved=False)
    corrupted = minor.model_copy(update={"severity": "major"})

    assert ReviewPolicy.final_gate_eligible((corrupted,), frozenset()) is False


def test_serialized_review_rejects_duplicate_accepted_major_ids() -> None:
    """JSON-style acceptance arrays are checked before conversion to a set."""
    with pytest.raises(ValidationError, match="duplicate"):
        DesignReviewPayload.model_validate(
            {
                "review_id": "review:duplicate-acceptance",
                "findings": [
                    design_finding(
                        "major:one",
                        ReviewSeverity.MAJOR,
                        resolved=False,
                        residual_risk="Coverage remains incomplete for one subgroup.",
                    ).model_dump(mode="json")
                ],
                "accepted_major_ids": ["major:one", "major:one"],
            }
        )


def test_design_payloads_require_unique_id_references_and_one_primary() -> None:
    """Method and memo references remain unambiguous for artifact persistence."""
    primary = method_candidate("profile:primary", MethodCandidateRole.PRIMARY, 1)
    alternative = method_candidate(
        "profile:alternative", MethodCandidateRole.ALTERNATIVE, 2
    )
    candidates = MethodCandidatesPayload(
        estimand_ref="estimand:policy-effect",
        candidates=(primary, alternative),
    )
    memo = IdentificationMemoMetadata(
        estimand_ref="estimand:policy-effect",
        primary_method_profile_ref="profile:primary",
        alternative_method_profile_refs=("profile:alternative",),
        assumption_refs=("assumption:exchangeability",),
        threat_refs=("threat:confounding",),
        diagnostic_refs=("diagnostic:balance",),
        evidence_refs=("evidence:policy",),
        residual_risks=("Residual confounding remains possible.",),
    )

    assert candidates.primary.method_profile_ref == "profile:primary"
    assert memo.alternative_method_profile_refs == ("profile:alternative",)
    with pytest.raises(ValidationError, match="duplicate method_profile_ref"):
        MethodCandidatesPayload(
            estimand_ref="estimand:policy-effect",
            candidates=(
                primary,
                primary.model_copy(
                    update={"role": MethodCandidateRole.ALTERNATIVE, "rank": 2}
                ),
            ),
        )


def test_method_alternatives_are_returned_in_rank_order() -> None:
    """Alternative profile consumers see deterministic rank order, not input order."""
    candidates = MethodCandidatesPayload(
        estimand_ref="estimand:policy-effect",
        candidates=(
            method_candidate("profile:primary", MethodCandidateRole.PRIMARY, 1),
            method_candidate("profile:third", MethodCandidateRole.ALTERNATIVE, 3),
            method_candidate("profile:second", MethodCandidateRole.ALTERNATIVE, 2),
        ),
    )

    assert [item.method_profile_ref for item in candidates.alternatives] == [
        "profile:second",
        "profile:third",
    ]


def test_analysis_plan_is_terminal_and_rejects_duplicate_primary_reference() -> None:
    """The final artifact only describes an approved plan and cannot repeat methods."""
    plan = analysis_plan()

    assert plan.stop_rule == "approved_plan_only"
    with pytest.raises(ValidationError, match="must not duplicate"):
        AnalysisPlanPayload.model_validate(
            {
                **plan.model_dump(),
                "alternative_method_profile_refs": ("profile:primary",),
            }
        )


@pytest.mark.parametrize("value", [True, "3", 3.0])
def test_quality_scores_reject_coercive_primitives(value: object) -> None:
    """Quality scores are strict whole-number rubric values, never coerced input."""
    with pytest.raises(ValidationError):
        ResearchQualityScores(
            contribution_clarity=value,
            evidence_coverage=3,
            data_feasibility=3,
            estimand_precision=3,
            identification_credibility=3,
            uncertainty_disclosure=3,
        )


@pytest.mark.parametrize("suffix", ("json", "yaml"))
def test_design_review_round_trips_through_structured_artifact_store(
    tmp_path: Path, suffix: str
) -> None:
    """JSON and YAML persistence preserve strict review collection contracts."""
    review = DesignReviewPayload(
        review_id="review:one",
        findings=(
            design_finding(
                "major:one",
                ReviewSeverity.MAJOR,
                resolved=False,
                residual_risk="Coverage remains incomplete for one population subgroup.",
            ),
        ),
        accepted_major_ids=frozenset({"major:one"}),
    )
    artifact = seal_artifact(
        ResearchArtifact(
            envelope=ArtifactEnvelope(
                artifact_id="design-review",
                artifact_version=1,
                run_id="run:one",
                created_at=datetime(2026, 8, 5, tzinfo=UTC),
                producer=ProducerIdentity(component="design-review", version="0.2.0"),
            ),
            payload=review,
        )
    )
    store = ResearchArtifactStore(tmp_path)
    relative = Path(f"artifacts/design-review.{suffix}")

    store.write_structured(relative, artifact)

    loaded = store.read_structured(
        relative, TypeAdapter(ResearchArtifact[DesignReviewPayload])
    )

    assert loaded == artifact
