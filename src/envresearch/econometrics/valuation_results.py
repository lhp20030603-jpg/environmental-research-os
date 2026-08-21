"""Typed scientific result evidence for valuation methods."""

from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from envresearch.econometrics._valuation_evidence import (
    BidYesShare,
    CoefficientEstimate,
    CovarianceEvidence,
    SensitivityEstimate,
    ValuationConfiguration,
    ValuationSupport,
    WelfareEstimate,
    finite,
    monotone_bid_yes_shares,
)
from envresearch.econometrics._valuation_welfare import (
    ratio_standard_error,
    require_welfare_uncertainty,
)
from envresearch.econometrics.contracts import STRICT_FROZEN

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class _ValuationResult(BaseModel):
    model_config = STRICT_FROZEN
    coefficients: tuple[CoefficientEstimate, ...] = Field(min_length=1)
    covariance: CovarianceEvidence
    welfare: tuple[WelfareEstimate, ...] = Field(min_length=1)
    support: ValuationSupport
    sensitivities: tuple[SensitivityEstimate, ...] = Field(min_length=1)
    max_sensitivity_change: float = Field(gt=0.0)
    configuration: ValuationConfiguration
    figure_sha256: str

    @field_validator("max_sensitivity_change")
    @classmethod
    def finite_threshold(cls, value: float) -> float:
        return finite(value)

    @field_validator("figure_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("figure digest must be canonical")
        return value

    @model_validator(mode="after")
    def shared_evidence(self) -> _ValuationResult:
        if not self.covariance.matches(self.coefficients):
            raise ValueError("covariance does not match coefficient evidence")
        if len({item.label for item in self.sensitivities}) != len(self.sensitivities):
            raise ValueError("sensitivity labels must be unique")
        if (
            max(item.absolute_change for item in self.sensitivities)
            > self.max_sensitivity_change
        ):
            raise ValueError("valuation sensitivity exceeds its threshold")
        return self


class HedonicResult(_ValuationResult):
    method_id: Literal["hedonic-pricing"]
    environmental_term: str
    price_term: str
    reference_price: float = Field(gt=0.0)
    reference_environment: float = Field(gt=0.0)
    condition_number: float = Field(gt=0.0)
    max_condition_number: float = Field(gt=1.0)
    max_vif: float = Field(ge=1.0)
    sensitivity_coefficient: float
    sensitivity_form: Literal["level-level", "log-level", "level-log", "log-log"]

    @field_validator("sensitivity_coefficient")
    @classmethod
    def finite_sensitivity_coefficient(cls, value: float) -> float:
        return finite(value)

    @model_validator(mode="after")
    def coherent(self) -> HedonicResult:
        if self.configuration.method_id != self.method_id:
            raise ValueError("result configuration selects another method")
        if self.condition_number > self.max_condition_number:
            raise ValueError("hedonic collinearity exceeds its threshold")
        if {item.label for item in self.sensitivities} != {
            "alternative-functional-form"
        }:
            raise ValueError("hedonic sensitivity evidence is not registered")
        terms = tuple(
            item for item in self.coefficients if item.term == self.environmental_term
        )
        if len(terms) != 1:
            raise ValueError("hedonic environmental coefficient is missing")
        form = self.configuration.functional_form
        if form is None:  # guarded by ValuationConfiguration
            raise ValueError("hedonic functional form is missing")
        beta = terms[0].estimate
        expected = {
            "level-level": beta,
            "log-level": beta * self.reference_price,
            "level-log": beta / self.reference_environment,
            "log-log": beta * self.reference_price / self.reference_environment,
        }[form]
        item = self.welfare[0] if len(self.welfare) == 1 else None
        sensitivity = self.sensitivities[0]
        if (
            item is None
            or item.transformation != "marginal-implicit-price"
            or item.numerator_term != self.environmental_term
            or item.denominator_term != self.price_term
            or not math.isclose(item.estimate, expected, abs_tol=1e-12)
            or not math.isclose(
                sensitivity.baseline_estimate, item.estimate, abs_tol=1e-12
            )
        ):
            raise ValueError("hedonic welfare evidence is inconsistent")
        multiplier = {
            "level-level": 1.0,
            "log-level": self.reference_price,
            "level-log": 1.0 / self.reference_environment,
            "log-log": self.reference_price / self.reference_environment,
        }[form]
        require_welfare_uncertainty(
            item,
            abs(multiplier) * terms[0].std_error,
            self.configuration.confidence_level,
        )
        sensitivity_multiplier = {
            "level-level": 1.0,
            "log-level": self.reference_price,
            "level-log": 1.0 / self.reference_environment,
            "log-log": self.reference_price / self.reference_environment,
        }[self.sensitivity_form]
        if not math.isclose(
            sensitivity.estimate,
            self.sensitivity_coefficient * sensitivity_multiplier,
            rel_tol=1e-10,
            abs_tol=1e-10,
        ):
            raise ValueError("hedonic sensitivity transformation is inconsistent")
        return self


class TravelCostResult(_ValuationResult):
    method_id: Literal["travel-cost"]
    cost_term: str
    dispersion: float = Field(ge=0.0)
    max_dispersion: float = Field(gt=0.0)
    log_likelihood: float
    deviance: float = Field(ge=0.0)
    residual_df: int = Field(gt=0)
    theta: float | None = Field(default=None, gt=0.0)
    sensitivity_cost_coefficient: float
    sensitivity_family: Literal["poisson", "negative-binomial"]

    @field_validator("log_likelihood", "deviance", "sensitivity_cost_coefficient")
    @classmethod
    def finite_diagnostics(cls, value: float) -> float:
        return finite(value)

    @model_validator(mode="after")
    def coherent(self) -> TravelCostResult:
        if self.configuration.method_id != self.method_id:
            raise ValueError("result configuration selects another method")
        if (self.configuration.family == "negative-binomial") != (
            self.theta is not None
        ):
            raise ValueError("travel-cost theta does not match the count family")
        if (
            self.configuration.family == "poisson"
            and self.dispersion > self.max_dispersion
        ):
            raise ValueError("travel-cost dispersion exceeds its threshold")
        cost = tuple(item for item in self.coefficients if item.term == self.cost_term)
        if len(cost) != 1 or cost[0].estimate >= 0:
            raise ValueError("travel-cost cost coefficient must be negative")
        if {item.label for item in self.sensitivities} != {
            "exclude-substitute-controls"
        }:
            raise ValueError("travel-cost sensitivity evidence is not registered")
        item = self.welfare[0] if len(self.welfare) == 1 else None
        sensitivity = self.sensitivities[0]
        if (
            item is None
            or item.transformation != "negative-inverse-cost"
            or item.denominator_term != self.cost_term
            or item.numerator_term is not None
            or not math.isclose(item.estimate, -1.0 / cost[0].estimate, abs_tol=1e-12)
            or not math.isclose(
                sensitivity.baseline_estimate, item.estimate, abs_tol=1e-12
            )
        ):
            raise ValueError("travel-cost welfare evidence is inconsistent")
        require_welfare_uncertainty(
            item,
            cost[0].std_error / (cost[0].estimate ** 2),
            self.configuration.confidence_level,
        )
        if (
            self.sensitivity_cost_coefficient >= 0
            or self.sensitivity_family != self.configuration.family
            or not math.isclose(
                sensitivity.estimate,
                -1.0 / self.sensitivity_cost_coefficient,
                rel_tol=1e-10,
                abs_tol=1e-10,
            )
        ):
            raise ValueError("travel-cost sensitivity transformation is inconsistent")
        return self


class ContingentValuationResult(_ValuationResult):
    method_id: Literal["contingent-valuation"]
    bid_term: str
    intercept_term: str
    extreme_probability_share: float = Field(ge=0.0, le=1.0)
    max_extreme_probability_share: float = Field(gt=0.0, lt=1.0)
    probability_min: float = Field(gt=0.0, lt=1.0)
    probability_max: float = Field(gt=0.0, lt=1.0)
    bid_yes_shares: tuple[BidYesShare, ...] = Field(min_length=2)
    sensitivity_intercept_coefficient: float
    sensitivity_bid_coefficient: float
    sensitivity_link: Literal["logit", "probit"]

    @field_validator("sensitivity_intercept_coefficient", "sensitivity_bid_coefficient")
    @classmethod
    def finite_sensitivity_bid(cls, value: float) -> float:
        return finite(value)

    @model_validator(mode="after")
    def coherent(self) -> ContingentValuationResult:
        if self.configuration.method_id != self.method_id:
            raise ValueError("result configuration selects another method")
        if self.probability_min > self.probability_max:
            raise ValueError("CV probability range is invalid")
        if self.extreme_probability_share > self.max_extreme_probability_share:
            raise ValueError("CV extreme probability share exceeds its threshold")
        if not monotone_bid_yes_shares(self.bid_yes_shares):
            raise ValueError("CV bid yes-share evidence is inconsistent")
        bid = tuple(item for item in self.coefficients if item.term == self.bid_term)
        intercept = tuple(
            item for item in self.coefficients if item.term == self.intercept_term
        )
        if len(bid) != 1 or bid[0].estimate >= 0:
            raise ValueError("CV bid coefficient must be negative")
        if len(intercept) != 1:
            raise ValueError("CV intercept coefficient is missing")
        if {item.label for item in self.sensitivities} != {"exclude-covariates"}:
            raise ValueError("CV sensitivity evidence is not registered")
        item = self.welfare[0] if len(self.welfare) == 1 else None
        expected = -intercept[0].estimate / bid[0].estimate
        if (
            item is None
            or item.transformation != "negative-intercept-over-bid"
            or item.numerator_term != self.intercept_term
            or item.denominator_term != self.bid_term
            or not math.isclose(item.estimate, expected, abs_tol=1e-12)
        ):
            raise ValueError("CV welfare evidence is inconsistent")
        require_welfare_uncertainty(
            item,
            ratio_standard_error(
                self.covariance,
                self.intercept_term,
                self.bid_term,
                intercept[0].estimate,
                bid[0].estimate,
            ),
            self.configuration.confidence_level,
        )
        sensitivity = self.sensitivities[0]
        if (
            not math.isclose(
                sensitivity.baseline_estimate, item.estimate, abs_tol=1e-12
            )
            or self.sensitivity_link != self.configuration.link
            or self.sensitivity_bid_coefficient >= 0
            or not math.isclose(
                sensitivity.estimate,
                -self.sensitivity_intercept_coefficient
                / self.sensitivity_bid_coefficient,
                rel_tol=1e-10,
                abs_tol=1e-10,
            )
        ):
            raise ValueError("CV sensitivity evidence is inconsistent")
        return self


class DiscreteChoiceResult(_ValuationResult):
    method_id: Literal["dce-clogit"]
    cost_term: str
    attribute_terms: tuple[str, ...] = Field(min_length=1)
    min_abs_cost_coefficient: float = Field(gt=0.0)
    sensitivity_attribute_coefficient: float
    sensitivity_cost_coefficient: float
    sensitivity_form: Literal["conditional-logit"]

    @field_validator(
        "sensitivity_attribute_coefficient", "sensitivity_cost_coefficient"
    )
    @classmethod
    def finite_sensitivity_cost(cls, value: float) -> float:
        return finite(value)

    @model_validator(mode="after")
    def coherent(self) -> DiscreteChoiceResult:
        if self.configuration.method_id != self.method_id:
            raise ValueError("result configuration selects another method")
        cost = tuple(item for item in self.coefficients if item.term == self.cost_term)
        if len(cost) != 1 or cost[0].estimate >= -self.min_abs_cost_coefficient:
            raise ValueError("DCE cost coefficient is not negative and identified")
        if {item.label for item in self.sensitivities} != {
            "include-alternative-specific-constants"
        }:
            raise ValueError("DCE sensitivity evidence is not registered")
        if len(self.attribute_terms) != len(set(self.attribute_terms)):
            raise ValueError("DCE attribute terms must be unique")
        by_term = {item.term: item for item in self.coefficients}
        if any(term not in by_term for term in self.attribute_terms):
            raise ValueError("DCE attribute coefficient is missing")
        numerators = tuple(item.numerator_term for item in self.welfare)
        if (
            len(self.welfare) != len(self.attribute_terms)
            or None in numerators
            or len(set(numerators)) != len(numerators)
        ):
            raise ValueError("DCE requires exactly one welfare row per attribute")
        welfare = {item.numerator_term: item for item in self.welfare}
        if set(welfare) != set(self.attribute_terms):
            raise ValueError("DCE welfare evidence does not cover each attribute")
        for term in self.attribute_terms:
            item = welfare[term]
            expected = -by_term[term].estimate / cost[0].estimate
            if (
                item.transformation != "negative-attribute-over-cost"
                or item.denominator_term != self.cost_term
                or not math.isclose(item.estimate, expected, abs_tol=1e-12)
            ):
                raise ValueError("DCE welfare evidence is inconsistent")
            require_welfare_uncertainty(
                item,
                ratio_standard_error(
                    self.covariance,
                    term,
                    self.cost_term,
                    by_term[term].estimate,
                    cost[0].estimate,
                ),
                self.configuration.confidence_level,
            )
        sensitivity = self.sensitivities[0]
        first = welfare[self.attribute_terms[0]]
        if (
            not math.isclose(
                sensitivity.baseline_estimate, first.estimate, abs_tol=1e-12
            )
            or self.sensitivity_form != "conditional-logit"
            or self.sensitivity_cost_coefficient >= -self.min_abs_cost_coefficient
            or not math.isclose(
                sensitivity.estimate,
                -self.sensitivity_attribute_coefficient
                / self.sensitivity_cost_coefficient,
                rel_tol=1e-10,
                abs_tol=1e-10,
            )
        ):
            raise ValueError("DCE sensitivity evidence is inconsistent")
        return self


__all__ = [
    "BidYesShare",
    "CoefficientEstimate",
    "ContingentValuationResult",
    "CovarianceEvidence",
    "DiscreteChoiceResult",
    "HedonicResult",
    "SensitivityEstimate",
    "TravelCostResult",
    "ValuationConfiguration",
    "ValuationSupport",
    "WelfareEstimate",
]
