"""Auditor-owned prose patterns and typed evidence reconstruction."""

from __future__ import annotations

import re

from envresearch.paper.contracts import (
    ClaimEvidenceRow,
    DescriptiveRangeValue,
    DescriptiveSeriesValue,
    EstimatedClaimValue,
)

NUMBER = re.compile(r"(?<![A-Za-z])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
POLICY_OVERREACH = re.compile(
    (
        r"\b(?:proves?|guarantees?|policymakers? must|must adopt|should adopt|"
        r"policy adoption is warranted)\b"
    ),
    re.IGNORECASE,
)
CAUSAL_LANGUAGE = re.compile(
    r"\b(?:causes?|caused|causal effect|led to|increased|reduced|impact|results in)\b",
    re.IGNORECASE,
)
SAFE_TITLES = frozenset(
    {
        "Registered contingent valuation evidence",
        "Registered environmental valuation evidence",
    }
)
SAFE_RESEARCH_QUESTION = "What value is supported within the registered model boundary?"


def claim_sentence(claim: ClaimEvidenceRow) -> str:
    """Reconstruct accepted prose directly from typed ledger fields."""
    quantity = claim.quantity.replace("-", " ")
    scope = (
        f"for {claim.population_basis} on a {claim.time_basis} basis "
        f"in {claim.price_base}"
    )
    value = claim.value
    if isinstance(value, EstimatedClaimValue):
        confidence = _number(value.uncertainty.confidence_level * 100)
        return (
            f"The registered {quantity} is {_number(value.estimate)} {claim.unit} "
            f"{scope}, with a {confidence}% confidence interval from "
            f"{_number(value.uncertainty.confidence_low)} to "
            f"{_number(value.uncertainty.confidence_high)}."
        )
    if isinstance(value, DescriptiveRangeValue):
        return (
            f"The registered descriptive {quantity} ranges from "
            f"{_number(value.minimum)} to {_number(value.maximum)} {claim.unit} "
            f"{scope}."
        )
    assert isinstance(value, DescriptiveSeriesValue)
    points = "; ".join(
        (
            f"{_number(point.x)} {value.x_unit}: {point.numerator} of "
            f"{point.denominator} ({_number(point.value)} {value.y_unit})"
        )
        for point in value.points
    )
    return f"The registered descriptive {quantity} for {scope} is {points}."


def output_caption(kind: str, claims: tuple[ClaimEvidenceRow, ...]) -> str:
    claim_ids = ", ".join(claim.claim_id for claim in claims)
    return f"Registered {kind} output for claim {claim_ids}."


def _number(value: float) -> str:
    return format(value, ".15g")


__all__ = [
    "CAUSAL_LANGUAGE",
    "NUMBER",
    "POLICY_OVERREACH",
    "claim_sentence",
    "output_caption",
]
