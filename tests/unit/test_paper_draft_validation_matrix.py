"""Numeric, basis, and output attack matrix for exact paper draft validation."""

from __future__ import annotations

import pytest
from paper_draft_fixtures import candidate

from envresearch.paper.contracts import (
    ClaimEvidenceRow,
    DescriptiveRangeValue,
    DescriptiveSeriesPoint,
    DescriptiveSeriesValue,
)
from envresearch.paper.draft_contracts import PaperDraftCandidate
from envresearch.paper.draft_validation import (
    render_claim_sentence,
    render_output_caption,
    validate_draft,
)
from envresearch.paper.errors import PaperScopeExceeded, PaperSupportInvalid


def _claim(kind: str) -> ClaimEvidenceRow:
    _, ledger, _, _ = candidate()
    base = ledger.claims[0]
    common = {
        "transition_ref": base.transition_ref,
        "analysis_ref": base.analysis_ref,
        "snapshot_ref": base.snapshot_ref,
        "output_evidence": base.output_evidence,
        "reconstruction_status": "independently-reconstructed",
    }
    if kind == "estimate":
        return base
    if kind == "range":
        return ClaimEvidenceRow(
            claim_id="contingent-valuation-probability-range",
            claim_type="descriptive-quantity",
            method_id="contingent-valuation",
            quantity="probability-range",
            value=DescriptiveRangeValue(
                kind="descriptive-range",
                minimum=-0.25,
                maximum=0.8,
                uncertainty_status="not-estimated",
            ),
            welfare_transformation=None,
            unit="probability",
            population_basis="survey respondent",
            time_basis="survey-response",
            price_base="synthetic-2025-USD",
            allowed_strength="descriptive",
            limitations=("The range is descriptive and not a population estimate.",),
            **common,
        )
    return ClaimEvidenceRow(
        claim_id="contingent-valuation-bid-yes-shares",
        claim_type="descriptive-quantity",
        method_id="contingent-valuation",
        quantity="bid-yes-shares",
        value=DescriptiveSeriesValue(
            kind="counted-series",
            x_name="bid",
            x_unit="USD",
            y_name="yes-share",
            y_unit="proportion",
            points=(
                DescriptiveSeriesPoint(x=5.0, numerator=2, denominator=4, value=0.5),
            ),
            uncertainty_status="not-estimated",
        ),
        welfare_transformation=None,
        unit="proportion",
        population_basis="survey respondent",
        time_basis="survey-response",
        price_base="synthetic-2025-USD",
        allowed_strength="descriptive",
        limitations=("The counted series is descriptive and sample-specific.",),
        **common,
    )


def _draft_for(kind: str) -> tuple[PaperDraftCandidate, object, object, object]:
    draft, ledger, argument_map, snapshot = candidate()
    claim = _claim(kind)
    result = render_claim_sentence(claim)
    limitation = claim.limitations[0]
    paragraphs = (
        *draft.paragraphs[:3],
        draft.paragraphs[3].model_copy(update={"text": result}),
        draft.paragraphs[4].model_copy(update={"text": limitation}),
    )
    finding = draft.claim_bindings[0].model_copy(
        update={
            "end": len(result),
            "claim_ids": (claim.claim_id,),
            "allowed_strength": claim.allowed_strength,
            "unit": claim.unit,
            "population_basis": claim.population_basis,
            "time_basis": claim.time_basis,
            "price_base": claim.price_base,
        }
    )
    limitation_binding = finding.model_copy(
        update={
            "paragraph_id": "limitations-primary",
            "end": len(limitation),
            "purpose": "limitation",
        }
    )
    value = draft.model_copy(
        update={
            "paragraphs": paragraphs,
            "claim_bindings": (finding, limitation_binding),
            "tables": (
                draft.tables[0].model_copy(
                    update={
                        "claim_ids": (claim.claim_id,),
                        "caption": render_output_caption("table", (claim,)),
                        "output": claim.output_evidence[0],
                    }
                ),
            ),
            "figures": (
                draft.figures[0].model_copy(
                    update={
                        "claim_ids": (claim.claim_id,),
                        "caption": render_output_caption("figure", (claim,)),
                        "output": claim.output_evidence[1],
                    }
                ),
            ),
        }
    )
    ledger = ledger.model_copy(update={"claims": (claim,)})
    node = argument_map.nodes[0].model_copy(update={"claim_ids": (claim.claim_id,)})
    argument_map = argument_map.model_copy(update={"nodes": (node,)})
    return value, ledger, argument_map, snapshot


@pytest.mark.parametrize("kind", ("estimate", "range", "series"))
def test_all_claim_value_kinds_accept_only_canonical_renderer(kind: str) -> None:
    draft, ledger, argument_map, snapshot = _draft_for(kind)

    validate_draft(
        draft,
        argument_map=argument_map,
        ledger=ledger,
        citation_snapshot=snapshot,
    )


@pytest.mark.parametrize(
    ("kind", "old", "new"),
    (
        ("estimate", "1.25 USD", "-1.25 USD"),
        ("estimate", "from 1.05", "from 1.06"),
        ("estimate", "to 1.45", "to 1.46"),
        ("estimate", "95%", "90%"),
        ("range", "from -0.25", "from +0.25"),
        ("range", "to 0.8", "to 0.9"),
        ("series", "5 USD", "-5 USD"),
        ("series", "2 of 4", "3 of 4"),
        ("series", "2 of 4", "2 of 5"),
        ("series", "0.5 proportion", "0.75 proportion"),
    ),
)
def test_number_sign_interval_range_and_count_attacks_fail(
    kind: str, old: str, new: str
) -> None:
    draft, ledger, argument_map, snapshot = _draft_for(kind)
    result = draft.paragraphs[3]
    assert old in result.text
    forged = draft.model_copy(
        update={
            "paragraphs": (
                *draft.paragraphs[:3],
                result.model_copy(update={"text": result.text.replace(old, new)}),
                draft.paragraphs[4],
            )
        }
    )

    with pytest.raises(PaperScopeExceeded, match="number|basis"):
        validate_draft(
            forged,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=snapshot,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("unit", "EUR"),
        ("population_basis", "national population"),
        ("time_basis", "monthly"),
        ("price_base", "nominal-current"),
        ("allowed_strength", "design-based-causal"),
    ),
)
def test_scope_basis_and_strength_attacks_fail(field: str, value: str) -> None:
    draft, ledger, argument_map, snapshot = _draft_for("estimate")
    binding = draft.claim_bindings[0].model_copy(update={field: value})
    forged = draft.model_copy(
        update={"claim_bindings": (binding, draft.claim_bindings[1])}
    )

    with pytest.raises(PaperScopeExceeded, match="basis|strength"):
        validate_draft(
            forged,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=snapshot,
        )


@pytest.mark.parametrize("fault", ("digest", "path", "result-pointer"))
def test_output_digest_path_and_result_pointer_attacks_fail(fault: str) -> None:
    draft, ledger, argument_map, snapshot = _draft_for("estimate")
    table = draft.tables[0]
    if fault == "path":
        table = table.model_copy(update={"artifact_path": "outputs/other.csv"})
    else:
        output = table.output.model_copy(
            update={
                "sha256": "f" * 64 if fault == "digest" else table.output.sha256,
                "result_pointers": ("/other",)
                if fault == "result-pointer"
                else table.output.result_pointers,
            }
        )
        table = table.model_copy(update={"output": output})
    forged = draft.model_copy(update={"tables": (table,)})

    with pytest.raises(PaperSupportInvalid, match="output"):
        validate_draft(
            forged,
            argument_map=argument_map,
            ledger=ledger,
            citation_snapshot=snapshot,
        )
