"""Independent, accumulating reconstruction of paper-audit findings."""

from __future__ import annotations

from envresearch.benchmarks.claim_report import (
    CitationIntegrityReport,
    report_binding_is_valid,
)
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_claims import ClaimVerificationStatus
from envresearch.paper._audit_findings import AuditCollector
from envresearch.paper._audit_prose import (
    CAUSAL_LANGUAGE,
    NUMBER,
    POLICY_OVERREACH,
    SAFE_RESEARCH_QUESTION,
    SAFE_TITLES,
    claim_sentence,
    output_caption,
)
from envresearch.paper._audit_sections import (
    audit_cross_sections,
    audit_section_coverage,
)
from envresearch.paper.argument_contracts import ArgumentMap
from envresearch.paper.audit_contracts import (
    FindingKind,
    PaperAuditFinding,
    artifact_ref_key,
)
from envresearch.paper.citation_authority import CitationAuthoritySnapshot
from envresearch.paper.contracts import ClaimEvidenceLedger, ClaimEvidenceRow
from envresearch.paper.draft_contracts import (
    FigureBinding,
    PaperDraft,
    PaperParagraph,
    TableBinding,
)


def reconstruct_audit_findings(
    *,
    draft_ref: ArtifactRef,
    draft: PaperDraft,
    argument_map: ArgumentMap,
    ledger: ClaimEvidenceLedger,
    citation_snapshot: CitationAuthoritySnapshot,
) -> tuple[PaperAuditFinding, ...]:
    """Return all independently observable findings without fail-fast validation."""
    claim_ids = tuple(
        sorted(
            {
                claim_id
                for node in argument_map.nodes
                if node.node_type == "empirical-claim"
                for claim_id in node.claim_ids
            }
        )
    )
    provenance_claim_ids = claim_ids or tuple(item.claim_id for item in ledger.claims)
    collector = AuditCollector(
        draft_ref=draft_ref,
        draft=draft,
        upstream_refs=audit_transitive_refs(
            draft_ref, draft, argument_map, ledger, citation_snapshot
        ),
        default_claim_ids=provenance_claim_ids,
        claims={item.claim_id: item for item in ledger.claims},
    )
    paragraphs = {item.paragraph_id: item for item in draft.paragraphs}
    claims = {item.claim_id: item for item in ledger.claims}
    _audit_citations(collector, paragraphs, citation_snapshot)
    _audit_claim_spans(
        collector,
        paragraphs,
        claims,
        argument_claim_ids=frozenset(claim_ids),
    )
    audit_section_coverage(collector, draft)
    _audit_outputs(
        collector,
        claims,
        (*draft.tables, *draft.figures),
        argument_claim_ids=frozenset(claim_ids),
    )
    _audit_policy_and_strength(collector, draft.paragraphs, claims)
    audit_cross_sections(collector, paragraphs)
    return tuple(sorted(collector.findings.values(), key=lambda item: item.finding_id))


def audit_transitive_refs(
    draft_ref: ArtifactRef,
    draft: PaperDraft,
    argument_map: ArgumentMap,
    ledger: ClaimEvidenceLedger,
    citation_snapshot: CitationAuthoritySnapshot,
) -> tuple[ArtifactRef, ...]:
    """Materialize the canonical complete exact-reference audit authority set."""
    _, report = citation_snapshot.report
    refs = {
        draft_ref,
        draft.map_ref,
        draft.ledger_ref,
        draft.citation_report_ref,
        argument_map.transition_ref,
        ledger.transition_ref,
        *(item.transition_ref for item in ledger.claims),
        *(item.snapshot_ref for item in ledger.claims),
        *(item[0] for item in citation_snapshot.source_sheets),
        *report.source_sheet_refs,
        *report.claim_fact_map_refs,
        *report.blinded_brief_refs,
        *report.accepted_artifact_refs,
    }
    return tuple(sorted(refs, key=artifact_ref_key))


def _audit_citations(
    collector: AuditCollector,
    paragraphs: dict[str, PaperParagraph],
    snapshot: CitationAuthoritySnapshot,
) -> None:
    report_ref, report = snapshot.report
    source_refs = tuple(ref for ref, _ in snapshot.source_sheets)
    sources = {ref: sheet for ref, sheet in snapshot.source_sheets}
    report_valid = (
        type(report) is CitationIntegrityReport
        and report_ref.artifact_id == "citation-integrity-report"
        and snapshot.token.report_ref == report_ref
        and report.passed
        and not report.findings
        and report.validator_version == "claim-integrity-v1"
        and report_binding_is_valid(report)
        and source_refs == report.source_sheet_refs
        and source_refs == tuple(sorted(source_refs, key=str))
        and len(source_refs) == len(set(source_refs))
        and collector.draft.citation_report_ref == report_ref
    )
    for binding in collector.draft.citation_bindings:
        paragraph = paragraphs.get(binding.paragraph_id)
        source = sources.get(binding.source_sheet_ref)
        claim = (
            next(
                (item for item in source.claims if item.claim_id == binding.claim_id),
                None,
            )
            if source is not None
            else None
        )
        selected = (
            paragraph.text[binding.start : binding.end]
            if paragraph is not None and binding.end <= len(paragraph.text)
            else None
        )
        if paragraph is None or binding.end > len(paragraph.text):
            collector.binding("citation-mismatch", binding)
        elif (
            not report_valid
            or claim is None
            or claim.status is not ClaimVerificationStatus.CLAIM_VERIFIED
            or selected != claim.normalized_claim
        ):
            collector.text(
                "citation-mismatch",
                paragraph,
                start=binding.start,
                end=binding.end,
                claim_ids=(binding.claim_id,),
            )


def _audit_claim_spans(
    collector: AuditCollector,
    paragraphs: dict[str, PaperParagraph],
    claims: dict[str, ClaimEvidenceRow],
    *,
    argument_claim_ids: frozenset[str],
) -> None:
    for binding in collector.draft.claim_bindings:
        paragraph = paragraphs.get(binding.paragraph_id)
        rows = tuple(claims[item] for item in binding.claim_ids if item in claims)
        if paragraph is None or binding.end > len(paragraph.text):
            collector.binding("scope-inconsistency", binding)
            continue
        selected = paragraph.text[binding.start : min(binding.end, len(paragraph.text))]
        expected_section = {
            "finding": "results",
            "limitation": "limitations",
            "validation-scope": "validation-scope",
        }[binding.purpose]
        if (
            len(rows) != len(binding.claim_ids)
            or any(item not in argument_claim_ids for item in binding.claim_ids)
            or paragraph.section != expected_section
        ):
            collector.text(
                "scope-inconsistency", paragraph, claim_ids=binding.claim_ids
            )
            if len(rows) != len(binding.claim_ids):
                continue
        if binding.purpose == "validation-scope":
            collector.text(
                "scope-inconsistency",
                paragraph,
                start=binding.start,
                end=binding.end,
                claim_ids=binding.claim_ids,
            )
        if any(binding.allowed_strength != row.allowed_strength for row in rows):
            collector.text(
                "claim-strength-excess",
                paragraph,
                start=binding.start,
                end=binding.end,
                claim_ids=binding.claim_ids,
            )
        if any(
            (
                binding.unit,
                binding.population_basis,
                binding.time_basis,
                binding.price_base,
            )
            != (row.unit, row.population_basis, row.time_basis, row.price_base)
            for row in rows
        ):
            collector.text(
                "basis-overreach",
                paragraph,
                start=binding.start,
                end=binding.end,
                claim_ids=binding.claim_ids,
            )
        if binding.purpose == "finding":
            expected = " ".join(claim_sentence(row) for row in rows)
            if selected != expected:
                kind: FindingKind = (
                    "numeric-contradiction"
                    if NUMBER.findall(selected) != NUMBER.findall(expected)
                    else "scope-inconsistency"
                )
                collector.text(
                    kind,
                    paragraph,
                    start=binding.start,
                    end=binding.end,
                    claim_ids=binding.claim_ids,
                )
        elif binding.purpose == "limitation":
            allowed = {limit for row in rows for limit in row.limitations}
            if selected not in allowed:
                collector.text(
                    "scope-inconsistency",
                    paragraph,
                    start=binding.start,
                    end=binding.end,
                    claim_ids=binding.claim_ids,
                )


def _audit_outputs(
    collector: AuditCollector,
    claims: dict[str, ClaimEvidenceRow],
    bindings: tuple[TableBinding | FigureBinding, ...],
    *,
    argument_claim_ids: frozenset[str],
) -> None:
    for binding in bindings:
        rows = tuple(claims[item] for item in binding.claim_ids if item in claims)
        expected_path = f"outputs/{binding.output.name}"
        if any(item not in argument_claim_ids for item in binding.claim_ids):
            collector.output("scope-inconsistency", binding)
            continue
        extension_ok = (
            binding.output.name.endswith(".svg")
            if isinstance(binding, FigureBinding)
            else not binding.output.name.endswith(".svg")
        )
        if (
            len(rows) != len(binding.claim_ids)
            or any(binding.output not in row.output_evidence for row in rows)
            or binding.artifact_path != expected_path
            or not extension_ok
            or binding.caption != output_caption(binding.kind, rows)
        ):
            collector.output("output-evidence-mismatch", binding)


def _audit_policy_and_strength(
    collector: AuditCollector,
    paragraphs: tuple[PaperParagraph, ...],
    claims: dict[str, ClaimEvidenceRow],
) -> None:
    for paragraph in paragraphs:
        section_is_safe = (
            paragraph.section == "title" and paragraph.text in SAFE_TITLES
        ) or (
            paragraph.section == "research-question"
            and paragraph.text == SAFE_RESEARCH_QUESTION
        )
        section_is_closed = paragraph.section in {"title", "research-question"}
        if section_is_closed and not section_is_safe:
            numeric = tuple(NUMBER.finditer(paragraph.text))
            causal = tuple(CAUSAL_LANGUAGE.finditer(paragraph.text))
            policy = tuple(POLICY_OVERREACH.finditer(paragraph.text))
            if numeric:
                for match in numeric:
                    collector.text(
                        "numeric-contradiction",
                        paragraph,
                        start=match.start(),
                        end=match.end(),
                    )
                continue
            if causal:
                for match in causal:
                    collector.text(
                        "claim-strength-excess",
                        paragraph,
                        start=match.start(),
                        end=match.end(),
                    )
                continue
            if policy:
                start, end = policy[0].span()
            else:
                start, end = 0, len(paragraph.text)
            collector.text(
                "policy-overclaim",
                paragraph,
                start=start,
                end=end,
            )
            continue
        paragraph_claims = tuple(
            binding
            for binding in collector.draft.claim_bindings
            if binding.paragraph_id == paragraph.paragraph_id
        )
        for match in NUMBER.finditer(paragraph.text):
            if not any(
                binding.start <= match.start() and match.end() <= binding.end
                for binding in paragraph_claims
            ):
                collector.text(
                    "numeric-contradiction",
                    paragraph,
                    start=match.start(),
                    end=match.end(),
                )
        for match in POLICY_OVERREACH.finditer(paragraph.text):
            collector.text(
                "policy-overclaim", paragraph, start=match.start(), end=match.end()
            )
        for match in CAUSAL_LANGUAGE.finditer(paragraph.text):
            collector.text(
                "claim-strength-excess",
                paragraph,
                start=match.start(),
                end=match.end(),
            )


__all__ = ["audit_transitive_refs", "reconstruct_audit_findings"]
