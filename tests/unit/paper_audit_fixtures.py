"""Typed audit attacks over one deterministic evidence-bound paper draft."""

from __future__ import annotations

from dataclasses import dataclass

from paper_draft_fixtures import materialized_draft

from envresearch.models.artifact import ArtifactRef
from envresearch.paper.argument_contracts import ArgumentMap
from envresearch.paper.citation_authority import CitationAuthoritySnapshot
from envresearch.paper.contracts import ClaimEvidenceLedger
from envresearch.paper.draft_contracts import PaperDraft, PaperParagraph


@dataclass(frozen=True, slots=True)
class AuditCase:
    draft: PaperDraft
    ledger: ClaimEvidenceLedger
    argument_map: ArgumentMap
    snapshot: CitationAuthoritySnapshot
    draft_ref: ArtifactRef


def base_case() -> AuditCase:
    draft, ledger, argument_map, snapshot, map_ref, ledger_ref, report_ref = (
        materialized_draft()
    )
    return AuditCase(
        draft=draft.model_copy(
            update={
                "map_ref": map_ref,
                "ledger_ref": ledger_ref,
                "citation_report_ref": report_ref,
            }
        ),
        ledger=ledger,
        argument_map=argument_map,
        snapshot=snapshot,
        draft_ref=ArtifactRef(
            artifact_id="paper-draft-authority",
            artifact_version=1,
            content_hash="f" * 64,
        ),
    )


def replace_paragraph(case: AuditCase, section: str, text: str) -> AuditCase:
    return _with_draft(
        case,
        case.draft.model_copy(
            update={
                "paragraphs": tuple(
                    paragraph.model_copy(update={"text": text})
                    if paragraph.section == section
                    else paragraph
                    for paragraph in case.draft.paragraphs
                )
            }
        ),
    )


def citation_attack(case: AuditCase) -> AuditCase:
    paragraph = _paragraph(case, "methods")
    return replace_paragraph(
        case, "methods", paragraph.text.replace("eligible", "ineligible")
    )


def unbound_methods_attack(case: AuditCase) -> AuditCase:
    paragraph = _paragraph(case, "methods")
    return replace_paragraph(
        case, "methods", f"{paragraph.text} Unsupported methodological prose."
    )


def disjoint_results_coverage_attack(case: AuditCase) -> AuditCase:
    paragraph = _paragraph(case, "results")
    prefix = "Prefix. "
    suffix = " Suffix."
    draft = case.draft.model_copy(
        update={
            "paragraphs": tuple(
                item.model_copy(update={"text": f"{prefix}{item.text}{suffix}"})
                if item.paragraph_id == paragraph.paragraph_id
                else item
                for item in case.draft.paragraphs
            ),
            "claim_bindings": tuple(
                item.model_copy(
                    update={
                        "start": item.start + len(prefix),
                        "end": item.end + len(prefix),
                    }
                )
                if item.paragraph_id == paragraph.paragraph_id
                else item
                for item in case.draft.claim_bindings
            ),
        }
    )
    return _with_draft(case, draft)


def numeric_attack(case: AuditCase) -> AuditCase:
    paragraph = _paragraph(case, "results")
    return replace_paragraph(case, "results", paragraph.text.replace("1.25", "9.99", 1))


def output_attack(case: AuditCase, kind: str) -> AuditCase:
    field = "tables" if kind == "table" else "figures"
    binding = getattr(case.draft, field)[0]
    forged = binding.model_copy(
        update={"output": binding.output.model_copy(update={"sha256": "7" * 64})}
    )
    return _with_draft(case, case.draft.model_copy(update={field: (forged,)}))


def strength_attack(case: AuditCase) -> AuditCase:
    return _claim_field_attack(case, "allowed_strength", "design-based-causal")


def policy_attack(case: AuditCase) -> AuditCase:
    return replace_paragraph(case, "title", "Policymakers must adopt this program.")


def title_number_attack(case: AuditCase) -> AuditCase:
    return replace_paragraph(case, "title", "The registered value is 999 USD.")


def question_number_attack(case: AuditCase) -> AuditCase:
    return replace_paragraph(case, "research-question", "Is the value 999 USD?")


def unbound_causal_attack(case: AuditCase, section: str) -> AuditCase:
    row = case.ledger.claims[0].model_copy(
        update={
            "claim_type": "effect-estimate",
            "allowed_strength": "design-based-causal",
        }
    )
    ledger = case.ledger.model_copy(update={"claims": (row,)})
    text = (
        "The program caused better outcomes."
        if section == "title"
        else "Did the program cause better outcomes?"
    )
    attacked = replace_paragraph(case, section, text)
    return AuditCase(
        draft=attacked.draft.model_copy(
            update={
                "claim_bindings": tuple(
                    item.model_copy(update={"allowed_strength": "design-based-causal"})
                    for item in attacked.draft.claim_bindings
                )
            }
        ),
        ledger=ledger,
        argument_map=attacked.argument_map,
        snapshot=attacked.snapshot,
        draft_ref=attacked.draft_ref,
    )


def basis_attack(case: AuditCase, field: str, value: str) -> AuditCase:
    return _claim_field_attack(case, field, value)


def scope_attack(case: AuditCase) -> AuditCase:
    return replace_paragraph(
        case,
        "limitations",
        "This limitation is not registered by the accepted evidence.",
    )


def unregistered_validator_attack(case: AuditCase) -> AuditCase:
    from dataclasses import replace

    report_ref, report = case.snapshot.report
    return AuditCase(
        draft=case.draft,
        ledger=case.ledger,
        argument_map=case.argument_map,
        snapshot=case.snapshot.__class__(
            report=(
                report_ref,
                replace(report, validator_version="unregistered-validator-v99"),
            ),
            source_sheets=case.snapshot.source_sheets,
            token=case.snapshot.token,
        ),
        draft_ref=case.draft_ref,
    )


def no_empirical_node_attack(case: AuditCase) -> AuditCase:
    node = case.argument_map.nodes[0].model_copy(
        update={
            "node_id": "research-question-node",
            "node_type": "research-question",
            "proposition": "What valuation evidence is registered?",
            "claim_ids": (),
        }
    )
    return AuditCase(
        draft=case.draft,
        ledger=case.ledger,
        argument_map=case.argument_map.model_copy(update={"nodes": (node,)}),
        snapshot=case.snapshot,
        draft_ref=case.draft_ref,
    )


def validation_scope_attack(case: AuditCase) -> AuditCase:
    text = "Validation remained within the registered local scope."
    paragraph = PaperParagraph(
        paragraph_id="validation-boundary",
        position=len(case.draft.paragraphs),
        section="validation-scope",
        text=text,
    )
    binding = case.draft.claim_bindings[0].model_copy(
        update={
            "paragraph_id": paragraph.paragraph_id,
            "start": 0,
            "end": len(text),
            "purpose": "validation-scope",
        }
    )
    draft = case.draft.model_copy(
        update={
            "paragraphs": (*case.draft.paragraphs, paragraph),
            "claim_bindings": tuple(
                sorted(
                    (*case.draft.claim_bindings, binding),
                    key=lambda item: (item.paragraph_id, item.start, item.end),
                )
            ),
        }
    )
    return _with_draft(case, draft)


def citation_token_attack(case: AuditCase) -> AuditCase:
    token = case.snapshot.token.__class__(
        report_ref=case.draft_ref,
        report_payload_sha256=case.snapshot.token.report_payload_sha256,
        source_generation=case.snapshot.token.source_generation,
        source_anchor_sha256=case.snapshot.token.source_anchor_sha256,
    )
    return AuditCase(
        draft=case.draft,
        ledger=case.ledger,
        argument_map=case.argument_map,
        snapshot=case.snapshot.__class__(
            report=case.snapshot.report,
            source_sheets=case.snapshot.source_sheets,
            token=token,
        ),
        draft_ref=case.draft_ref,
    )


def cross_section_attack(case: AuditCase) -> AuditCase:
    primary = case.ledger.claims[0]
    secondary = case.ledger.claims[0].model_copy(
        update={
            "claim_id": "contingent-valuation-secondary",
            "quantity": "secondary",
        }
    )
    # Revalidate the copied row with its service-reachable identity.
    secondary = type(primary).model_validate(secondary.model_dump(mode="python"))
    ledger = case.ledger.model_copy(update={"claims": (primary, secondary)})
    node = case.argument_map.nodes[0].model_copy(
        update={"claim_ids": (primary.claim_id, secondary.claim_id)}
    )
    argument_map = case.argument_map.model_copy(update={"nodes": (node,)})
    bindings = tuple(
        binding.model_copy(update={"claim_ids": (secondary.claim_id,)})
        if binding.purpose == "limitation"
        else binding
        for binding in case.draft.claim_bindings
    )
    return AuditCase(
        draft=case.draft.model_copy(update={"claim_bindings": bindings}),
        ledger=ledger,
        argument_map=argument_map,
        snapshot=case.snapshot,
        draft_ref=case.draft_ref,
    )


def _claim_field_attack(case: AuditCase, field: str, value: str) -> AuditCase:
    return _with_draft(
        case,
        case.draft.model_copy(
            update={
                "claim_bindings": tuple(
                    binding.model_copy(update={field: value})
                    for binding in case.draft.claim_bindings
                )
            }
        ),
    )


def _paragraph(case: AuditCase, section: str):  # type: ignore[no-untyped-def]
    return next(item for item in case.draft.paragraphs if item.section == section)


def _with_draft(case: AuditCase, draft: PaperDraft) -> AuditCase:
    return AuditCase(
        draft=draft,
        ledger=case.ledger,
        argument_map=case.argument_map,
        snapshot=case.snapshot,
        draft_ref=case.draft_ref,
    )


def roundtrip_case(case: AuditCase) -> AuditCase:
    """Require every attack to remain reachable through strict service payloads."""
    return AuditCase(
        draft=PaperDraft.model_validate(case.draft.model_dump(mode="python")),
        ledger=ClaimEvidenceLedger.model_validate(
            case.ledger.model_dump(mode="python")
        ),
        argument_map=ArgumentMap.model_validate(
            case.argument_map.model_dump(mode="python")
        ),
        snapshot=case.snapshot,
        draft_ref=case.draft_ref,
    )


__all__ = [
    "AuditCase",
    "base_case",
    "basis_attack",
    "citation_attack",
    "citation_token_attack",
    "cross_section_attack",
    "disjoint_results_coverage_attack",
    "no_empirical_node_attack",
    "numeric_attack",
    "output_attack",
    "policy_attack",
    "question_number_attack",
    "roundtrip_case",
    "scope_attack",
    "strength_attack",
    "title_number_attack",
    "unbound_causal_attack",
    "unbound_methods_attack",
    "unregistered_validator_attack",
    "validation_scope_attack",
]
