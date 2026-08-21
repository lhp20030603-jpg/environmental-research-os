"""Contract and validator tests for evidence-bound Paper Builder drafts."""

from __future__ import annotations

import pytest
from paper_draft_fixtures import candidate, materialized_draft
from pydantic import ValidationError

from envresearch.benchmarks.claim_report import CitationIntegrityReport
from envresearch.models.benchmark_claims import ClaimVerificationStatus
from envresearch.paper.citation_authority import CitationAuthoritySnapshot
from envresearch.paper.draft_contracts import ClaimSpanBinding, PaperParagraph
from envresearch.paper.draft_validation import validate_draft
from envresearch.paper.errors import PaperScopeExceeded, PaperSupportInvalid


def test_paragraph_contract_is_strict_frozen_and_plain_text() -> None:
    paragraph = PaperParagraph(
        paragraph_id="results-primary",
        position=3,
        section="results",
        text="The registered estimate is 1.25 USD.",
    )

    with pytest.raises(ValidationError):
        PaperParagraph.model_validate(
            {**paragraph.model_dump(), "untrusted_summary": "passed"}
        )
    with pytest.raises(ValidationError):
        paragraph.text = "Changed."  # type: ignore[misc]
    with pytest.raises(ValidationError, match="plain text"):
        PaperParagraph(
            paragraph_id="results-primary",
            position=3,
            section="results",
            text="Line one.\nLine two.",
        )


def test_claim_span_contract_rejects_empty_or_reversed_offsets() -> None:
    base = {
        "paragraph_id": "results-primary",
        "purpose": "finding",
        "allowed_strength": "model-conditional-valuation",
        "unit": "USD",
        "population_basis": "respondent",
        "time_basis": "annual",
        "price_base": "synthetic-2025-USD",
    }
    with pytest.raises(ValidationError, match="end"):
        ClaimSpanBinding(
            **base,
            start=10,
            end=10,
            claim_ids=("contingent-valuation-median-wtp",),
        )
    with pytest.raises(ValidationError, match="claim"):
        ClaimSpanBinding(**base, start=0, end=10, claim_ids=())


def test_validator_accepts_exact_spans_numbers_citation_and_outputs() -> None:
    draft, ledger, argument_map, snapshot = candidate()

    validate_draft(
        draft,
        argument_map=argument_map,
        ledger=ledger,
        citation_snapshot=snapshot,
    )


def test_validator_rejects_resealed_numeric_and_policy_overreach() -> None:
    draft, ledger, argument_map, snapshot = candidate()
    paragraph = draft.paragraphs[3]
    changed = paragraph.model_copy(
        update={"text": paragraph.text.replace("1.25", "9.25")}
    )
    forged = draft.model_copy(
        update={"paragraphs": (*draft.paragraphs[:3], changed, draft.paragraphs[4])}
    )

    with pytest.raises(PaperScopeExceeded, match="number|template"):
        validate_draft(
            forged,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=snapshot,
        )

    title = draft.paragraphs[0].model_copy(
        update={"text": "The evidence proves policymakers must adopt the program."}
    )
    overclaim = draft.model_copy(update={"paragraphs": (title, *draft.paragraphs[1:])})
    with pytest.raises(PaperScopeExceeded, match="policy|strength"):
        validate_draft(
            overclaim,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=snapshot,
        )


def test_validator_rejects_missing_overlap_and_invented_citation() -> None:
    draft, ledger, argument_map, snapshot = candidate()
    missing = draft.model_copy(update={"claim_bindings": draft.claim_bindings[1:]})
    with pytest.raises(PaperSupportInvalid, match="result|span"):
        validate_draft(
            missing,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=snapshot,
        )

    overlap = draft.model_copy(
        update={"claim_bindings": (draft.claim_bindings[0],) * 2}
    )
    with pytest.raises(PaperSupportInvalid, match="overlap|duplicate"):
        validate_draft(
            overlap,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=snapshot,
        )

    invented = draft.citation_bindings[0].model_copy(
        update={"claim_id": "invented-claim"}
    )
    with pytest.raises(PaperSupportInvalid, match="citation"):
        validate_draft(
            draft.model_copy(update={"citation_bindings": (invented,)}),
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=snapshot,
        )


def test_validator_rejects_nonverified_citation_and_output_forgery() -> None:
    draft, ledger, argument_map, snapshot = candidate()
    source_ref, source = snapshot.source_sheets[0]
    pending = source.claims[0].model_copy(
        update={"status": ClaimVerificationStatus.UNVERIFIED}
    )
    nonverified = CitationAuthoritySnapshot(
        report=snapshot.report,
        source_sheets=((source_ref, source.model_copy(update={"claims": (pending,)})),),
        token=snapshot.token,
    )
    with pytest.raises(PaperSupportInvalid, match="verified"):
        validate_draft(
            draft,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=nonverified,
        )

    table = draft.tables[0]
    forged_output = table.output.model_copy(update={"sha256": "f" * 64})
    forged = draft.model_copy(
        update={"tables": (table.model_copy(update={"output": forged_output}),)}
    )
    with pytest.raises(PaperSupportInvalid, match="output"):
        validate_draft(
            forged,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=snapshot,
        )


@pytest.mark.parametrize("field", ("map_ref", "ledger_ref", "citation_report_ref"))
@pytest.mark.parametrize("part", ("artifact_id", "artifact_version", "content_hash"))
def test_materialized_draft_requires_all_exact_authority_ref_parts(
    field: str, part: str
) -> None:
    draft, ledger, argument_map, snapshot, map_ref, ledger_ref, report_ref = (
        materialized_draft()
    )
    supplied = {
        "map_ref": map_ref,
        "ledger_ref": ledger_ref,
        "citation_report_ref": report_ref,
    }
    original = supplied[field]
    value: object = (
        f"{original.artifact_id}-other"
        if part == "artifact_id"
        else original.artifact_version + 1
        if part == "artifact_version"
        else "f" * 64
    )
    supplied[field] = original.model_copy(update={part: value})

    with pytest.raises(PaperSupportInvalid, match="reference|authority"):
        validate_draft(
            draft,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=snapshot,
            **supplied,
        )


def test_materialized_draft_accepts_the_three_exact_authority_refs() -> None:
    draft, ledger, argument_map, snapshot, map_ref, ledger_ref, report_ref = (
        materialized_draft()
    )

    validate_draft(
        draft,
        argument_map=argument_map,
        ledger=ledger,
        citation_snapshot=snapshot,
        map_ref=map_ref,
        ledger_ref=ledger_ref,
        citation_report_ref=report_ref,
    )


def test_citation_report_requires_valid_binding_and_exact_source_refs() -> None:
    draft, ledger, argument_map, snapshot = candidate()
    report_ref, report = snapshot.report
    invalid = CitationAuthoritySnapshot(
        report=(
            report_ref,
            CitationIntegrityReport(
                findings=report.findings,
                passed=report.passed,
                validator_version=report.validator_version,
                source_sheet_refs=report.source_sheet_refs,
                claim_fact_map_refs=report.claim_fact_map_refs,
                blinded_brief_refs=report.blinded_brief_refs,
                accepted_artifact_refs=report.accepted_artifact_refs,
                accepted_artifact_bindings=report.accepted_artifact_bindings,
                binding_sha256="0" * 64,
            ),
        ),
        source_sheets=snapshot.source_sheets,
        token=snapshot.token,
    )
    with pytest.raises(PaperSupportInvalid, match="citation report"):
        validate_draft(
            draft,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=invalid,
        )

    wrong_source = snapshot.source_sheets[0][0].model_copy(
        update={"artifact_version": 2}
    )
    mismatched = CitationAuthoritySnapshot(
        report=snapshot.report,
        source_sheets=((wrong_source, snapshot.source_sheets[0][1]),),
        token=snapshot.token,
    )
    with pytest.raises(PaperSupportInvalid, match="source"):
        validate_draft(
            draft,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=mismatched,
        )


@pytest.mark.parametrize(
    "text",
    (
        "Validation covered 3 exception cases.",
        "The registered model led to adoption.",
        "The registered model increased welfare.",
        "The registered model reduced harm.",
        "The registered model results in benefits.",
    ),
)
def test_unbound_validation_or_causal_prose_is_rejected(text: str) -> None:
    draft, ledger, argument_map, snapshot = candidate()
    paragraph = PaperParagraph(
        paragraph_id="validation-boundary",
        position=5,
        section="validation-scope",
        text=text,
    )
    forged = draft.model_copy(update={"paragraphs": (*draft.paragraphs, paragraph)})
    with pytest.raises((PaperSupportInvalid, PaperScopeExceeded)):
        validate_draft(
            forged,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=snapshot,
        )


def test_validation_scope_number_fails_even_when_fully_bound_to_real_claim() -> None:
    draft, ledger, argument_map, snapshot = candidate()
    text = "Validation covered 3 exception cases."
    paragraph = PaperParagraph(
        paragraph_id="validation-boundary",
        position=5,
        section="validation-scope",
        text=text,
    )
    binding = draft.claim_bindings[0].model_copy(
        update={
            "paragraph_id": paragraph.paragraph_id,
            "start": 0,
            "end": len(text),
            "purpose": "validation-scope",
        }
    )
    forged = draft.model_copy(
        update={
            "paragraphs": (*draft.paragraphs, paragraph),
            "claim_bindings": (*draft.claim_bindings, binding),
        }
    )

    with pytest.raises(PaperSupportInvalid) as raised:
        validate_draft(
            forged,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=snapshot,
        )

    assert raised.value.finding_kind == "validation-scope-unsupported"


def test_methods_text_must_be_completely_covered_by_verified_citations() -> None:
    draft, ledger, argument_map, snapshot = candidate()
    methods = draft.paragraphs[2]
    changed = methods.model_copy(
        update={"text": f"{methods.text} Invented method fact."}
    )
    forged = draft.model_copy(
        update={"paragraphs": (*draft.paragraphs[:2], changed, *draft.paragraphs[3:])}
    )

    with pytest.raises(PaperSupportInvalid, match="method|citation"):
        validate_draft(
            forged,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=snapshot,
        )


def test_output_binding_must_exist_in_every_bound_claim_and_ids_are_global() -> None:
    draft, ledger, argument_map, snapshot = candidate()
    claim = ledger.claims[0]
    second = claim.model_copy(
        update={
            "claim_id": "contingent-valuation-other-wtp",
            "quantity": "other-wtp",
            "output_evidence": (claim.output_evidence[1],),
        }
    )
    ledger = ledger.model_copy(update={"claims": (*ledger.claims, second)})
    node = argument_map.nodes[0].model_copy(
        update={"claim_ids": (*argument_map.nodes[0].claim_ids, second.claim_id)}
    )
    argument_map = argument_map.model_copy(update={"nodes": (node,)})
    table = draft.tables[0].model_copy(
        update={"claim_ids": (claim.claim_id, second.claim_id)}
    )
    forged = draft.model_copy(update={"tables": (table,)})
    with pytest.raises(PaperSupportInvalid, match="every|output"):
        validate_draft(
            forged,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=snapshot,
        )

    duplicate_id = draft.figures[0].model_copy(
        update={"binding_id": draft.tables[0].binding_id}
    )
    with pytest.raises(PaperSupportInvalid, match="binding"):
        validate_draft(
            draft.model_copy(update={"figures": (duplicate_id,)}),
            argument_map=argument_map.model_copy(
                update={
                    "nodes": (
                        argument_map.nodes[0].model_copy(
                            update={"claim_ids": (claim.claim_id,)}
                        ),
                    )
                }
            ),
            ledger=ledger.model_copy(update={"claims": (claim,)}),
            citation_snapshot=snapshot,
        )
