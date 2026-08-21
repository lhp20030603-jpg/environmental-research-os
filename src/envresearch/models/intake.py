"""Strict contracts for broad-topic and structured-brief research intake."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_serializer,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

SCORE_FIELDS = (
    "contribution_potential",
    "literature_gap",
    "data_feasibility",
    "identification_plausibility",
    "policy_relevance",
    "scope_manageability",
)


class ResearchIntakeMode(StrEnum):
    """The explicitly selected path into Discover/Design."""

    BROAD_TOPIC = "broad_topic"
    STRUCTURED_BRIEF = "structured_brief"


class ScoreUncertainty(StrEnum):
    """Allowed confidence levels for a charter score's supporting evidence."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CharterScore(BaseModel):
    """One evidenced, bounded score used to rank a candidate charter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    score: float = Field(ge=0, le=100)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    uncertainty: ScoreUncertainty

    @field_validator("score", mode="before")
    @classmethod
    def require_numeric_score(cls, value: object) -> object:
        """Reject coercive score primitives while allowing integer scores."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PydanticCustomError("score_type", "score must be a numeric value")
        return value

    @field_validator("evidence_refs")
    @classmethod
    def require_nonblank_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Keep every score auditable through at least one named reference."""
        if any(not reference.strip() for reference in value):
            raise ValueError("evidence_refs must not contain blank values")
        return value


class DistinctnessClaim(BaseModel):
    """A declared, reviewable difference from one other candidate charter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    other_candidate_id: str
    different_exposure_or_policy: StrictBool
    different_outcome_or_mechanism: StrictBool
    explanation: str

    @field_validator("other_candidate_id", "explanation")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        """Reject claims without a target or an explanation for later review."""
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def require_a_substantive_difference(self) -> DistinctnessClaim:
        """Require a claim to identify at least one concrete difference."""
        if not (
            self.different_exposure_or_policy
            or self.different_outcome_or_mechanism
        ):
            raise ValueError("a distinctness claim must describe a difference")
        return self


def question_fingerprint(research_question: str) -> str:
    """Normalize only superficial question presentation, never semantic meaning."""
    return " ".join(research_question.casefold().split())


class CandidateCharter(BaseModel):
    """A proposed research direction with fixed-dimension scores and claims."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    research_question: str
    scores: Mapping[str, CharterScore]
    distinctness_claims: tuple[DistinctnessClaim, DistinctnessClaim]
    total_score: float | None = None

    @field_validator("candidate_id", "research_question")
    @classmethod
    def require_nonblank_identity_text(cls, value: str) -> str:
        """Reject blank IDs and questions before a charter enters ranking."""
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("scores")
    @classmethod
    def require_exact_score_dimensions(
        cls, value: Mapping[str, CharterScore]
    ) -> Mapping[str, CharterScore]:
        """Keep ranking comparable by requiring every approved dimension once."""
        if set(value) != set(SCORE_FIELDS):
            raise ValueError("scores must contain exactly the six score dimensions")
        return value

    @model_validator(mode="after")
    def require_internal_claim_consistency(self) -> CandidateCharter:
        """Reject self-claims and duplicate comparison targets."""
        target_ids = tuple(claim.other_candidate_id for claim in self.distinctness_claims)
        if self.candidate_id in target_ids:
            raise ValueError("distinctness claims must not target the candidate itself")
        if len(set(target_ids)) != len(target_ids):
            raise ValueError("distinctness claims must not duplicate targets")
        object.__setattr__(self, "scores", MappingProxyType(dict(self.scores)))
        return self

    @field_serializer("scores")
    def serialize_scores(self, value: Mapping[str, CharterScore]) -> dict[str, CharterScore]:
        """Expose a normal serializable mapping without exposing mutable state."""
        return dict(value)

    @property
    def question_fingerprint(self) -> str:
        """Return the superficial-normalization fingerprint for duplicate checks."""
        return question_fingerprint(self.research_question)

    def with_total(self, total_score: float) -> CandidateCharter:
        """Return a copy with a ranker-derived, rather than caller-trusted, total."""
        return self.model_copy(update={"total_score": total_score})


class ResearchBriefPayload(BaseModel):
    """One explicit intake request, either broad-topic or structured-brief."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intake_mode: ResearchIntakeMode
    broad_topic: str | None = None
    structured_brief: str | None = None

    @field_validator("broad_topic", "structured_brief")
    @classmethod
    def require_nonblank_optional_text(cls, value: str | None) -> str | None:
        """Treat blank text as absent so an intake path cannot be bypassed."""
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def require_selected_intake_content(self) -> ResearchBriefPayload:
        """Require content for exactly the intake mode selected by the caller."""
        if self.intake_mode is ResearchIntakeMode.BROAD_TOPIC:
            if self.broad_topic is None or self.structured_brief is not None:
                raise ValueError("broad_topic intake requires only broad_topic")
        elif self.structured_brief is None or self.broad_topic is not None:
            raise ValueError("structured_brief intake requires only structured_brief")
        return self


class CandidateChartersPayload(BaseModel):
    """The exactly-three broad-topic options that must still pass Gate 1."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    brief: ResearchBriefPayload
    candidates: tuple[CandidateCharter, CandidateCharter, CandidateCharter]
    gate_one_required: Literal[True] = True

    @model_validator(mode="after")
    def require_broad_topic_candidates(self) -> CandidateChartersPayload:
        """Keep broad-topic output at exactly three mutually claimed options."""
        if self.brief.intake_mode is not ResearchIntakeMode.BROAD_TOPIC:
            raise ValueError("candidate charters require a broad_topic brief")
        _validate_candidate_set(self.candidates)
        return self


class ResearchCharterPayload(BaseModel):
    """One structured-brief draft charter awaiting mandatory Gate 1 approval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    brief: ResearchBriefPayload
    charter: CandidateCharter
    gate_one_required: Literal[True] = True

    @model_validator(mode="after")
    def require_structured_brief_draft(self) -> ResearchCharterPayload:
        """Ensure structured intake creates a draft rather than bypassing Gate 1."""
        if self.brief.intake_mode is not ResearchIntakeMode.STRUCTURED_BRIEF:
            raise ValueError("research charter requires a structured_brief")
        return self


def validate_three_distinct_candidates(
    candidates: tuple[CandidateCharter, ...],
) -> None:
    """Validate exactly three IDs, fingerprints, and complete pairwise claims."""
    if len(candidates) != 3:
        raise ValueError("three substantively distinct candidates are required")
    _validate_candidate_set(candidates)


def _validate_candidate_set(candidates: tuple[CandidateCharter, ...]) -> None:
    candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("three substantively distinct candidates are required")
    fingerprints = tuple(candidate.question_fingerprint for candidate in candidates)
    if len(set(fingerprints)) != len(fingerprints):
        raise ValueError("three substantively distinct candidates are required")
    candidate_id_set = set(candidate_ids)
    for candidate in candidates:
        targets = {claim.other_candidate_id for claim in candidate.distinctness_claims}
        if targets != candidate_id_set - {candidate.candidate_id}:
            raise ValueError("three substantively distinct candidates are required")
