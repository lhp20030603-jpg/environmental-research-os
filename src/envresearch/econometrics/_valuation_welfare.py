"""Independent delta-method checks for valuation welfare evidence."""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import TYPE_CHECKING, Protocol, cast

from envresearch.econometrics._valuation_evidence import (
    CoefficientEstimate,
    CovarianceEvidence,
    SensitivityEstimate,
    WelfareEstimate,
)
from envresearch.econometrics.valuation_contracts import (
    ContingentValuationSpec,
    DiscreteChoiceSpec,
    HedonicSpec,
    TravelCostSpec,
)

if TYPE_CHECKING:
    from envresearch.econometrics.valuation_results import (
        ContingentValuationResult,
        DiscreteChoiceResult,
        HedonicResult,
        TravelCostResult,
    )

ValuationSpec = (
    HedonicSpec | TravelCostSpec | ContingentValuationSpec | DiscreteChoiceSpec
)


class _ValuationResultLike(Protocol):
    coefficients: tuple[CoefficientEstimate, ...]
    covariance: CovarianceEvidence
    welfare: tuple[WelfareEstimate, ...]
    sensitivities: tuple[SensitivityEstimate, ...]


def reconstruct_welfare(
    spec: ValuationSpec, result: object
) -> tuple[WelfareEstimate, ...]:
    """Rebuild welfare rows without consuming the declared welfare fields."""
    if isinstance(spec, HedonicSpec):
        hedonic_result = cast("HedonicResult", result)
        coefficient = _coefficient(
            hedonic_result.coefficients, spec.columns.environmental_attribute
        )
        multiplier = {
            "level-level": 1.0,
            "log-level": hedonic_result.reference_price,
            "level-log": 1.0 / hedonic_result.reference_environment,
            "log-log": hedonic_result.reference_price
            / hedonic_result.reference_environment,
        }[spec.functional_form]
        return (
            _welfare(
                spec,
                name="implicit-price",
                estimate=coefficient.estimate * multiplier,
                std_error=abs(multiplier)
                * _covariance_se(hedonic_result.covariance, coefficient.term),
                transformation="marginal-implicit-price",
                numerator=spec.columns.environmental_attribute,
                denominator=spec.columns.price,
            ),
        )
    if isinstance(spec, TravelCostSpec):
        travel_result = cast("TravelCostResult", result)
        coefficient = _coefficient(travel_result.coefficients, spec.columns.travel_cost)
        return (
            _welfare(
                spec,
                name="consumer-surplus",
                estimate=-1.0 / coefficient.estimate,
                std_error=_covariance_se(travel_result.covariance, coefficient.term)
                / coefficient.estimate**2,
                transformation="negative-inverse-cost",
                numerator=None,
                denominator=spec.columns.travel_cost,
            ),
        )
    if isinstance(spec, ContingentValuationSpec):
        cv_result = cast("ContingentValuationResult", result)
        numerator = _coefficient(cv_result.coefficients, cv_result.intercept_term)
        denominator = _coefficient(cv_result.coefficients, spec.columns.bid)
        return (
            _welfare(
                spec,
                name="median-wtp",
                estimate=-numerator.estimate / denominator.estimate,
                std_error=ratio_standard_error(
                    cv_result.covariance,
                    numerator.term,
                    denominator.term,
                    numerator.estimate,
                    denominator.estimate,
                ),
                transformation="negative-intercept-over-bid",
                numerator=numerator.term,
                denominator=denominator.term,
            ),
        )
    dce_result = cast("DiscreteChoiceResult", result)
    denominator = _coefficient(dce_result.coefficients, spec.columns.cost)
    return tuple(
        _welfare(
            spec,
            name=f"{term}-wtp",
            estimate=-numerator.estimate / denominator.estimate,
            std_error=ratio_standard_error(
                dce_result.covariance,
                numerator.term,
                denominator.term,
                numerator.estimate,
                denominator.estimate,
            ),
            transformation="negative-attribute-over-cost",
            numerator=numerator.term,
            denominator=denominator.term,
        )
        for term in spec.columns.attributes
        for numerator in (_coefficient(dce_result.coefficients, term),)
    )


def valuation_evidence_matches(spec: ValuationSpec, result: object) -> bool:
    """Reconcile coefficient intervals, welfare, and derived sensitivity rows."""
    try:
        reconstructed = reconstruct_welfare(spec, result)
        typed = cast(_ValuationResultLike, result)
        if not _coefficient_intervals_match(
            typed.coefficients, typed.covariance, spec.confidence_level
        ):
            return False
        return (
            _sensitivity_matches(spec, result, reconstructed)
            and len(reconstructed) == len(typed.welfare)
            and all(
                _welfare_matches(expected, declared)
                for expected, declared in zip(reconstructed, typed.welfare, strict=True)
            )
        )
    except (AttributeError, KeyError, TypeError, ValueError, ZeroDivisionError):
        return False


def require_welfare_uncertainty(
    item: WelfareEstimate, expected_se: float, confidence_level: float
) -> None:
    """Require the declared interval to equal its registered delta-method result."""
    critical = NormalDist().inv_cdf(1.0 - (1.0 - confidence_level) / 2.0)
    if not (
        math.isclose(item.std_error, expected_se, rel_tol=1e-10, abs_tol=1e-10)
        and math.isclose(
            item.confidence_low,
            item.estimate - critical * expected_se,
            rel_tol=1e-10,
            abs_tol=1e-10,
        )
        and math.isclose(
            item.confidence_high,
            item.estimate + critical * expected_se,
            rel_tol=1e-10,
            abs_tol=1e-10,
        )
    ):
        raise ValueError("valuation welfare uncertainty is inconsistent")


def ratio_standard_error(
    covariance: CovarianceEvidence,
    numerator_term: str,
    denominator_term: str,
    numerator: float,
    denominator: float,
) -> float:
    """Recompute the delta-method error for negative numerator/denominator."""
    positions = {term: index for index, term in enumerate(covariance.terms)}
    numerator_index = positions[numerator_term]
    denominator_index = positions[denominator_term]
    numerator_gradient = -1.0 / denominator
    denominator_gradient = numerator / (denominator**2)
    variance = (
        numerator_gradient**2 * covariance.values[numerator_index][numerator_index]
        + denominator_gradient**2
        * covariance.values[denominator_index][denominator_index]
        + 2.0
        * numerator_gradient
        * denominator_gradient
        * covariance.values[numerator_index][denominator_index]
    )
    if variance < -1e-12:
        raise ValueError("valuation welfare variance is invalid")
    return math.sqrt(max(variance, 0.0))


def _coefficient(
    coefficients: tuple[CoefficientEstimate, ...], term: str
) -> CoefficientEstimate:
    matches = tuple(item for item in coefficients if item.term == term)
    if len(matches) != 1:
        raise ValueError("valuation coefficient is not uniquely registered")
    return matches[0]


def _covariance_se(covariance: CovarianceEvidence, term: str) -> float:
    index = covariance.terms.index(term)
    return math.sqrt(covariance.values[index][index])


def _welfare(
    spec: ValuationSpec,
    *,
    name: str,
    estimate: float,
    std_error: float,
    transformation: str,
    numerator: str | None,
    denominator: str,
) -> WelfareEstimate:
    critical = NormalDist().inv_cdf(1.0 - (1.0 - spec.confidence_level) / 2.0)
    return WelfareEstimate(
        name=name,
        estimate=estimate,
        std_error=std_error,
        confidence_low=estimate - critical * std_error,
        confidence_high=estimate + critical * std_error,
        currency=spec.currency,
        price_base=spec.price_base,
        time_basis=spec.time_basis,
        population_basis=spec.population_basis,
        transformation=transformation,  # type: ignore[arg-type]
        numerator_term=numerator,
        denominator_term=denominator,
    )


def _coefficient_intervals_match(
    coefficients: tuple[CoefficientEstimate, ...],
    covariance: CovarianceEvidence,
    confidence_level: float,
) -> bool:
    critical = NormalDist().inv_cdf(1.0 - (1.0 - confidence_level) / 2.0)
    return all(
        math.isclose(
            item.confidence_low,
            item.estimate - critical * _covariance_se(covariance, item.term),
            rel_tol=1e-10,
            abs_tol=1e-10,
        )
        and math.isclose(
            item.confidence_high,
            item.estimate + critical * _covariance_se(covariance, item.term),
            rel_tol=1e-10,
            abs_tol=1e-10,
        )
        for item in coefficients
    )


def _welfare_matches(expected: WelfareEstimate, declared: WelfareEstimate) -> bool:
    numeric = ("estimate", "std_error", "confidence_low", "confidence_high")
    return all(
        math.isclose(
            getattr(expected, name),
            getattr(declared, name),
            rel_tol=1e-10,
            abs_tol=1e-10,
        )
        for name in numeric
    ) and expected.model_dump(exclude=set(numeric)) == declared.model_dump(
        exclude=set(numeric)
    )


def _sensitivity_matches(
    spec: ValuationSpec,
    result: object,
    welfare: tuple[WelfareEstimate, ...],
) -> bool:
    """Rebuild sensitivity transformations from their registered raw estimates."""
    sensitivity = cast(_ValuationResultLike, result).sensitivities
    if len(sensitivity) != 1:
        return False
    item = sensitivity[0]
    if isinstance(spec, HedonicSpec):
        typed = cast("HedonicResult", result)
        expected_label = "alternative-functional-form"
        form = typed.sensitivity_form
        if form != spec.sensitivity_form:
            return False
        multiplier = {
            "level-level": 1.0,
            "log-level": typed.reference_price,
            "level-log": 1.0 / typed.reference_environment,
            "log-log": typed.reference_price / typed.reference_environment,
        }[form]
        estimate = typed.sensitivity_coefficient * multiplier
    elif isinstance(spec, TravelCostSpec):
        typed_travel = cast("TravelCostResult", result)
        expected_label = "exclude-substitute-controls"
        coefficient = typed_travel.sensitivity_cost_coefficient
        if coefficient >= 0 or typed_travel.sensitivity_family != spec.family:
            return False
        estimate = -1.0 / coefficient
    elif isinstance(spec, ContingentValuationSpec):
        typed_cv = cast("ContingentValuationResult", result)
        expected_label = "exclude-covariates"
        denominator = typed_cv.sensitivity_bid_coefficient
        if denominator >= 0 or typed_cv.sensitivity_link != spec.link:
            return False
        estimate = -typed_cv.sensitivity_intercept_coefficient / denominator
    else:
        typed_dce = cast("DiscreteChoiceResult", result)
        expected_label = "include-alternative-specific-constants"
        denominator = typed_dce.sensitivity_cost_coefficient
        if (
            denominator >= -spec.min_abs_cost_coefficient
            or typed_dce.sensitivity_form != "conditional-logit"
        ):
            return False
        estimate = -typed_dce.sensitivity_attribute_coefficient / denominator
    baseline = welfare[0].estimate
    return (
        item.label == expected_label
        and math.isclose(item.estimate, estimate, rel_tol=1e-10, abs_tol=1e-10)
        and math.isclose(item.baseline_estimate, baseline, rel_tol=1e-10, abs_tol=1e-10)
        and math.isclose(
            item.absolute_change,
            abs(estimate - baseline),
            rel_tol=1e-10,
            abs_tol=1e-10,
        )
    )
