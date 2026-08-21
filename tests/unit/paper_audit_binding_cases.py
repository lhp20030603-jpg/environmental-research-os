"""Strict draft-binding attacks kept separate from core audit fixtures."""

from __future__ import annotations

from paper_audit_fixtures import AuditCase

from envresearch.paper.draft_contracts import PaperParagraph


def missing_results_binding_attack(case: AuditCase) -> AuditCase:
    finding = next(
        item for item in case.draft.claim_bindings if item.purpose == "finding"
    )
    bindings = tuple(
        item.model_copy(
            update={"paragraph_id": "paper-title", "end": 1, "purpose": "limitation"}
        )
        if item is finding
        else item
        for item in case.draft.claim_bindings
    )
    return _with_draft(case, claim_bindings=bindings)


def dangling_claim_binding_attack(case: AuditCase) -> AuditCase:
    bindings = tuple(
        item.model_copy(update={"paragraph_id": "missing-results"})
        if item.purpose == "finding"
        else item
        for item in case.draft.claim_bindings
    )
    return _with_draft(case, claim_bindings=bindings)


def dangling_citation_binding_attack(case: AuditCase) -> AuditCase:
    citation = case.draft.citation_bindings[0].model_copy(
        update={"paragraph_id": "missing-methods"}
    )
    return _with_draft(case, citation_bindings=(citation,))


def overlapping_claim_bindings_attack(case: AuditCase) -> AuditCase:
    finding = next(
        item for item in case.draft.claim_bindings if item.purpose == "finding"
    )
    overlap = finding.model_copy(update={"start": 1})
    bindings = tuple(
        sorted(
            (*case.draft.claim_bindings, overlap),
            key=lambda item: (item.paragraph_id, item.start, item.end),
        )
    )
    return _with_draft(case, claim_bindings=bindings)


def overlapping_citation_bindings_attack(case: AuditCase) -> AuditCase:
    citation = case.draft.citation_bindings[0]
    overlap = citation.model_copy(update={"start": 1})
    bindings = tuple(
        sorted(
            (citation, overlap),
            key=lambda item: (item.paragraph_id, item.start, item.end),
        )
    )
    return _with_draft(case, citation_bindings=bindings)


def unbound_validation_scope_attack(case: AuditCase) -> AuditCase:
    paragraph = PaperParagraph(
        paragraph_id="validation-boundary",
        position=len(case.draft.paragraphs),
        section="validation-scope",
        text="Validation remained within the registered local scope.",
    )
    return _with_draft(case, paragraphs=(*case.draft.paragraphs, paragraph))


def oversized_claim_span_attack(case: AuditCase) -> AuditCase:
    bindings = tuple(
        item.model_copy(update={"end": 151}) if item.purpose == "finding" else item
        for item in case.draft.claim_bindings
    )
    return _with_draft(case, claim_bindings=bindings)


def purpose_section_attack(case: AuditCase) -> AuditCase:
    bindings = tuple(
        item.model_copy(update={"purpose": "limitation"})
        if item.purpose == "finding"
        else item
        for item in case.draft.claim_bindings
    )
    return _with_draft(case, claim_bindings=bindings)


def _with_draft(case: AuditCase, **updates: object) -> AuditCase:
    return AuditCase(
        draft=case.draft.model_copy(update=updates),
        ledger=case.ledger,
        argument_map=case.argument_map,
        snapshot=case.snapshot,
        draft_ref=case.draft_ref,
    )


__all__ = [
    "dangling_citation_binding_attack",
    "dangling_claim_binding_attack",
    "missing_results_binding_attack",
    "overlapping_citation_bindings_attack",
    "overlapping_claim_bindings_attack",
    "oversized_claim_span_attack",
    "purpose_section_attack",
    "unbound_validation_scope_attack",
]
