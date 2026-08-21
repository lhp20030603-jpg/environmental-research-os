"""Strict contracts for the V0.4 claim-evidence ledger."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from envresearch.econometrics.report import LocalAnalysisReference, OutputEvidence
from envresearch.models.artifact import ArtifactRef
from envresearch.paper.contracts import (
    AnalysisOutputRef,
    ClaimEvidenceLedger,
    ClaimEvidenceRow,
    ClaimUncertainty,
    DescriptiveRangeValue,
    DescriptiveSeriesPoint,
    DescriptiveSeriesValue,
    EstimatedClaimValue,
)


def _artifact_ref(identity: str, digest: str) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=identity,
        artifact_version=1,
        content_hash=digest * 64,
    )


def _analysis_ref(digest: str = "c") -> LocalAnalysisReference:
    sha256 = digest * 64
    return LocalAnalysisReference(
        analysis_id="local-contingent-valuation-example",
        generation=1,
        relative_path=Path(
            "analyses/local-contingent-valuation-example/history/"
            f"generation-1-{sha256}.json"
        ),
        sha256=sha256,
    )


def _output(name: str, digest: str) -> AnalysisOutputRef:
    evidence = OutputEvidence(
        name=name,
        relative_path=Path(
            "analyses/local-contingent-valuation-example/evidence/outputs"
        )
        / name,
        sha256=digest * 64,
        size_bytes=128,
    )
    return AnalysisOutputRef(
        analysis_ref=_analysis_ref(),
        name=evidence.name,
        sha256=evidence.sha256,
        size_bytes=evidence.size_bytes,
        result_pointers=("/welfare/0",),
    )


def test_analysis_output_ref_binds_output_to_its_authenticated_report() -> None:
    analysis_ref = _analysis_ref()
    reference = AnalysisOutputRef(
        analysis_ref=analysis_ref,
        name="wtp.csv",
        sha256="e" * 64,
        size_bytes=128,
        result_pointers=("/welfare/0", "/figure_sha256"),
    )

    assert reference.analysis_ref == analysis_ref
    with pytest.raises(ValidationError, match="canonical JSON pointer"):
        AnalysisOutputRef(
            analysis_ref=analysis_ref,
            name="wtp.csv",
            sha256="e" * 64,
            size_bytes=128,
            result_pointers=("welfare/0",),
        )


def test_claim_values_distinguish_estimates_ranges_and_counted_series() -> None:
    estimate = EstimatedClaimValue(
        kind="estimate",
        estimate=20.0,
        uncertainty=ClaimUncertainty(
            std_error=2.0,
            confidence_low=16.08,
            confidence_high=23.92,
            confidence_level=0.95,
        ),
    )
    probability_range = DescriptiveRangeValue(
        kind="descriptive-range",
        minimum=0.1,
        maximum=0.8,
        uncertainty_status="not-estimated",
    )
    shares = DescriptiveSeriesValue(
        kind="counted-series",
        x_name="bid",
        x_unit="cny",
        y_name="yes-share",
        y_unit="proportion",
        points=(
            DescriptiveSeriesPoint(x=10.0, numerator=7, denominator=10, value=0.7),
            DescriptiveSeriesPoint(x=20.0, numerator=6, denominator=10, value=0.6),
        ),
        uncertainty_status="not-estimated",
    )

    assert estimate.estimate == 20.0
    assert probability_range.maximum == 0.8
    assert shares.points[0].value == 0.7
    assert (shares.x_unit, shares.y_unit) == ("cny", "proportion")
    with pytest.raises(ValidationError, match="counted-series value"):
        DescriptiveSeriesPoint(x=10.0, numerator=7, denominator=10, value=0.8)


def test_claim_target_is_a_generic_quantity_not_an_estimand_only_field() -> None:
    payload = _row().model_dump(mode="python")
    assert payload["quantity"] == "median-wtp"
    assert ClaimEvidenceRow.model_validate(payload).quantity == "median-wtp"


def _row(*, transition: ArtifactRef | None = None) -> ClaimEvidenceRow:
    return ClaimEvidenceRow(
        claim_id="contingent-valuation-median-wtp",
        claim_type="welfare-estimate",
        method_id="contingent-valuation",
        quantity="median-wtp",
        value=EstimatedClaimValue(
            kind="estimate",
            estimate=20.0,
            uncertainty=ClaimUncertainty(
                std_error=2.0,
                confidence_low=16.08,
                confidence_high=23.92,
                confidence_level=0.95,
            ),
        ),
        transition_ref=transition or _artifact_ref("valuation-transition-v031", "a"),
        analysis_ref=_analysis_ref(),
        snapshot_ref=_artifact_ref("local-data-example", "d"),
        output_evidence=(
            _output("wtp.csv", "e"),
            _output("estimate.svg", "f"),
        ),
        reconstruction_status="independently-reconstructed",
        welfare_transformation="negative-intercept-over-bid",
        unit="cny",
        population_basis="sample",
        time_basis="annual",
        price_base="p2025",
        allowed_strength="model-conditional-valuation",
        limitations=(
            "Stated-preference estimate conditional on the registered response model.",
        ),
    )


def test_claim_evidence_models_are_frozen_strict_and_exact() -> None:
    row = _row()
    ledger = ClaimEvidenceLedger(
        schema_version="paper.claim-evidence-ledger.v1",
        ledger_id="valuation-core-claims",
        producer="paper-builder-ledger-v1",
        transition_ref=row.transition_ref,
        claims=(row,),
    )

    assert ledger.claims == (row,)
    assert ledger.claims[0].output_evidence[0].name == "wtp.csv"
    with pytest.raises(ValidationError, match="frozen"):
        row.value = row.value  # type: ignore[misc]
    with pytest.raises(ValidationError, match="Extra inputs"):
        ClaimEvidenceRow.model_validate(
            {**row.model_dump(mode="python"), "unreviewed": True}
        )


@pytest.mark.parametrize(
    ("update", "message"),
    (
        ({"claim_id": "CV claim"}, "claim id"),
        ({"claim_id": "other-median-wtp"}, "method and quantity"),
        ({"welfare_transformation": " negative-ratio "}, "transformation"),
        ({"allowed_strength": "design-based-causal"}, "valuation strength"),
        (
            {
                "value": {
                    "kind": "estimate",
                    "estimate": float("nan"),
                    "uncertainty": {
                        "std_error": 2.0,
                        "confidence_low": 16.08,
                        "confidence_high": 23.92,
                        "confidence_level": 0.95,
                    },
                }
            },
            "finite",
        ),
        (
            {
                "value": {
                    "kind": "estimate",
                    "estimate": 20.0,
                    "uncertainty": ClaimUncertainty(
                        std_error=2.0,
                        confidence_low=21.0,
                        confidence_high=23.0,
                        confidence_level=0.95,
                    ),
                }
            },
            "estimate must lie within",
        ),
        ({"output_evidence": ()}, "at least 1"),
        ({"limitations": ()}, "at least 1"),
    ),
)
def test_claim_evidence_rejects_incomplete_or_incoherent_rows(
    update: dict[str, object], message: str
) -> None:
    payload = _row().model_dump(mode="python")
    payload.update(update)

    with pytest.raises(ValidationError, match=message):
        ClaimEvidenceRow.model_validate(payload)


def test_ledger_rejects_duplicate_claims_and_cross_transition_rows() -> None:
    row = _row()
    base = {
        "schema_version": "paper.claim-evidence-ledger.v1",
        "ledger_id": "valuation-core-claims",
        "producer": "paper-builder-ledger-v1",
        "transition_ref": row.transition_ref,
    }

    with pytest.raises(ValidationError, match="claim ids must be unique"):
        ClaimEvidenceLedger(**base, claims=(row, row))

    other = _row(transition=_artifact_ref("valuation-transition-v031", "b"))
    with pytest.raises(ValidationError, match="same transition"):
        ClaimEvidenceLedger(**base, claims=(other,))
