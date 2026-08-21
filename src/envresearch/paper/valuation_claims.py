"""Derive exact writable claims from accepted valuation reports."""

from __future__ import annotations

import re
from collections.abc import Iterable

from envresearch.econometrics.report import LocalAnalysisReference, LocalAnalysisReport
from envresearch.econometrics.valuation_results import (
    ContingentValuationResult,
    DiscreteChoiceResult,
    HedonicResult,
    TravelCostResult,
)
from envresearch.models.artifact import ArtifactRef
from envresearch.paper.contracts import (
    AnalysisOutputRef,
    ClaimEvidenceRow,
    ClaimUncertainty,
    DescriptiveRangeValue,
    DescriptiveSeriesPoint,
    DescriptiveSeriesValue,
    EstimatedClaimValue,
)
from envresearch.paper.errors import PaperSupportInvalid

ValuationResult = (
    HedonicResult | TravelCostResult | ContingentValuationResult | DiscreteChoiceResult
)

_LIMITATIONS = {
    "hedonic-pricing": (
        (
            "Model-conditional marginal implicit price; residual confounding and "
            "housing-market sorting remain outside the registered design."
        ),
    ),
    "travel-cost": (
        (
            "Model-conditional consumer surplus depends on registered trip demand, "
            "site effects, and measured travel cost."
        ),
    ),
    "contingent-valuation": (
        (
            "Stated-preference valuation is conditional on the registered response "
            "model and hypothetical-market design."
        ),
    ),
    "dce-clogit": (
        (
            "Choice-model valuation is conditional on registered alternatives, "
            "attributes, and the cost coefficient."
        ),
    ),
}


def valuation_claims(
    reports: Iterable[tuple[LocalAnalysisReference, LocalAnalysisReport]],
    transition_ref: ArtifactRef,
) -> tuple[ClaimEvidenceRow, ...]:
    """Return deterministic claim rows from independently accepted reports."""
    rows: list[ClaimEvidenceRow] = []
    for analysis_ref, report in reports:
        result = _accepted_result(report)
        if report.snapshot is None:
            raise PaperSupportInvalid(
                "accepted report has no authenticated snapshot",
                finding_kind="snapshot-missing",
            )
        for index, welfare in enumerate(result.welfare):
            quantity = _identifier(welfare.name)
            rows.append(
                ClaimEvidenceRow(
                    claim_id=f"{result.method_id}-{quantity}",
                    claim_type="welfare-estimate",
                    method_id=result.method_id,
                    quantity=quantity,
                    value=EstimatedClaimValue(
                        kind="estimate",
                        estimate=welfare.estimate,
                        uncertainty=ClaimUncertainty(
                            std_error=welfare.std_error,
                            confidence_low=welfare.confidence_low,
                            confidence_high=welfare.confidence_high,
                            confidence_level=result.configuration.confidence_level,
                        ),
                    ),
                    transition_ref=transition_ref,
                    analysis_ref=analysis_ref,
                    snapshot_ref=report.snapshot.reference,
                    output_evidence=_outputs(
                        analysis_ref,
                        report,
                        welfare_pointer=f"/welfare/{index}",
                    ),
                    reconstruction_status="independently-reconstructed",
                    welfare_transformation=welfare.transformation,
                    unit=welfare.currency,
                    population_basis=welfare.population_basis,
                    time_basis=welfare.time_basis,
                    price_base=welfare.price_base,
                    allowed_strength="model-conditional-valuation",
                    limitations=_LIMITATIONS[result.method_id],
                )
            )
        if isinstance(result, ContingentValuationResult):
            rows.extend(
                _cv_descriptive_claims(
                    analysis_ref, report, result, transition_ref=transition_ref
                )
            )
    if not rows:
        raise PaperSupportInvalid(
            "accepted evidence contains no writable claim",
            finding_kind="claim-evidence-missing",
        )
    return tuple(rows)


def _accepted_result(report: LocalAnalysisReport) -> ValuationResult:
    if report.status != "passed" or report.snapshot is None or report.result is None:
        raise PaperSupportInvalid(
            "only passed reconstructed reports can become claim evidence",
            finding_kind="analysis-not-green",
        )
    if not isinstance(
        report.result,
        (
            HedonicResult,
            TravelCostResult,
            ContingentValuationResult,
            DiscreteChoiceResult,
        ),
    ):
        raise PaperSupportInvalid(
            "analysis result is not an accepted valuation result",
            finding_kind="method-not-supported",
        )
    return report.result


def _cv_descriptive_claims(
    analysis_ref: LocalAnalysisReference,
    report: LocalAnalysisReport,
    result: ContingentValuationResult,
    *,
    transition_ref: ArtifactRef,
) -> tuple[ClaimEvidenceRow, ClaimEvidenceRow]:
    assert report.snapshot is not None
    welfare = result.welfare[0]
    limitations = (
        (
            "Descriptive diagnostic of the authenticated contingent-valuation "
            "sample; it is not a causal or population estimate."
        ),
    )
    probability = ClaimEvidenceRow(
        claim_id="contingent-valuation-probability-range",
        claim_type="descriptive-quantity",
        method_id=result.method_id,
        quantity="probability-range",
        value=DescriptiveRangeValue(
            kind="descriptive-range",
            minimum=result.probability_min,
            maximum=result.probability_max,
            uncertainty_status="not-estimated",
        ),
        transition_ref=transition_ref,
        analysis_ref=analysis_ref,
        snapshot_ref=report.snapshot.reference,
        output_evidence=_named_outputs(
            analysis_ref,
            report,
            names=("probabilities.csv",),
            result_pointers=("/probability_min", "/probability_max"),
        ),
        reconstruction_status="independently-reconstructed",
        welfare_transformation=None,
        unit="probability",
        population_basis=welfare.population_basis,
        time_basis="survey-response",
        price_base=welfare.price_base,
        allowed_strength="descriptive",
        limitations=limitations,
    )
    shares = ClaimEvidenceRow(
        claim_id="contingent-valuation-bid-yes-shares",
        claim_type="descriptive-quantity",
        method_id=result.method_id,
        quantity="bid-yes-shares",
        value=DescriptiveSeriesValue(
            kind="counted-series",
            x_name="bid",
            x_unit=welfare.currency,
            y_name="yes-share",
            y_unit="proportion",
            points=tuple(
                DescriptiveSeriesPoint(
                    x=item.bid,
                    numerator=item.yes_count,
                    denominator=item.observations,
                    value=item.yes_share,
                )
                for item in result.bid_yes_shares
            ),
            uncertainty_status="not-estimated",
        ),
        transition_ref=transition_ref,
        analysis_ref=analysis_ref,
        snapshot_ref=report.snapshot.reference,
        output_evidence=_named_outputs(
            analysis_ref,
            report,
            names=("bid_yes_shares.csv",),
            result_pointers=("/bid_yes_shares",),
        ),
        reconstruction_status="independently-reconstructed",
        welfare_transformation=None,
        unit="proportion",
        population_basis=welfare.population_basis,
        time_basis="survey-response",
        price_base=welfare.price_base,
        allowed_strength="descriptive",
        limitations=limitations,
    )
    return probability, shares


def _outputs(
    analysis_ref: LocalAnalysisReference,
    report: LocalAnalysisReport,
    *,
    welfare_pointer: str,
) -> tuple[AnalysisOutputRef, ...]:
    return tuple(
        AnalysisOutputRef(
            analysis_ref=analysis_ref,
            name=item.name,
            sha256=item.sha256,
            size_bytes=item.size_bytes,
            result_pointers=(
                ("/figure_sha256",)
                if item.name.endswith(".svg")
                else (welfare_pointer,)
            ),
        )
        for item in report.outputs
    )


def _named_outputs(
    analysis_ref: LocalAnalysisReference,
    report: LocalAnalysisReport,
    *,
    names: tuple[str, ...],
    result_pointers: tuple[str, ...],
) -> tuple[AnalysisOutputRef, ...]:
    selected = tuple(item for item in report.outputs if item.name in names)
    if tuple(item.name for item in selected) != names:
        raise PaperSupportInvalid(
            "accepted claim output evidence is missing",
            finding_kind="output-missing",
        )
    return tuple(
        AnalysisOutputRef(
            analysis_ref=analysis_ref,
            name=item.name,
            sha256=item.sha256,
            size_bytes=item.size_bytes,
            result_pointers=result_pointers,
        )
        for item in selected
    )


def _identifier(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not normalized:
        raise PaperSupportInvalid(
            "claim quantity has no canonical identity",
            finding_kind="quantity-invalid",
        )
    return normalized


__all__ = ["valuation_claims"]
