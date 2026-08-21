"""Focused service-reachable attacks for audit regression boundaries."""

from __future__ import annotations

from paper_audit_fixtures import AuditCase

from envresearch.paper._audit_prose import claim_sentence, output_caption


def methods_spacing_attack(case: AuditCase) -> AuditCase:
    """Place two verified methods spans around a forbidden two-space gap."""
    paragraph = next(
        item for item in case.draft.paragraphs if item.section == "methods"
    )
    separator = "  "
    second = case.draft.citation_bindings[0].model_copy(
        update={
            "start": len(paragraph.text) + len(separator),
            "end": len(paragraph.text) * 2 + len(separator),
        }
    )
    draft = case.draft.model_copy(
        update={
            "paragraphs": tuple(
                item.model_copy(
                    update={"text": f"{paragraph.text}{separator}{paragraph.text}"}
                )
                if item.paragraph_id == paragraph.paragraph_id
                else item
                for item in case.draft.paragraphs
            ),
            "citation_bindings": (*case.draft.citation_bindings, second),
        }
    )
    return _with_draft(case, draft)


def methods_numeric_attack(case: AuditCase) -> AuditCase:
    """Keep a whole methods sentence cited while introducing an unbound number."""
    text = "The policy applies to 10 eligible facilities."
    source_ref, sheet = case.snapshot.source_sheets[0]
    claim = sheet.claims[0].model_copy(update={"normalized_claim": text})
    rebound_sheet = sheet.model_copy(update={"claims": (claim,)})
    paragraph = next(
        item for item in case.draft.paragraphs if item.section == "methods"
    )
    draft = case.draft.model_copy(
        update={
            "paragraphs": tuple(
                item.model_copy(update={"text": text})
                if item.paragraph_id == paragraph.paragraph_id
                else item
                for item in case.draft.paragraphs
            ),
            "citation_bindings": (
                case.draft.citation_bindings[0].model_copy(update={"end": len(text)}),
            ),
        }
    )
    return AuditCase(
        draft=type(case.draft).model_validate(draft.model_dump(mode="python")),
        ledger=case.ledger,
        argument_map=case.argument_map,
        snapshot=case.snapshot.__class__(
            report=case.snapshot.report,
            source_sheets=((source_ref, rebound_sheet),),
            token=case.snapshot.token,
        ),
        draft_ref=case.draft_ref,
    )


def policy_warranted_title_attack(case: AuditCase) -> AuditCase:
    """Use ordinary prescriptive wording that still exceeds evidence scope."""
    return _with_draft(
        case,
        case.draft.model_copy(
            update={
                "paragraphs": tuple(
                    item.model_copy(
                        update={"text": "Immediate policy adoption is warranted."}
                    )
                    if item.section == "title"
                    else item
                    for item in case.draft.paragraphs
                )
            }
        ),
    )


def policy_ought_title_attack(case: AuditCase) -> AuditCase:
    """Use a service-reachable prescriptive title outside the closed renderer."""
    return _replace_unbound_section(
        case, "title", "Regulators ought to implement this program nationwide."
    )


def policy_universal_question_attack(case: AuditCase) -> AuditCase:
    """Use a service-reachable universal policy question outside the renderer."""
    return _replace_unbound_section(
        case, "research-question", "Should this apply to every household?"
    )


def nested_claim_overlaps_attack(case: AuditCase) -> AuditCase:
    """Nest two disjoint inner bindings under one outer claim span."""
    finding = next(
        item for item in case.draft.claim_bindings if item.purpose == "finding"
    )
    inner_left = finding.model_copy(update={"start": 1, "end": 10})
    inner_right = finding.model_copy(update={"start": 20, "end": 30})
    bindings = tuple(
        sorted(
            (*case.draft.claim_bindings, inner_left, inner_right),
            key=lambda item: (item.paragraph_id, item.start, item.end),
        )
    )
    return _with_draft(case, case.draft.model_copy(update={"claim_bindings": bindings}))


def nested_citation_overlaps_attack(case: AuditCase) -> AuditCase:
    """Nest two disjoint inner bindings under one outer citation span."""
    citation = case.draft.citation_bindings[0]
    inner_left = citation.model_copy(update={"start": 1, "end": 10})
    inner_right = citation.model_copy(update={"start": 20, "end": 30})
    bindings = tuple(
        sorted(
            (citation, inner_left, inner_right),
            key=lambda item: (item.paragraph_id, item.start, item.end),
        )
    )
    return _with_draft(
        case, case.draft.model_copy(update={"citation_bindings": bindings})
    )


def duplicate_citation_sources_attack(case: AuditCase) -> AuditCase:
    """Duplicate a typed source authority before the auditor's dict projection."""
    return AuditCase(
        draft=case.draft,
        ledger=case.ledger,
        argument_map=case.argument_map,
        snapshot=case.snapshot.__class__(
            report=case.snapshot.report,
            source_sheets=case.snapshot.source_sheets * 2,
            token=case.snapshot.token,
        ),
        draft_ref=case.draft_ref,
    )


def design_causal_result_attack(case: AuditCase) -> AuditCase:
    """Use a rebound design-causal claim whose exact result says impact."""
    original = case.ledger.claims[0]
    row = type(original).model_validate(
        {
            **original.model_dump(mode="python"),
            "claim_id": "contingent-valuation-impact",
            "claim_type": "effect-estimate",
            "quantity": "impact",
            "welfare_transformation": None,
            "allowed_strength": "design-based-causal",
        }
    )
    result_text = claim_sentence(row)
    paragraphs = tuple(
        item.model_copy(update={"text": result_text})
        if item.section == "results"
        else item
        for item in case.draft.paragraphs
    )
    claim_bindings = tuple(
        item.model_copy(
            update={
                "claim_ids": (row.claim_id,),
                "end": len(result_text) if item.purpose == "finding" else item.end,
                "allowed_strength": row.allowed_strength,
            }
        )
        for item in case.draft.claim_bindings
    )
    tables = tuple(
        item.model_copy(
            update={
                "claim_ids": (row.claim_id,),
                "caption": output_caption("table", (row,)),
            }
        )
        for item in case.draft.tables
    )
    figures = tuple(
        item.model_copy(
            update={
                "claim_ids": (row.claim_id,),
                "caption": output_caption("figure", (row,)),
            }
        )
        for item in case.draft.figures
    )
    node = case.argument_map.nodes[0].model_copy(update={"claim_ids": (row.claim_id,)})
    return AuditCase(
        draft=case.draft.model_copy(
            update={
                "paragraphs": paragraphs,
                "claim_bindings": claim_bindings,
                "tables": tables,
                "figures": figures,
            }
        ),
        ledger=case.ledger.model_copy(update={"claims": (row,)}),
        argument_map=case.argument_map.model_copy(update={"nodes": (node,)}),
        snapshot=case.snapshot,
        draft_ref=case.draft_ref,
    )


def _with_draft(case: AuditCase, draft: object) -> AuditCase:
    return AuditCase(
        draft=type(case.draft).model_validate(  # strict service-reachable payload
            draft.model_dump(mode="python")  # type: ignore[attr-defined]
        ),
        ledger=case.ledger,
        argument_map=case.argument_map,
        snapshot=case.snapshot,
        draft_ref=case.draft_ref,
    )


def _replace_unbound_section(case: AuditCase, section: str, text: str) -> AuditCase:
    return _with_draft(
        case,
        case.draft.model_copy(
            update={
                "paragraphs": tuple(
                    item.model_copy(update={"text": text})
                    if item.section == section
                    else item
                    for item in case.draft.paragraphs
                )
            }
        ),
    )


__all__ = [
    "design_causal_result_attack",
    "duplicate_citation_sources_attack",
    "methods_numeric_attack",
    "methods_spacing_attack",
    "nested_citation_overlaps_attack",
    "nested_claim_overlaps_attack",
    "policy_ought_title_attack",
    "policy_universal_question_attack",
    "policy_warranted_title_attack",
]
