"""Submission normalization policies applied before artifact promotion."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from envresearch.models.intake import CandidateChartersPayload
from envresearch.research.ranking import CharterRanker, CharterRankingPolicy


def apply_submission_policy(
    order_id: str,
    payload: BaseModel | dict[str, Any],
    ranking_policy: CharterRankingPolicy,
) -> BaseModel | dict[str, Any]:
    """Apply deterministic charter ranking while leaving other payloads intact."""
    if order_id != "frame-charters":
        return payload
    if not isinstance(payload, CandidateChartersPayload):
        raise TypeError("frame-charters requires candidate charter payload")
    return payload.model_copy(
        update={"candidates": CharterRanker(ranking_policy).rank(payload.candidates)}
    )


def accepted_major_ids(conditions: Mapping[str, object]) -> frozenset[str]:
    """Parse the exact unique human risk acceptances from Final Gate conditions."""
    raw_ids = conditions.get("accepted_major_ids", [])
    if not isinstance(raw_ids, list) or any(
        not isinstance(item, str) for item in raw_ids
    ):
        raise TypeError("final-gate accepted_major_ids must be a list of strings")
    accepted = frozenset(raw_ids)
    if len(accepted) != len(raw_ids):
        raise ValueError("final-gate accepted_major_ids must be unique")
    return accepted
