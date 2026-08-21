"""Strict contracts for blind benchmark evaluation and release readiness."""

from __future__ import annotations

import re
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    StrictInt,
    field_validator,
    model_validator,
)

from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_claims import ClaimUsage
from envresearch.models.design import MethodCandidatesPayload

__all__ = [
    "EXPERT_WEIGHTS",
    "AcceptedArtifactClaims",
    "AdjudicationVerdict",
    "CaseEvaluation",
    "CitationIntegrityFinding",
    "CitationIntegrityReport",
    "CriticalMethodFinding",
    "DimensionScore",
    "ExpertDimension",
    "ExpertScoreSheet",
    "MethodRecommendationPayload",
    "PosthocComparison",
    "ReleaseReadinessReport",
]

_CANONICAL_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_FACT_REFERENCE = re.compile(r"\bfact-[a-z0-9]+(?:-[a-z0-9]+)*\b")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _parse_serialized_tuple(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


SerializedStrings = Annotated[tuple[str, ...], BeforeValidator(_parse_serialized_tuple)]


def _require_nonblank(value: str, field_name: str) -> str:
    if not value.strip() or value != value.strip():
        raise ValueError(f"{field_name} must be a canonical nonblank value")
    return value


def _require_id(value: str, field_name: str) -> str:
    if not _CANONICAL_ID.fullmatch(value):
        raise ValueError(f"{field_name} must be a canonical lowercase identifier")
    return value


def _require_unique_strings(
    value: tuple[str, ...], field_name: str, *, required: bool = False
) -> tuple[str, ...]:
    if required and not value:
        raise ValueError(f"{field_name} must contain at least one item")
    for item in value:
        _require_nonblank(item, field_name)
    if len(value) != len(set(value)):
        raise ValueError(f"{field_name} must not contain duplicate values")
    return value


class ExpertDimension(StrEnum):
    IDENTIFICATION_FIT = "identification_fit"
    ASSUMPTIONS_THREATS = "assumptions_threats"
    DATA_COMPATIBILITY = "data_compatibility"
    ALTERNATIVE_METHODS = "alternative_methods"
    DIAGNOSTICS_POLICY = "diagnostics_policy"


EXPERT_WEIGHTS: dict[ExpertDimension, Decimal] = {
    ExpertDimension.IDENTIFICATION_FIT: Decimal("0.30"),
    ExpertDimension.ASSUMPTIONS_THREATS: Decimal("0.25"),
    ExpertDimension.DATA_COMPATIBILITY: Decimal("0.20"),
    ExpertDimension.ALTERNATIVE_METHODS: Decimal("0.15"),
    ExpertDimension.DIAGNOSTICS_POLICY: Decimal("0.10"),
}


class MethodRecommendationPayload(_StrictModel):
    blinded_brief_ref: ArtifactRef
    leakage_report_ref: ArtifactRef
    method_profile_registry_sha256: str
    estimand_interpretation: str
    method_candidates: MethodCandidatesPayload
    fact_refs: SerializedStrings
    diagnostics: SerializedStrings
    falsification_tests: SerializedStrings
    robustness_plan: SerializedStrings
    data_gaps: SerializedStrings
    decision_boundaries: SerializedStrings
    recommender_principal: str

    @field_validator("method_profile_registry_sha256")
    @classmethod
    def require_registry_hash(cls, value: str) -> str:
        """Bind recommendation choices to one immutable profile registry."""
        if not _SHA256.fullmatch(value):
            raise ValueError("method_profile_registry_sha256 must be a 64-character lowercase SHA-256")
        return value

    @field_validator("estimand_interpretation", "recommender_principal")
    @classmethod
    def require_nonblank_text(cls, value: str, info: object) -> str:
        """Keep the recommendation meaning and accountable author explicit."""
        return _require_nonblank(value, getattr(info, "field_name", "recommendation"))

    @field_validator(
        "fact_refs",
        "diagnostics",
        "falsification_tests",
        "robustness_plan",
        "data_gaps",
        "decision_boundaries",
    )
    @classmethod
    def require_complete_lists(
        cls, value: tuple[str, ...], info: object
    ) -> tuple[str, ...]:
        """Require unique reviewable content in every recommendation section."""
        field_name = getattr(info, "field_name", "recommendation list")
        return _require_unique_strings(value, field_name, required=True)

    @field_validator("fact_refs")
    @classmethod
    def require_fact_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require opaque fact identifiers rather than source-specific citations."""
        for fact_id in value:
            if not fact_id.startswith("fact-"):
                raise ValueError("fact_refs must contain blinded fact IDs")
            _require_id(fact_id, "fact_refs")
        return value

    @model_validator(mode="after")
    def reserve_fact_ids_for_structured_refs(self) -> MethodRecommendationPayload:
        """Keep evidence linkage out of analytical prose fields."""
        fields = (
            self.estimand_interpretation,
            *self.diagnostics,
            *self.falsification_tests,
            *self.robustness_plan,
            *self.data_gaps,
            *self.decision_boundaries,
        )
        used_facts = {match.group(0) for text in fields for match in _FACT_REFERENCE.finditer(text)}
        if used_facts:
            raise ValueError("recommendation may contain fact IDs only in fact_refs")
        return self


class DimensionScore(_StrictModel):
    dimension: ExpertDimension
    score: StrictInt = Field(ge=0, le=4)
    rationale: str
    fact_refs: SerializedStrings = ()

    @field_validator("dimension", mode="before")
    @classmethod
    def parse_dimension(cls, value: object) -> object:
        """Accept exact serialized rubric members at persisted boundaries."""
        return ExpertDimension(value) if isinstance(value, str) else value

    @field_validator("rationale")
    @classmethod
    def require_rationale(cls, value: str) -> str:
        """Require a human-reviewable reason for each ordinal score."""
        return _require_nonblank(value, "rationale")

    @field_validator("fact_refs")
    @classmethod
    def require_fact_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Keep optional blind-evidence links unique and opaque."""
        return _require_unique_strings(value, "fact_refs")


class CriticalMethodFinding(_StrictModel):
    finding_id: str
    severity: Literal["critical"] = "critical"
    description: str
    fact_refs: SerializedStrings = ()

    @field_validator("finding_id")
    @classmethod
    def require_finding_id(cls, value: str) -> str:
        """Require a stable identifier for later adjudication."""
        return _require_id(value, "finding_id")

    @field_validator("description")
    @classmethod
    def require_description(cls, value: str) -> str:
        """Prevent unreviewable empty critical defects."""
        return _require_nonblank(value, "description")

    @field_validator("fact_refs")
    @classmethod
    def require_finding_fact_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Keep blind evidence links unique without adding source identity."""
        return _require_unique_strings(value, "fact_refs")


class ExpertScoreSheet(_StrictModel):
    recommendation_ref: ArtifactRef
    scores: tuple[DimensionScore, ...]
    critical_findings: tuple[CriticalMethodFinding, ...] = ()
    verdict: Literal["pass", "fail"]
    scorer_principal: str

    @field_validator("scores", mode="before")
    @classmethod
    def parse_scores(cls, value: object) -> object:
        """Accept JSON arrays while storing immutable tuples."""
        return _parse_serialized_tuple(value)

    @field_validator("scorer_principal")
    @classmethod
    def require_scorer(cls, value: str) -> str:
        """Retain accountable expert identity for every score sheet."""
        return _require_nonblank(value, "scorer_principal")

    @model_validator(mode="after")
    def require_complete_rubric_and_consistent_verdict(self) -> ExpertScoreSheet:
        """Fail closed unless the rubric is complete and pass criteria hold."""
        dimensions = tuple(score.dimension for score in self.scores)
        if len(dimensions) != len(ExpertDimension) or set(dimensions) != set(ExpertDimension):
            raise ValueError("scores must contain exactly the five expert dimensions once")
        identification = next(
            score.score
            for score in self.scores
            if score.dimension is ExpertDimension.IDENTIFICATION_FIT
        )
        if self.verdict == "pass" and identification < 3:
            raise ValueError("PASS score sheet requires identification_fit score of at least 3")
        if self.verdict == "pass" and self.critical_findings:
            raise ValueError("PASS score sheet cannot contain critical findings")
        return self

    def weighted_score(self) -> Decimal:
        """Return the exact weighted score without binary floating-point arithmetic."""
        return sum(
            (Decimal(score.score) * EXPERT_WEIGHTS[score.dimension] for score in self.scores),
            Decimal(0),
        )


class AdjudicationVerdict(_StrictModel):
    score_sheet_ref: ArtifactRef
    verdict: Literal["accept", "reject", "revise"]
    rationale: str
    adjudicator_principal: str

    @field_validator("rationale", "adjudicator_principal")
    @classmethod
    def require_text(cls, value: str, info: object) -> str:
        """Make adjudication reasons and responsibility explicit."""
        return _require_nonblank(value, getattr(info, "field_name", "adjudication"))


class PosthocComparison(_StrictModel):
    recommendation_ref: ArtifactRef
    realized_method_profile_ref: str
    comparison: JsonValue
    analyst_principal: str

    @field_validator("realized_method_profile_ref", "analyst_principal")
    @classmethod
    def require_posthoc_text(cls, value: str, info: object) -> str:
        """Require reviewable post-hoc method and analyst labels."""
        return _require_nonblank(value, getattr(info, "field_name", "posthoc field"))


class AcceptedArtifactClaims(_StrictModel):
    artifact_ref: ArtifactRef
    payload: JsonValue
    usages: tuple[ClaimUsage, ...]

    @field_validator("usages", mode="before")
    @classmethod
    def parse_usages(cls, value: object) -> object:
        """Accept serialized citations while storing an immutable collection."""
        return _parse_serialized_tuple(value)

    @model_validator(mode="after")
    def require_unique_claim_locations(self) -> AcceptedArtifactClaims:
        """Prevent duplicate claim attachments at one accepted payload location."""
        locations = tuple((usage.claim_id, usage.json_pointer) for usage in self.usages)
        if len(locations) != len(set(locations)):
            raise ValueError("usages must not contain duplicate claim locations")
        return self


class CitationIntegrityFinding(_StrictModel):
    claim_id: str
    json_pointer: str
    status: Literal["verified", "missing", "mismatch", "stale"]
    detail: str

    @field_validator("claim_id")
    @classmethod
    def require_claim_id(cls, value: str) -> str:
        """Keep citation review linked to a durable source claim."""
        return _require_id(value, "claim_id")

    @field_validator("json_pointer", "detail")
    @classmethod
    def require_finding_text(cls, value: str, info: object) -> str:
        """Require a concrete location and reviewable disposition detail."""
        return _require_nonblank(value, getattr(info, "field_name", "citation field"))


class CitationIntegrityReport(_StrictModel):
    accepted_artifact_claims_ref: ArtifactRef
    findings: tuple[CitationIntegrityFinding, ...]
    verdict: Literal["pass", "rejected"]
    validator_principal: str

    @field_validator("findings", mode="before")
    @classmethod
    def parse_findings(cls, value: object) -> object:
        """Accept JSON arrays while retaining immutable report findings."""
        return _parse_serialized_tuple(value)

    @field_validator("validator_principal")
    @classmethod
    def require_validator(cls, value: str) -> str:
        """Keep citation-verdict responsibility explicit."""
        return _require_nonblank(value, "validator_principal")

    @model_validator(mode="after")
    def require_pass_has_only_verified_citations(self) -> CitationIntegrityReport:
        """Reject a passing report containing any nonverified citation finding."""
        if self.verdict == "pass" and any(
            finding.status != "verified" for finding in self.findings
        ):
            raise ValueError("PASS citation report cannot contain nonverified findings")
        return self


class CaseEvaluation(_StrictModel):
    case_id: str
    recommendation_ref: ArtifactRef
    expert_score_sheet_refs: Annotated[
        tuple[ArtifactRef, ...], BeforeValidator(_parse_serialized_tuple)
    ]
    adjudication_ref: ArtifactRef | None = None
    citation_integrity_report_ref: ArtifactRef
    posthoc_comparison_ref: ArtifactRef | None = None

    @field_validator("case_id")
    @classmethod
    def require_case_id(cls, value: str) -> str:
        return _require_id(value, "case_id")

    @model_validator(mode="after")
    def require_two_unique_expert_score_sheets(self) -> CaseEvaluation:
        if len(self.expert_score_sheet_refs) != 2 or (
            self.expert_score_sheet_refs[0] == self.expert_score_sheet_refs[1]
        ):
            raise ValueError("must contain exactly two unique expert score sheet refs")
        return self


class ReleaseReadinessReport(_StrictModel):
    case_evaluations: tuple[CaseEvaluation, ...] = Field(min_length=1)
    verdict: Literal["ready", "blocked"]
    release_principal: str

    @field_validator("case_evaluations", mode="before")
    @classmethod
    def parse_cases(cls, value: object) -> object:
        return _parse_serialized_tuple(value)

    @field_validator("release_principal")
    @classmethod
    def require_release_principal(cls, value: str) -> str:
        return _require_nonblank(value, "release_principal")

    @model_validator(mode="after")
    def require_unique_cases(self) -> ReleaseReadinessReport:
        case_ids = tuple(case.case_id for case in self.case_evaluations)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case_evaluations must not contain duplicate case IDs")
        return self
