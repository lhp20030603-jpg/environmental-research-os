"""Section-coverage and cross-section checks for independent paper audit."""

from __future__ import annotations

from envresearch.paper._audit_findings import AuditCollector
from envresearch.paper.audit_contracts import FindingKind
from envresearch.paper.draft_contracts import (
    CitationBinding,
    ClaimSpanBinding,
    PaperDraft,
    PaperParagraph,
)


def audit_section_coverage(collector: AuditCollector, draft: PaperDraft) -> None:
    """Find every non-whitespace manuscript character lacking its typed binding."""
    _audit_binding_overlaps(collector, draft)
    for paragraph in draft.paragraphs:
        if paragraph.section == "methods":
            spans = tuple(
                (item.start, item.end)
                for item in draft.citation_bindings
                if item.paragraph_id == paragraph.paragraph_id
            )
            kind: FindingKind = "citation-mismatch"
        elif paragraph.section == "results":
            spans = tuple(
                (item.start, item.end)
                for item in draft.claim_bindings
                if item.paragraph_id == paragraph.paragraph_id
                and item.purpose == "finding"
            )
            kind = "scope-inconsistency"
        elif paragraph.section == "limitations":
            spans = tuple(
                (item.start, item.end)
                for item in draft.claim_bindings
                if item.paragraph_id == paragraph.paragraph_id
                and item.purpose == "limitation"
            )
            kind = "scope-inconsistency"
        elif paragraph.section == "validation-scope":
            spans = tuple(
                (item.start, item.end)
                for item in draft.claim_bindings
                if item.paragraph_id == paragraph.paragraph_id
                and item.purpose == "validation-scope"
            )
            kind = "scope-inconsistency"
        else:
            continue
        unsupported = tuple(
            index
            for index in range(len(paragraph.text))
            if not any(start <= index < end for start, end in spans)
        )
        runs = (
            _methods_runs(paragraph.text, spans)
            if paragraph.section == "methods"
            else _nonblank_runs(paragraph.text, unsupported)
        )
        for start, end in runs:
            collector.text(
                kind,
                paragraph,
                start=start,
                end=end,
            )


def _audit_binding_overlaps(collector: AuditCollector, draft: PaperDraft) -> None:
    _audit_claim_overlaps(collector, draft.claim_bindings)
    _audit_citation_overlaps(collector, draft.citation_bindings)


def _audit_claim_overlaps(
    collector: AuditCollector, bindings: tuple[ClaimSpanBinding, ...]
) -> None:
    ordered = tuple(
        sorted(bindings, key=lambda item: (item.paragraph_id, item.start, item.end))
    )
    active_paragraph = ""
    active_end = -1
    for current in ordered:
        if current.paragraph_id != active_paragraph:
            active_paragraph = current.paragraph_id
            active_end = current.end
            continue
        if current.start < active_end:
            collector.binding("scope-inconsistency", current)
        active_end = max(active_end, current.end)


def _audit_citation_overlaps(
    collector: AuditCollector, bindings: tuple[CitationBinding, ...]
) -> None:
    ordered = tuple(
        sorted(bindings, key=lambda item: (item.paragraph_id, item.start, item.end))
    )
    active_paragraph = ""
    active_end = -1
    for current in ordered:
        if current.paragraph_id != active_paragraph:
            active_paragraph = current.paragraph_id
            active_end = current.end
            continue
        if current.start < active_end:
            collector.binding("citation-mismatch", current)
        active_end = max(active_end, current.end)


def _methods_runs(
    text: str, spans: tuple[tuple[int, int], ...]
) -> tuple[tuple[int, int], ...]:
    """Mirror the one-space-only methods boundary without Task 3 imports."""
    runs: list[tuple[int, int]] = []
    cursor = 0
    for start, end in sorted(spans):
        gap = text[cursor:start]
        if gap not in {"", " "}:
            if gap.isspace():
                runs.append((cursor, start))
            else:
                _append_nonblank(runs, text, cursor, start)
        cursor = max(cursor, end)
    if not spans:
        _append_nonblank(runs, text, 0, len(text))
    elif cursor < len(text):
        _append_nonblank(runs, text, cursor, len(text))
    return tuple(runs)


def _nonblank_runs(text: str, indexes: tuple[int, ...]) -> tuple[tuple[int, int], ...]:
    """Return unsupported regions with internal, but not boundary, whitespace."""
    if not indexes:
        return ()
    runs: list[tuple[int, int]] = []
    start = previous = indexes[0]
    for index in indexes[1:]:
        if index != previous + 1:
            _append_nonblank(runs, text, start, previous + 1)
            start = index
        previous = index
    _append_nonblank(runs, text, start, previous + 1)
    return tuple(runs)


def _append_nonblank(
    runs: list[tuple[int, int]], text: str, start: int, end: int
) -> None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start < end:
        runs.append((start, end))


def audit_cross_sections(
    collector: AuditCollector, paragraphs: dict[str, PaperParagraph]
) -> None:
    by_claim: dict[str, list[ClaimSpanBinding]] = {}
    for binding in collector.draft.claim_bindings:
        for claim_id in binding.claim_ids:
            by_claim.setdefault(claim_id, []).append(binding)
    for claim_id, bindings in by_claim.items():
        scopes = {
            (
                item.allowed_strength,
                item.unit,
                item.population_basis,
                item.time_basis,
                item.price_base,
            )
            for item in bindings
        }
        if len(scopes) > 1:
            item = bindings[-1]
            paragraph = paragraphs.get(item.paragraph_id)
            if paragraph is None or item.end > len(paragraph.text):
                collector.binding("cross-section-contradiction", item)
            else:
                collector.text(
                    "cross-section-contradiction",
                    paragraph,
                    start=item.start,
                    end=item.end,
                    claim_ids=(claim_id,),
                )
    finding_claims = {
        claim_id
        for binding in collector.draft.claim_bindings
        if binding.purpose == "finding"
        for claim_id in binding.claim_ids
    }
    limitation_bindings = tuple(
        binding
        for binding in collector.draft.claim_bindings
        if binding.purpose == "limitation"
    )
    limitation_claims = {
        claim_id for binding in limitation_bindings for claim_id in binding.claim_ids
    }
    if (
        finding_claims
        and limitation_claims
        and finding_claims != limitation_claims
        and limitation_bindings
    ):
        item = limitation_bindings[0]
        paragraph = paragraphs.get(item.paragraph_id)
        if paragraph is None or item.end > len(paragraph.text):
            collector.binding("cross-section-contradiction", item)
        else:
            collector.text(
                "cross-section-contradiction",
                paragraph,
                start=item.start,
                end=item.end,
                claim_ids=finding_claims | limitation_claims,
            )


__all__ = ["audit_cross_sections", "audit_section_coverage"]
