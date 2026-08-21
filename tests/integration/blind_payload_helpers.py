"""Canonical payload builders shared by blind integration fixtures."""

from __future__ import annotations

from datetime import UTC, datetime

from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_blinding import (
    BlindedBrief,
    BlindedFact,
    LeakageReport,
)
from envresearch.models.benchmark_claims import (
    ClaimFactMap,
    ClaimFactMappingEntry,
    ClaimVerificationStatus,
    CuratorSourceSheet,
    SourceLocator,
    VerifiedClaim,
)
from envresearch.models.benchmark_evaluation import (
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
NOW = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)


def source_sheet(
    case_id: str = "case-rct", *, generation: int = 1
) -> CuratorSourceSheet:
    claim = VerifiedClaim(
        claim_id="claim-001",
        normalized_claim="Eligible units enter treatment after assignment.",
        source_item_key="RCT00001",
        source_attachment_key="RCT00002",
        source_content_hash=SHA256,
        locator=SourceLocator(page=1),
        supporting_passage_hash="b" * 64,
        status=ClaimVerificationStatus.CLAIM_VERIFIED,
        extractor_principal="extractor-1",
        verifier_principal="verifier-1",
        verified_at=NOW,
    )
    return CuratorSourceSheet(
        case_id=case_id,
        method_family="RCT",
        zotero_item_key="RCT00001",
        zotero_attachment_key="RCT00002",
        doi="10.1000/rct.1",
        title="Source title",
        authors=("Author One",),
        source_content_hash=SHA256,
        source_generation=generation,
        institutional_context=("Policy context",),
        restricted_terms=(),
        distinctive_phrase_hashes=(),
        claims=(claim,),
    )


def brief(
    source_ref: ArtifactRef, principal: str, case_id: str = "case-rct"
) -> BlindedBrief:
    return BlindedBrief(
        case_id=case_id,
        source_sheet_ref=source_ref,
        policy_setting="A policy assignment.",
        population="Eligible units.",
        unit="Unit-year",
        treatment_or_exposure="Assignment",
        timing="Before and after assignment.",
        candidate_outcomes=("Outcome",),
        data_structures=("Panel",),
        available_variables=("Outcome", "assignment"),
        institutional_rules=("Eligibility is observed.",),
        constraints=("No source identity is disclosed.",),
        facts=(
            BlindedFact(
                fact_id="fact-001", statement="Assignment is observed.", fact_kind="timing"
            ),
        ),
        masker_principal=principal,
    )


def fact_map(
    source_ref: ArtifactRef, brief_ref: ArtifactRef, principal: str
) -> ClaimFactMap:
    return ClaimFactMap(
        case_id="case-rct",
        source_sheet_ref=source_ref,
        blinded_brief_ref=brief_ref,
        entries=(ClaimFactMappingEntry(claim_id="claim-001", fact_id="fact-001"),),
        mapper_principal=principal,
    )


def leakage(
    source_ref: ArtifactRef, brief_ref: ArtifactRef, principal: str
) -> LeakageReport:
    return LeakageReport(
        source_sheet_ref=source_ref,
        blinded_brief_ref=brief_ref,
        findings=(),
        verdict="pass",
        validator_principal=principal,
        scanner_version="blind-leakage-v1",
        scanner_config_sha256=SHA256,
        checked_at=NOW,
    )


def recommendation(
    brief_ref: ArtifactRef, leakage_ref: ArtifactRef, principal: str
) -> MethodRecommendationPayload:
    candidate = MethodCandidate(
        method_profile_ref="rct-profile-v1",
        role=MethodCandidateRole.PRIMARY,
        rank=1,
        estimand_compatible=True,
        required_assumption_refs=("random-assignment",),
        required_data_structure=("panel",),
        diagnostics=("balance",),
        fallback_or_limitations=("Report attrition.",),
    )
    alternative = candidate.model_copy(
        update={
            "method_profile_ref": "did-profile-v1",
            "role": MethodCandidateRole.ALTERNATIVE,
            "rank": 2,
        }
    )
    return MethodRecommendationPayload(
        blinded_brief_ref=brief_ref,
        leakage_report_ref=leakage_ref,
        method_profile_registry_sha256=SHA256,
        estimand_interpretation="Estimate assignment effects for eligible units.",
        method_candidates=MethodCandidatesPayload(
            estimand_ref="estimand-001", candidates=(candidate, alternative)
        ),
        fact_refs=("fact-001",),
        diagnostics=("Check balance across assignment groups",),
        falsification_tests=("Check placebo outcomes",),
        robustness_plan=("Assess sensitivity to attrition",),
        data_gaps=("Confirm follow-up coverage",),
        decision_boundaries=("Do not extrapolate beyond eligible units",),
        recommender_principal=principal,
    )


def expert_sheet(recommendation_ref: ArtifactRef, principal: str) -> ExpertScoreSheet:
    return ExpertScoreSheet(
        recommendation_ref=recommendation_ref,
        scores=tuple(
            DimensionScore(dimension=dimension, score=3, rationale="Supported.")
            for dimension in ExpertDimension
        ),
        verdict="pass",
        scorer_principal=principal,
    )
