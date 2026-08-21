"""Shared finite evidence models used by valuation results."""

from __future__ import annotations

import math
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from envresearch.econometrics.contracts import STRICT_FROZEN, _canonical_name
from envresearch.models.artifact import ArtifactRef

ValuationMethod = Literal[
    "hedonic-pricing", "travel-cost", "contingent-valuation", "dce-clogit"
]
WelfareTransformation = Literal[
    "marginal-implicit-price",
    "negative-inverse-cost",
    "negative-intercept-over-bid",
    "negative-attribute-over-cost",
]


def finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("valuation evidence must be finite")
    return value


class CoefficientEstimate(BaseModel):
    model_config = STRICT_FROZEN
    term: str
    estimate: float
    std_error: float = Field(ge=0.0)
    confidence_low: float
    confidence_high: float

    @field_validator("term")
    @classmethod
    def canonical_term(cls, value: str) -> str:
        return _canonical_name(value, "coefficient term")

    @field_validator("estimate", "std_error", "confidence_low", "confidence_high")
    @classmethod
    def finite_value(cls, value: float) -> float:
        return finite(value)

    @model_validator(mode="after")
    def ordered(self) -> CoefficientEstimate:
        if not self.confidence_low <= self.estimate <= self.confidence_high:
            raise ValueError("confidence interval must contain the estimate")
        return self


def _positive_semidefinite(values: tuple[tuple[float, ...], ...]) -> bool:
    matrix = [list(row) for row in values]
    tolerance = 1e-10
    for pivot_index in range(len(matrix)):
        pivot = matrix[pivot_index][pivot_index]
        if pivot < -tolerance:
            return False
        if abs(pivot) <= tolerance:
            if any(
                abs(matrix[row][pivot_index]) > tolerance
                for row in range(pivot_index + 1, len(matrix))
            ):
                return False
            continue
        for row in range(pivot_index + 1, len(matrix)):
            for column in range(row, len(matrix)):
                updated = matrix[column][row] - (
                    matrix[column][pivot_index] * matrix[row][pivot_index] / pivot
                )
                matrix[column][row] = updated
                matrix[row][column] = updated
    return True


class CovarianceEvidence(BaseModel):
    model_config = STRICT_FROZEN
    terms: tuple[str, ...] = Field(min_length=1)
    values: tuple[tuple[float, ...], ...] = Field(min_length=1)

    @field_validator("terms")
    @classmethod
    def canonical_terms(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        result = tuple(_canonical_name(item, "covariance term") for item in value)
        if len(result) != len(set(result)):
            raise ValueError("covariance terms must be unique")
        return result

    @model_validator(mode="after")
    def square_symmetric(self) -> CovarianceEvidence:
        size = len(self.terms)
        if len(self.values) != size or any(len(row) != size for row in self.values):
            raise ValueError("covariance matrix dimensions do not match terms")
        if not all(math.isfinite(value) for row in self.values for value in row):
            raise ValueError("covariance values must be finite")
        for left in range(size):
            if self.values[left][left] < 0:
                raise ValueError("covariance diagonal must be nonnegative")
            for right in range(size):
                if not math.isclose(
                    self.values[left][right],
                    self.values[right][left],
                    abs_tol=1e-12,
                ):
                    raise ValueError("covariance matrix must be symmetric")
        if not _positive_semidefinite(self.values):
            raise ValueError("covariance matrix must be positive semidefinite")
        return self

    def matches(self, coefficients: tuple[CoefficientEstimate, ...]) -> bool:
        if tuple(item.term for item in coefficients) != self.terms:
            return False
        return all(
            math.isclose(self.values[index][index], item.std_error**2, abs_tol=1e-12)
            for index, item in enumerate(coefficients)
        )


class WelfareEstimate(BaseModel):
    model_config = STRICT_FROZEN
    name: str
    estimate: float
    std_error: float = Field(ge=0.0)
    confidence_low: float
    confidence_high: float
    currency: str
    price_base: str
    time_basis: str
    population_basis: str
    transformation: WelfareTransformation
    numerator_term: str | None
    denominator_term: str

    @field_validator(
        "name",
        "currency",
        "price_base",
        "time_basis",
        "population_basis",
        "numerator_term",
        "denominator_term",
    )
    @classmethod
    def canonical_text(cls, value: str | None) -> str | None:
        return None if value is None else _canonical_name(value, "welfare field")

    @field_validator("estimate", "std_error", "confidence_low", "confidence_high")
    @classmethod
    def finite_value(cls, value: float) -> float:
        return finite(value)

    @model_validator(mode="after")
    def ordered(self) -> WelfareEstimate:
        if not self.confidence_low <= self.estimate <= self.confidence_high:
            raise ValueError("welfare interval must contain the estimate")
        return self


class SensitivityEstimate(BaseModel):
    model_config = STRICT_FROZEN
    label: str
    estimate: float
    baseline_estimate: float
    absolute_change: float = Field(ge=0.0)

    @field_validator("label")
    @classmethod
    def canonical_label(cls, value: str) -> str:
        return _canonical_name(value, "sensitivity label")

    @field_validator("estimate", "baseline_estimate", "absolute_change")
    @classmethod
    def finite_value(cls, value: float) -> float:
        return finite(value)

    @model_validator(mode="after")
    def reconstructed(self) -> SensitivityEstimate:
        expected = abs(self.estimate - self.baseline_estimate)
        if not math.isclose(self.absolute_change, expected, abs_tol=1e-12):
            raise ValueError("sensitivity absolute change is inconsistent")
        return self


class ValuationConfiguration(BaseModel):
    model_config = STRICT_FROZEN
    method_id: ValuationMethod
    r_version: str
    confidence_level: float = Field(gt=0.0, lt=1.0)
    cluster_column: str | None
    fixed_effects: tuple[str, ...]
    functional_form: Literal["level-level", "log-level", "level-log", "log-log"] | None
    family: Literal["poisson", "negative-binomial"] | None
    link: Literal["logit", "probit"] | None
    package_authorities: tuple[ArtifactRef, ...] = ()

    @field_validator("package_authorities")
    @classmethod
    def unique_package_authorities(
        cls, value: tuple[ArtifactRef, ...]
    ) -> tuple[ArtifactRef, ...]:
        if len(value) != len(set(value)):
            raise ValueError("package authority references must be unique")
        return value

    @model_validator(mode="after")
    def method_configuration(self) -> ValuationConfiguration:
        expected = {
            "hedonic-pricing": (True, False, False),
            "travel-cost": (False, True, False),
            "contingent-valuation": (False, False, True),
            "dce-clogit": (False, False, False),
        }[self.method_id]
        if (
            tuple(
                value is not None
                for value in (self.functional_form, self.family, self.link)
            )
            != expected
        ):
            raise ValueError("valuation configuration does not match its method")
        return self


class BidYesShare(BaseModel):
    """Typed observed yes-share evidence for one declared CV bid."""

    model_config = STRICT_FROZEN
    bid: float = Field(gt=0.0)
    yes_count: int = Field(ge=0)
    observations: int = Field(gt=0)
    yes_share: float = Field(ge=0.0, le=1.0)

    @field_validator("bid", "yes_share")
    @classmethod
    def finite_value(cls, value: float) -> float:
        return finite(value)

    @model_validator(mode="after")
    def reconcile(self) -> BidYesShare:
        if self.yes_count > self.observations or not math.isclose(
            self.yes_share, self.yes_count / self.observations, abs_tol=1e-12
        ):
            raise ValueError("CV bid yes-share counts do not reconcile")
        return self


def monotone_bid_yes_shares(values: tuple[BidYesShare, ...]) -> bool:
    """Return whether bid evidence is unique, ordered, and nonincreasing."""
    bids = tuple(item.bid for item in values)
    return (
        len(values) >= 2
        and bids == tuple(sorted(set(bids)))
        and all(
            left.yes_share + 1e-12 >= right.yes_share
            for left, right in pairwise(values)
        )
    )


def bid_yes_shares_match(
    reported: tuple[BidYesShare, ...], reconstructed: tuple[BidYesShare, ...]
) -> bool:
    """Compare exact counts while allowing only R numeric serialization error."""
    return len(reported) == len(reconstructed) and all(
        left.yes_count == right.yes_count
        and left.observations == right.observations
        and math.isclose(left.bid, right.bid, rel_tol=1e-12, abs_tol=1e-12)
        and math.isclose(left.yes_share, right.yes_share, rel_tol=1e-12, abs_tol=1e-12)
        for left, right in zip(reported, reconstructed, strict=True)
    )


class ValuationSupport(BaseModel):
    model_config = STRICT_FROZEN
    observations: int = Field(gt=0)
    primary_units: int = Field(gt=0)
    groups: int | None = Field(default=None, gt=0)
    zero_or_no_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def reconcile(self) -> ValuationSupport:
        if (
            self.primary_units > self.observations
            or self.zero_or_no_count > self.observations
        ):
            raise ValueError("valuation support counts do not reconcile")
        return self
