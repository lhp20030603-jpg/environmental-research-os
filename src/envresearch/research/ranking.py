"""Deterministic transparent ranking for research charter candidates."""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from envresearch.models.intake import (
    SCORE_FIELDS,
    CandidateCharter,
    CharterScore,
    validate_three_distinct_candidates,
)


class CharterRankingPolicy(BaseModel):
    """Validated weights for the six published charter score dimensions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    weights: Mapping[str, float] = Field(
        default_factory=lambda: {name: 1 / 6 for name in SCORE_FIELDS}
    )

    @field_validator("weights", mode="before")
    @classmethod
    def require_numeric_weights(cls, value: object) -> object:
        """Reject coercive weight primitives while allowing integer weights."""
        if not isinstance(value, Mapping):
            return value
        if any(
            isinstance(weight, bool) or not isinstance(weight, (int, float))
            for weight in value.values()
        ):
            raise ValueError("weights must contain numeric values")
        return value

    @field_validator("weights")
    @classmethod
    def require_complete_normalized_weights(
        cls, value: Mapping[str, float]
    ) -> Mapping[str, float]:
        """Accept only finite nonnegative weights that sum to one."""
        if set(value) != set(SCORE_FIELDS):
            raise ValueError("weights must contain exactly the six score dimensions")
        if any(not math.isfinite(weight) or weight < 0 for weight in value.values()):
            raise ValueError("weights must be finite and nonnegative")
        if not math.isclose(sum(value.values()), 1.0, abs_tol=1e-9):
            raise ValueError("weights must sum to 1")
        return value

    @model_validator(mode="after")
    def freeze_weights(self) -> CharterRankingPolicy:
        """Prevent post-validation edits from invalidating the policy contract."""
        object.__setattr__(self, "weights", MappingProxyType(dict(self.weights)))
        return self

    @field_serializer("weights")
    def serialize_weights(self, value: Mapping[str, float]) -> dict[str, float]:
        """Serialize a detached mapping while retaining immutable runtime state."""
        return dict(value)


class CharterRanker:
    """Compute ranker-owned totals and stable candidate ordering."""

    def __init__(self, policy: CharterRankingPolicy) -> None:
        self._policy = policy

    def rank(
        self, candidates: tuple[CandidateCharter, ...]
    ) -> tuple[CandidateCharter, ...]:
        """Return three valid candidates by total descending then ID ascending."""
        validate_three_distinct_candidates(candidates)
        scored = tuple(
            candidate.with_total(self._weighted_total(candidate.scores))
            for candidate in candidates
        )
        return tuple(
            sorted(
                scored,
                key=lambda candidate: (
                    -candidate.total_score if candidate.total_score is not None else 0.0,
                    candidate.candidate_id,
                ),
            )
        )

    def _weighted_total(self, scores: Mapping[str, CharterScore]) -> float:
        """Calculate a policy-weighted total from validated fixed dimensions."""
        return sum(
            self._policy.weights[dimension] * scores[dimension].score
            for dimension in SCORE_FIELDS
        )
