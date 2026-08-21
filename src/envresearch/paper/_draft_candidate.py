"""Deterministic useful paper slice from exact claim and citation authorities."""

from __future__ import annotations

from envresearch.models.benchmark_claims import ClaimVerificationStatus
from envresearch.paper.argument_contracts import ArgumentMap
from envresearch.paper.citation_authority import CitationAuthoritySnapshot
from envresearch.paper.contracts import ClaimEvidenceLedger, EstimatedClaimValue
from envresearch.paper.draft_contracts import (
    CitationBinding,
    ClaimSpanBinding,
    FigureBinding,
    PaperDraftCandidate,
    PaperParagraph,
    TableBinding,
)
from envresearch.paper.draft_validation import (
    render_claim_sentence,
    render_output_caption,
)
from envresearch.paper.errors import PaperSupportInvalid


def deterministic_draft_candidate(
    *,
    argument_map: ArgumentMap,
    ledger: ClaimEvidenceLedger,
    citation_snapshot: CitationAuthoritySnapshot,
) -> PaperDraftCandidate:
    """Build one compact valuation paper slice without an LLM or mutable paths."""
    accepted_ids = frozenset(
        claim_id
        for node in argument_map.nodes
        if node.node_type == "empirical-claim"
        for claim_id in node.claim_ids
    )
    primary = next(
        (
            claim
            for claim in ledger.claims
            if claim.claim_id in accepted_ids
            and isinstance(claim.value, EstimatedClaimValue)
        ),
        None,
    )
    citation = next(
        (
            (source_ref, claim)
            for source_ref, source in citation_snapshot.source_sheets
            for claim in source.claims
            if claim.status is ClaimVerificationStatus.CLAIM_VERIFIED
        ),
        None,
    )
    if primary is None or citation is None:
        raise PaperSupportInvalid(
            "deterministic draft requires one estimated claim and verified citation",
            finding_kind="draft-support-missing",
        )
    table_output = next(
        (item for item in primary.output_evidence if item.name == "wtp.csv"),
        next(
            (
                item
                for item in primary.output_evidence
                if not item.name.endswith(".svg")
            ),
            None,
        ),
    )
    figure_output = next(
        (item for item in primary.output_evidence if item.name.endswith(".svg")), None
    )
    if table_output is None or figure_output is None:
        raise PaperSupportInvalid(
            "deterministic draft requires exact table and figure outputs",
            finding_kind="draft-output-missing",
        )
    source_ref, verified_claim = citation
    finding = render_claim_sentence(primary)
    limitation = primary.limitations[0]
    methods = verified_claim.normalized_claim
    paragraphs = (
        PaperParagraph(
            paragraph_id="paper-title",
            position=0,
            section="title",
            text="Registered environmental valuation evidence",
        ),
        PaperParagraph(
            paragraph_id="research-question",
            position=1,
            section="research-question",
            text="What value is supported within the registered model boundary?",
        ),
        PaperParagraph(
            paragraph_id="methods-source",
            position=2,
            section="methods",
            text=methods,
        ),
        PaperParagraph(
            paragraph_id="results-primary",
            position=3,
            section="results",
            text=finding,
        ),
        PaperParagraph(
            paragraph_id="limitations-primary",
            position=4,
            section="limitations",
            text=limitation,
        ),
    )
    finding_binding = ClaimSpanBinding(
        paragraph_id="results-primary",
        start=0,
        end=len(finding),
        claim_ids=(primary.claim_id,),
        purpose="finding",
        allowed_strength=primary.allowed_strength,
        unit=primary.unit,
        population_basis=primary.population_basis,
        time_basis=primary.time_basis,
        price_base=primary.price_base,
    )
    return PaperDraftCandidate(
        paragraphs=paragraphs,
        claim_bindings=(
            finding_binding,
            finding_binding.model_copy(
                update={
                    "paragraph_id": "limitations-primary",
                    "end": len(limitation),
                    "purpose": "limitation",
                }
            ),
        ),
        citation_bindings=(
            CitationBinding(
                paragraph_id="methods-source",
                start=0,
                end=len(methods),
                source_sheet_ref=source_ref,
                claim_id=verified_claim.claim_id,
            ),
        ),
        tables=(
            TableBinding(
                kind="table",
                binding_id="results-table",
                claim_ids=(primary.claim_id,),
                artifact_path=f"outputs/{table_output.name}",
                caption=render_output_caption("table", (primary,)),
                output=table_output,
            ),
        ),
        figures=(
            FigureBinding(
                kind="figure",
                binding_id="valuation-figure",
                claim_ids=(primary.claim_id,),
                artifact_path=f"outputs/{figure_output.name}",
                caption=render_output_caption("figure", (primary,)),
                output=figure_output,
            ),
        ),
    )


__all__ = ["deterministic_draft_candidate"]
