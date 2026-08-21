"""Review regressions for paper prose, captions, and citation report identity."""

from __future__ import annotations

import pytest
from paper_draft_fixtures import artifact_ref, candidate

from envresearch.models.benchmark_evaluation import (
    CitationIntegrityReport as EvaluationCitationIntegrityReport,
)
from envresearch.paper.citation_authority import CitationAuthoritySnapshot
from envresearch.paper.draft_validation import validate_draft
from envresearch.paper.errors import PaperScopeExceeded, PaperSupportInvalid


@pytest.mark.parametrize(
    "caption",
    (
        "The registered value is 999 USD.",
        "The registered value is -20 USD.",
        "This output guarantees policymakers should adopt the program.",
    ),
)
@pytest.mark.parametrize("binding_group", ("tables", "figures"))
def test_output_caption_must_be_deterministic_and_claim_bound(
    caption: str, binding_group: str
) -> None:
    draft, ledger, argument_map, snapshot = candidate()
    bindings = getattr(draft, binding_group)
    forged = draft.model_copy(
        update={binding_group: (bindings[0].model_copy(update={"caption": caption}),)}
    )

    with pytest.raises((PaperSupportInvalid, PaperScopeExceeded), match="caption"):
        validate_draft(
            forged,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=snapshot,
        )


def test_evaluation_only_citation_report_is_typed_support_failure() -> None:
    draft, ledger, argument_map, snapshot = candidate()
    evaluation_report = EvaluationCitationIntegrityReport(
        accepted_artifact_claims_ref=artifact_ref("accepted-claims", "e"),
        findings=(),
        verdict="pass",
        validator_principal="evaluation-validator",
    )
    forged = CitationAuthoritySnapshot(
        report=(snapshot.report[0], evaluation_report),  # type: ignore[arg-type]
        source_sheets=snapshot.source_sheets,
        token=snapshot.token,
    )

    with pytest.raises(PaperSupportInvalid, match="citation report") as raised:
        validate_draft(
            draft,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=forged,
        )

    assert raised.value.finding_kind == "citation-report-invalid"


def test_limitations_paragraph_must_be_fully_covered_by_exact_spans() -> None:
    draft, ledger, argument_map, snapshot = candidate()
    limitation = draft.paragraphs[4]
    forged = draft.model_copy(
        update={
            "paragraphs": (
                *draft.paragraphs[:4],
                limitation.model_copy(
                    update={"text": f"{limitation.text} Unsupported caveat."}
                ),
            )
        }
    )

    with pytest.raises(PaperSupportInvalid, match="limitation|span"):
        validate_draft(
            forged,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=snapshot,
        )


def test_validation_scope_paragraph_fails_without_typed_failed_case_authority() -> None:
    draft, ledger, argument_map, snapshot = candidate()
    validation = draft.paragraphs[0].model_copy(
        update={
            "paragraph_id": "validation-boundary",
            "position": 5,
            "section": "validation-scope",
            "text": "Validation remained within the registered local scope.",
        }
    )
    forged = draft.model_copy(update={"paragraphs": (*draft.paragraphs, validation)})

    with pytest.raises(PaperSupportInvalid, match="validation") as raised:
        validate_draft(
            forged,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=snapshot,
        )

    assert raised.value.finding_kind == "validation-scope-unsupported"
