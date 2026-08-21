from __future__ import annotations

import pytest
from pydantic import ValidationError

from envresearch.econometrics._valuation_evidence import BidYesShare
from envresearch.econometrics.valuation_results import (
    CoefficientEstimate,
    ContingentValuationResult,
    CovarianceEvidence,
    DiscreteChoiceResult,
    HedonicResult,
    SensitivityEstimate,
    TravelCostResult,
    ValuationConfiguration,
    ValuationSupport,
    WelfareEstimate,
)


def coefficient(term: str = "cost", estimate: float = -0.5) -> CoefficientEstimate:
    return CoefficientEstimate(
        term=term,
        estimate=estimate,
        std_error=0.1,
        confidence_low=estimate - 0.2,
        confidence_high=estimate + 0.2,
    )


def welfare(estimate: float = 2.0) -> WelfareEstimate:
    return WelfareEstimate(
        name="consumer-surplus",
        estimate=estimate,
        std_error=0.4,
        confidence_low=estimate - 0.78398559381602,
        confidence_high=estimate + 0.78398559381602,
        currency="USD",
        price_base="2025",
        time_basis="per-year",
        population_basis="sample-household",
        transformation="negative-inverse-cost",
        numerator_term=None,
        denominator_term="cost",
    )


def test_shared_valuation_evidence_is_finite_and_frozen() -> None:
    value = welfare()
    assert value.estimate == 2.0
    with pytest.raises(ValidationError):
        WelfareEstimate.model_validate({**value.model_dump(), "estimate": float("nan")})
    with pytest.raises(ValidationError):
        WelfareEstimate.model_validate({**value.model_dump(), "currency": " "})


def test_coefficient_interval_must_contain_estimate() -> None:
    with pytest.raises(ValidationError, match="contain"):
        CoefficientEstimate(
            term="cost",
            estimate=-0.5,
            std_error=0.1,
            confidence_low=0.0,
            confidence_high=1.0,
        )


def test_sensitivity_change_is_reconstructed_not_caller_selected() -> None:
    with pytest.raises(ValidationError, match="absolute change"):
        SensitivityEstimate(
            label="drop-substitute-control",
            estimate=-0.3,
            baseline_estimate=-0.5,
            absolute_change=0.01,
        )


def test_configuration_requires_method_specific_design() -> None:
    with pytest.raises(ValidationError, match="configuration"):
        ValuationConfiguration(
            method_id="travel-cost",
            r_version="R version 4.4.3",
            confidence_level=0.95,
            cluster_column=None,
            fixed_effects=(),
            functional_form="log-level",
            family=None,
            link=None,
        )


def test_welfare_interval_must_contain_estimate() -> None:
    payload = welfare().model_dump()
    payload["confidence_low"] = 3.0
    with pytest.raises(ValidationError, match="contain"):
        WelfareEstimate.model_validate(payload)


def test_covariance_is_finite_symmetric_and_matches_standard_errors() -> None:
    with pytest.raises(ValidationError, match="symmetric"):
        CovarianceEvidence(
            terms=("intercept", "bid"),
            values=((1.0, 0.2), (0.1, 0.04)),
        )
    covariance = CovarianceEvidence(
        terms=("intercept", "bid"), values=((1.0, 0.2), (0.2, 0.04))
    )
    assert (
        covariance.matches((coefficient("intercept", 1.0), coefficient("bid", -0.5)))
        is False
    )
    with pytest.raises(ValidationError, match="positive semidefinite"):
        CovarianceEvidence(terms=("a", "b"), values=((1.0, 2.0), (2.0, 1.0)))


def _configuration(
    method_id: str, *, family: str | None = None, link: str | None = None
) -> ValuationConfiguration:
    return ValuationConfiguration(
        method_id=method_id,
        r_version="R version 4.4.3",
        confidence_level=0.95,
        cluster_column=None,
        fixed_effects=(),
        functional_form=None,
        family=family,
        link=link,
    )


def _support() -> ValuationSupport:
    return ValuationSupport(
        observations=10, primary_units=10, groups=1, zero_or_no_count=2
    )


def _sensitivity(label: str, baseline: float) -> SensitivityEstimate:
    return SensitivityEstimate(
        label=label,
        estimate=baseline + 0.1,
        baseline_estimate=baseline,
        absolute_change=0.1,
    )


def test_travel_cost_rejects_positive_slope_and_forged_welfare() -> None:
    payload = {
        "method_id": "travel-cost",
        "coefficients": (coefficient("cost", 0.5),),
        "covariance": CovarianceEvidence(terms=("cost",), values=((0.01,),)),
        "welfare": (welfare(2.0),),
        "support": _support(),
        "sensitivities": (_sensitivity("exclude-substitute-controls", 0.5),),
        "max_sensitivity_change": 0.25,
        "configuration": _configuration("travel-cost", family="negative-binomial"),
        "figure_sha256": "a" * 64,
        "cost_term": "cost",
        "dispersion": 1.0,
        "max_dispersion": 2.0,
        "log_likelihood": -30.0,
        "deviance": 20.0,
        "residual_df": 5,
        "theta": 10.0,
        "sensitivity_cost_coefficient": -2.0,
        "sensitivity_family": "negative-binomial",
    }
    with pytest.raises(ValidationError, match="cost coefficient"):
        TravelCostResult.model_validate(payload)
    payload["coefficients"] = (coefficient("cost", -0.5),)
    payload["sensitivities"] = (_sensitivity("exclude-substitute-controls", -0.5),)
    payload["welfare"] = (welfare(999.0),)
    with pytest.raises(ValidationError, match="welfare"):
        TravelCostResult.model_validate(payload)


def test_cv_rejects_positive_bid_coefficient() -> None:
    payload = {
        "method_id": "contingent-valuation",
        "coefficients": (coefficient("intercept", 1.0), coefficient("bid", 0.5)),
        "covariance": CovarianceEvidence(
            terms=("intercept", "bid"), values=((0.01, 0.0), (0.0, 0.01))
        ),
        "welfare": (
            WelfareEstimate(
                **{
                    **welfare().model_dump(),
                    "name": "median-wtp",
                    "transformation": "negative-intercept-over-bid",
                    "numerator_term": "intercept",
                    "denominator_term": "bid",
                }
            ),
        ),
        "support": _support(),
        "sensitivities": (_sensitivity("exclude-covariates", 0.5),),
        "max_sensitivity_change": 0.25,
        "configuration": _configuration("contingent-valuation", link="logit"),
        "figure_sha256": "a" * 64,
        "bid_term": "bid",
        "intercept_term": "intercept",
        "extreme_probability_share": 0.05,
        "max_extreme_probability_share": 0.10,
        "probability_min": 0.1,
        "probability_max": 0.9,
        "bid_yes_shares": (
            BidYesShare(bid=10, yes_count=1, observations=2, yes_share=0.5),
            BidYesShare(bid=20, yes_count=0, observations=2, yes_share=0),
        ),
        "sensitivity_intercept_coefficient": 1.0,
        "sensitivity_bid_coefficient": -0.5,
        "sensitivity_link": "logit",
    }
    with pytest.raises(ValidationError, match="bid coefficient"):
        ContingentValuationResult.model_validate(payload)


def test_hedonic_reconstructs_functional_form_welfare() -> None:
    configuration = ValuationConfiguration(
        method_id="hedonic-pricing",
        r_version="R version 4.4.3",
        confidence_level=0.95,
        cluster_column=None,
        fixed_effects=(),
        functional_form="log-level",
        family=None,
        link=None,
    )
    value = welfare(999.0).model_copy(
        update={
            "name": "implicit-price",
            "transformation": "marginal-implicit-price",
            "numerator_term": "pm25",
            "denominator_term": "price",
        }
    )
    payload = {
        "method_id": "hedonic-pricing",
        "coefficients": (coefficient("pm25", -0.5),),
        "covariance": CovarianceEvidence(terms=("pm25",), values=((0.01,),)),
        "welfare": (value,),
        "support": _support(),
        "sensitivities": (_sensitivity("alternative-functional-form", -0.5),),
        "max_sensitivity_change": 0.25,
        "configuration": configuration,
        "figure_sha256": "a" * 64,
        "environmental_term": "pm25",
        "price_term": "price",
        "reference_price": 100.0,
        "reference_environment": 10.0,
        "condition_number": 2.0,
        "max_condition_number": 30.0,
        "max_vif": 1.5,
        "sensitivity_coefficient": -0.499,
        "sensitivity_form": "level-level",
    }
    with pytest.raises(ValidationError, match="hedonic welfare"):
        HedonicResult.model_validate(payload)


def test_dce_reconstructs_each_attribute_wtp() -> None:
    value = welfare(999.0).model_copy(
        update={
            "name": "air-wtp",
            "transformation": "negative-attribute-over-cost",
            "numerator_term": "air",
            "denominator_term": "cost",
        }
    )
    payload = {
        "method_id": "dce-clogit",
        "coefficients": (coefficient("cost", -0.5), coefficient("air", 1.0)),
        "covariance": CovarianceEvidence(
            terms=("cost", "air"), values=((0.01, 0.0), (0.0, 0.01))
        ),
        "welfare": (value,),
        "support": _support(),
        "sensitivities": (
            _sensitivity("include-alternative-specific-constants", -0.5),
        ),
        "max_sensitivity_change": 0.25,
        "configuration": _configuration("dce-clogit"),
        "figure_sha256": "a" * 64,
        "cost_term": "cost",
        "attribute_terms": ("air",),
        "min_abs_cost_coefficient": 0.001,
        "sensitivity_attribute_coefficient": 1.05,
        "sensitivity_cost_coefficient": -0.5,
        "sensitivity_form": "conditional-logit",
    }
    with pytest.raises(ValidationError, match="DCE welfare"):
        DiscreteChoiceResult.model_validate(payload)
    valid = welfare(2.0).model_copy(
        update={
            "name": "air-wtp",
            "transformation": "negative-attribute-over-cost",
            "numerator_term": "air",
            "denominator_term": "cost",
        }
    )
    payload["welfare"] = (valid, valid)
    with pytest.raises(ValidationError, match="exactly one"):
        DiscreteChoiceResult.model_validate(payload)


def test_hedonic_rejects_wrong_welfare_denominator() -> None:
    configuration = ValuationConfiguration(
        method_id="hedonic-pricing",
        r_version="R version 4.4.3",
        confidence_level=0.95,
        cluster_column=None,
        fixed_effects=(),
        functional_form="level-level",
        family=None,
        link=None,
    )
    value = welfare(-0.5).model_copy(
        update={
            "name": "implicit-price",
            "transformation": "marginal-implicit-price",
            "numerator_term": "pm25",
            "denominator_term": "wrong-price",
        }
    )
    payload = {
        "method_id": "hedonic-pricing",
        "coefficients": (coefficient("pm25", -0.5),),
        "covariance": CovarianceEvidence(terms=("pm25",), values=((0.01,),)),
        "welfare": (value,),
        "support": _support(),
        "sensitivities": (_sensitivity("alternative-functional-form", -0.5),),
        "max_sensitivity_change": 0.25,
        "configuration": configuration,
        "figure_sha256": "a" * 64,
        "environmental_term": "pm25",
        "price_term": "price",
        "reference_price": 100.0,
        "reference_environment": 10.0,
        "condition_number": 2.0,
        "max_condition_number": 30.0,
        "max_vif": 1.5,
        "sensitivity_coefficient": -0.4,
        "sensitivity_form": "level-level",
    }
    with pytest.raises(ValidationError, match="hedonic welfare"):
        HedonicResult.model_validate(payload)
