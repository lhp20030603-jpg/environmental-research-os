"""Canonical prose rendering and coverage checks for paper drafts."""

from __future__ import annotations

import re

from envresearch.paper.contracts import (
    ClaimEvidenceRow,
    DescriptiveRangeValue,
    DescriptiveSeriesValue,
    EstimatedClaimValue,
)
from envresearch.paper.draft_contracts import (
    CitationBinding,
    ClaimSpanBinding,
    PaperParagraph,
)
from envresearch.paper.errors import PaperScopeExceeded, PaperSupportInvalid

NUMBER_PATTERN = r"(?<![A-Za-z])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
NUMBER = re.compile(NUMBER_PATTERN)
POLICY_OVERREACH = re.compile(
    r"\b(?:proves?|guarantees?|policymakers? must|must adopt|should adopt)\b",
    re.IGNORECASE,
)
CAUSAL_LANGUAGE = re.compile(
    r"\b(?:causes?|caused|causal effect|led to|increased|reduced|impact|results in)\b",
    re.IGNORECASE,
)


def render_claim_sentence(claim: ClaimEvidenceRow) -> str:
    """Render one canonical empirical sentence directly from a ledger row."""
    quantity = claim.quantity.replace("-", " ")
    scope = (
        f"for {claim.population_basis} on a {claim.time_basis} basis "
        f"in {claim.price_base}"
    )
    value = claim.value
    if isinstance(value, EstimatedClaimValue):
        confidence = number(value.uncertainty.confidence_level * 100)
        return (
            f"The registered {quantity} is {number(value.estimate)} {claim.unit} "
            f"{scope}, with a {confidence}% confidence interval from "
            f"{number(value.uncertainty.confidence_low)} to "
            f"{number(value.uncertainty.confidence_high)}."
        )
    if isinstance(value, DescriptiveRangeValue):
        return (
            f"The registered descriptive {quantity} ranges from "
            f"{number(value.minimum)} to {number(value.maximum)} {claim.unit} "
            f"{scope}."
        )
    assert isinstance(value, DescriptiveSeriesValue)
    points = "; ".join(
        (
            f"{number(point.x)} {value.x_unit}: {point.numerator} of "
            f"{point.denominator} ({number(point.value)} {value.y_unit})"
        )
        for point in value.points
    )
    return f"The registered descriptive {quantity} for {scope} is {points}."


def render_output_caption(kind: str, claims: tuple[ClaimEvidenceRow, ...]) -> str:
    """Render an output caption from only its exact registered claim identities."""
    claim_ids = ", ".join(claim.claim_id for claim in claims)
    return f"Registered {kind} output for claim {claim_ids}."


def require_results_coverage(
    paragraphs: tuple[PaperParagraph, ...], bindings: tuple[ClaimSpanBinding, ...]
) -> None:
    for paragraph in paragraphs:
        if paragraph.section != "results":
            continue
        spans = tuple(
            item
            for item in bindings
            if item.paragraph_id == paragraph.paragraph_id and item.purpose == "finding"
        )
        if any(
            not character.isspace()
            and not any(item.start <= index < item.end for item in spans)
            for index, character in enumerate(paragraph.text)
        ):
            raise PaperSupportInvalid(
                "every results sentence requires an exact claim span",
                finding_kind="result-span-missing",
            )


def require_limitations_coverage(
    paragraphs: tuple[PaperParagraph, ...], bindings: tuple[ClaimSpanBinding, ...]
) -> None:
    for paragraph in paragraphs:
        if paragraph.section != "limitations":
            continue
        spans = tuple(
            item
            for item in bindings
            if item.paragraph_id == paragraph.paragraph_id
            and item.purpose == "limitation"
        )
        if any(
            not character.isspace()
            and not any(item.start <= index < item.end for item in spans)
            for index, character in enumerate(paragraph.text)
        ):
            raise PaperSupportInvalid(
                "every limitations statement requires an exact claim span",
                finding_kind="limitation-span-missing",
            )


def require_methods_coverage(
    paragraphs: tuple[PaperParagraph, ...], bindings: tuple[CitationBinding, ...]
) -> None:
    for paragraph in paragraphs:
        if paragraph.section != "methods":
            continue
        spans = sorted(
            (item.start, item.end)
            for item in bindings
            if item.paragraph_id == paragraph.paragraph_id
        )
        cursor = 0
        for start, end in spans:
            if paragraph.text[cursor:start] not in {"", " "}:
                raise PaperSupportInvalid(
                    "methods prose must be completely covered by verified citations",
                    finding_kind="methods-citation-missing",
                )
            cursor = end
        if not spans or paragraph.text[cursor:] != "":
            raise PaperSupportInvalid(
                "methods prose must be completely covered by verified citations",
                finding_kind="methods-citation-missing",
            )


def require_safe_unbound_sections(paragraphs: tuple[PaperParagraph, ...]) -> None:
    for paragraph in paragraphs:
        if paragraph.section == "validation-scope":
            raise PaperSupportInvalid(
                "validation-scope prose requires typed failed-case authority",
                finding_kind="validation-scope-unsupported",
            )
        if paragraph.section in {"title", "research-question"} and (
            NUMBER.search(paragraph.text)
            or POLICY_OVERREACH.search(paragraph.text)
            or CAUSAL_LANGUAGE.search(paragraph.text)
        ):
            raise PaperScopeExceeded(
                "title or research question contains unsupported factual language",
                finding_kind="unbound-factual-language",
            )


def require_numbers_are_bound(
    paragraphs: tuple[PaperParagraph, ...], bindings: tuple[ClaimSpanBinding, ...]
) -> None:
    for paragraph in paragraphs:
        spans = tuple(
            item for item in bindings if item.paragraph_id == paragraph.paragraph_id
        )
        for match in NUMBER.finditer(paragraph.text):
            if not any(
                item.start <= match.start() and match.end() <= item.end
                for item in spans
            ):
                raise PaperSupportInvalid(
                    "numeric token is not bound to registered claim evidence",
                    finding_kind="numeric-token-unbound",
                )


def number(value: float) -> str:
    return format(value, ".15g")


__all__ = [
    "CAUSAL_LANGUAGE",
    "POLICY_OVERREACH",
    "render_claim_sentence",
    "render_output_caption",
    "require_limitations_coverage",
    "require_methods_coverage",
    "require_numbers_are_bound",
    "require_results_coverage",
    "require_safe_unbound_sections",
]
