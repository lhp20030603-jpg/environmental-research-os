"""Deterministic synthetic payloads for actual design-workflow replay."""

from __future__ import annotations

from decimal import Decimal

from envresearch.connectors.contracts import ConnectorCoverage
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
    ReviewSeverity,
)
from envresearch.models.evidence import (
    DataFeasibilityPayload,
    DatasetCandidate,
    EvidenceRow,
    LiteratureMapPayload,
    SourceRecord,
)
from envresearch.models.intake import (
    SCORE_FIELDS,
    CandidateCharter,
    CandidateChartersPayload,
    CharterScore,
    DistinctnessClaim,
    ResearchBriefPayload,
    ResearchCharterPayload,
    ScoreUncertainty,
)


def candidate_charters(brief: ResearchBriefPayload) -> CandidateChartersPayload:
    """Return three distinct, rankable policy charters."""
    ids = ("charter-air", "charter-tax", "charter-transit")
    questions = (
        "Do clean-air zones reduce PM2.5?",
        "Do pollution taxes reduce emissions?",
        "Does transit access reduce car use?",
    )
    candidates = (
        _charter(ids[0], questions[0], (ids[1], ids[2])),
        _charter(ids[1], questions[1], (ids[0], ids[2])),
        _charter(ids[2], questions[2], (ids[0], ids[1])),
    )
    return CandidateChartersPayload(brief=brief, candidates=candidates)


def normalized_charter(brief: ResearchBriefPayload) -> ResearchCharterPayload:
    """Return one structured draft that still requires Gate 1."""
    return ResearchCharterPayload(
        brief=brief,
        charter=_charter(
            "charter-structured",
            "Do clean-air zones reduce PM2.5?",
            ("comparison-policy", "comparison-mechanism"),
        ),
    )


def literature(*, coverage: ConnectorCoverage | None = None) -> LiteratureMapPayload:
    """Return local metadata evidence, optionally documenting connector fallback."""
    source = (
        "repository local fallback after connector outage"
        if coverage is not None and coverage.status == "degraded"
        else "repository local literature fixture"
    )
    return LiteratureMapPayload(
        research_question="Do clean-air zones reduce PM2.5?",
        sources=(
            SourceRecord(
                source_id="paper-1",
                title="Clean-air zones and air quality",
                source=source,
                evidence_reason="Directly studies the policy and outcome.",
            ),
        ),
        evidence_rows=(
            EvidenceRow(
                evidence_id="evidence-1",
                source_id="paper-1",
                finding="Exposure fell after implementation.",
                relevance="Supports the proposed outcome definition.",
                evidence_reason="Reported in repository-owned synthetic metadata.",
            ),
        ),
        synthesis="Distributional effects remain an open design question.",
    )


def feasibility(*, restricted: bool = False) -> DataFeasibilityPayload:
    """Return either public metadata or a credential-bound candidate."""
    candidate = DatasetCandidate(
        dataset_id="local-air",
        source="repository local fixture catalog",
        public_access=not restricted,
        requires_credentials=restricted,
        clear_license=not restricted,
        license="CC0-1.0" if not restricted else "unknown",
        estimated_download_bytes=10,
        estimated_local_storage_bytes=20,
        estimated_api_calls=0,
        estimated_external_cost=Decimal(0),
        estimated_elapsed_seconds=1,
        suitable_for_design=True,
        suitability_reason="Contains the variables needed for design planning.",
        access_reason=(
            "Institutional permission is required."
            if restricted
            else "Public repository-owned metadata fixture."
        ),
        data_structures=("panel",),
        available_features=(
            "donor_pool",
            "one_or_few_treated_units",
            "pre_treatment_periods",
            "treatment_timing_variation",
            "untreated_comparison",
        ),
    )
    return DataFeasibilityPayload(
        research_design="Panel comparison",
        candidates=(candidate,),
        recommendation="Use only after the declared access boundary is satisfied.",
        evidence_reason="Access, license, and budget metadata are explicit.",
    )


def estimand() -> EstimandSpecPayload:
    return EstimandSpecPayload(
        estimand_id="estimand-air",
        estimand_type=EstimandType.CAUSAL,
        population="Residents of adopting cities",
        unit="city-month",
        exposure_or_treatment="Clean-air-zone adoption",
        outcome="Monthly PM2.5",
        comparison_or_counterfactual="Same cities absent adoption",
        time_horizon="Three years",
        target_parameter="Average treatment effect on treated cities",
        evidence_refs=("evidence-1",),
        assumption_refs=("assumption-parallel-trends",),
    )


def methods(estimand_ref: str) -> MethodCandidatesPayload:
    return MethodCandidatesPayload(
        estimand_ref=estimand_ref,
        candidates=(
            MethodCandidate(
                method_profile_ref="did-event-study@0.2.0",
                role=MethodCandidateRole.PRIMARY,
                rank=1,
                estimand_compatible=True,
                required_assumption_refs=("assumption-parallel-trends",),
                required_data_structure=("city-month panel",),
                diagnostics=("pre-trend diagnostic",),
                fallback_or_limitations=("Use descriptive trends if support fails",),
            ),
            MethodCandidate(
                method_profile_ref="synthetic-control@0.2.0",
                role=MethodCandidateRole.ALTERNATIVE,
                rank=2,
                estimand_compatible=True,
                required_assumption_refs=("assumption-parallel-trends",),
                required_data_structure=("city-month panel",),
                diagnostics=("pre-trend diagnostic",),
                fallback_or_limitations=("Use descriptive trends if support fails",),
            ),
        ),
    )


def memo(estimand_ref: str) -> dict[str, object]:
    metadata = IdentificationMemoMetadata(
        estimand_ref=estimand_ref,
        primary_method_profile_ref="did-event-study@0.2.0",
        alternative_method_profile_refs=("synthetic-control@0.2.0",),
        assumption_refs=("assumption-parallel-trends",),
        threat_refs=("threat-policy-selection",),
        diagnostic_refs=("pre-trend diagnostic",),
        evidence_refs=("evidence-1",),
        residual_risks=("Policy timing may remain endogenous.",),
    )
    return {
        "metadata": metadata.model_dump(mode="json"),
        "body": "# Identification\n\nCompare treated and untreated city trends.\n",
    }


def blocking_review() -> DesignReviewPayload:
    return DesignReviewPayload(
        review_id="review-blocked",
        findings=(
            DesignFinding(
                finding_id="blocking-selection",
                severity=ReviewSeverity.BLOCKING,
                resolved=False,
                finding="Treatment timing is not yet credibly separated from selection.",
                evidence_refs=("evidence-1",),
                remediation="Define a defensible timing diagnostic before composition.",
                residual_risk="Selection could invalidate causal interpretation.",
            ),
        ),
    )


def resolved_review() -> DesignReviewPayload:
    """Return an independent review with no open findings."""
    return DesignReviewPayload(review_id="review-clear", findings=())


def resolved_blocking_review() -> DesignReviewPayload:
    """Close the exact blocker introduced by :func:`blocking_review`."""
    original = blocking_review().findings[0]
    return DesignReviewPayload(
        review_id="review-blocked-r2",
        findings=(
            original.model_copy(
                update={
                    "resolved": True,
                    "remediation": None,
                    "resolution": (
                        "Added a timing diagnostic and a pre-specified descriptive "
                        "fallback for unresolved selection."
                    ),
                }
            ),
        ),
    )


def analysis_plan(estimand_ref: str) -> AnalysisPlanPayload:
    """Return the complete planning-only terminal artifact."""
    return AnalysisPlanPayload(
        estimand_ref=estimand_ref,
        estimand_type=EstimandType.CAUSAL,
        primary_method_profile_ref="did-event-study@0.2.0",
        alternative_method_profile_refs=("synthetic-control@0.2.0",),
        data_boundaries=("Use only documented data after access approval.",),
        assumptions=("Parallel trends and no anticipatory treatment response.",),
        diagnostics=("Event-study pre-trends and support diagnostics.",),
        exclusion_rules=("Exclude cities without baseline outcome coverage.",),
        robustness_plan=("Compare alternative comparison groups and windows.",),
        fallback_rules=("Use descriptive claims if identifying assumptions fail.",),
        claim_mode=ClaimMode.CAUSAL,
    )


def _charter(
    candidate_id: str, question: str, others: tuple[str, str]
) -> CandidateCharter:
    scores = {
        field: CharterScore(
            score=75.0,
            evidence_refs=("brief",),
            uncertainty=ScoreUncertainty.MEDIUM,
        )
        for field in SCORE_FIELDS
    }
    return CandidateCharter(
        candidate_id=candidate_id,
        research_question=question,
        scores=scores,
        distinctness_claims=(
            DistinctnessClaim(
                other_candidate_id=others[0],
                different_exposure_or_policy=True,
                different_outcome_or_mechanism=False,
                explanation=(
                    f"{candidate_id} uses a different policy from {others[0]}."
                ),
            ),
            DistinctnessClaim(
                other_candidate_id=others[1],
                different_exposure_or_policy=True,
                different_outcome_or_mechanism=False,
                explanation=(
                    f"{candidate_id} uses a different policy from {others[1]}."
                ),
            ),
        ),
    )
