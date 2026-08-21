"""Strict execution authorities for the V0.3.1 valuation core."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from envresearch.econometrics.contracts import (
    STRICT_FROZEN,
    ResourceBudget,
    _canonical_name,
)

HedonicForm = Literal["level-level", "log-level", "level-log", "log-log"]
CountFamily = Literal["poisson", "negative-binomial"]
BinaryLink = Literal["logit", "probit"]


def _names(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    result = tuple(_canonical_name(value, label) for value in values)
    if len(result) != len(set(result)):
        raise ValueError(f"{label} columns must be unique")
    return result


def _unique(values: tuple[str, ...]) -> None:
    if len(values) != len(set(values)):
        raise ValueError("column roles must be unique")


class HedonicColumns(BaseModel):
    model_config = STRICT_FROZEN
    transaction: str
    price: str
    environmental_attribute: str
    controls: tuple[str, ...] = Field(min_length=1)
    fixed_effects: tuple[str, ...] = ()

    @field_validator("transaction", "price", "environmental_attribute")
    @classmethod
    def primary(cls, value: str) -> str:
        return _canonical_name(value, "column")

    @field_validator("controls", "fixed_effects")
    @classmethod
    def tuples(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        return _names(value, (info.field_name or "column").replace("_", " "))

    @model_validator(mode="after")
    def coherent(self) -> HedonicColumns:
        _unique(
            (
                self.transaction,
                self.price,
                self.environmental_attribute,
                *self.controls,
                *self.fixed_effects,
            )
        )
        return self

    def required(self) -> tuple[str, ...]:
        return (
            self.transaction,
            self.price,
            self.environmental_attribute,
            *self.controls,
            *self.fixed_effects,
        )


class TravelCostColumns(BaseModel):
    model_config = STRICT_FROZEN
    unit: str
    visits: str
    travel_cost: str
    exposure: str
    site: str
    substitute_controls: tuple[str, ...] = Field(min_length=1)

    @field_validator("unit", "visits", "travel_cost", "exposure", "site")
    @classmethod
    def primary(cls, value: str) -> str:
        return _canonical_name(value, "column")

    @field_validator("substitute_controls")
    @classmethod
    def substitutes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _names(value, "substitute control")

    @model_validator(mode="after")
    def coherent(self) -> TravelCostColumns:
        _unique(
            (
                self.unit,
                self.visits,
                self.travel_cost,
                self.exposure,
                self.site,
                *self.substitute_controls,
            )
        )
        return self

    def required(self) -> tuple[str, ...]:
        return (
            self.unit,
            self.visits,
            self.travel_cost,
            self.exposure,
            self.site,
            *self.substitute_controls,
        )


class ContingentValuationColumns(BaseModel):
    model_config = STRICT_FROZEN
    respondent: str
    response: str
    bid: str
    covariates: tuple[str, ...] = ()

    @field_validator("respondent", "response", "bid")
    @classmethod
    def primary(cls, value: str) -> str:
        return _canonical_name(value, "column")

    @field_validator("covariates")
    @classmethod
    def covariate_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _names(value, "covariate")

    @model_validator(mode="after")
    def coherent(self) -> ContingentValuationColumns:
        _unique((self.respondent, self.response, self.bid, *self.covariates))
        return self

    def required(self) -> tuple[str, ...]:
        return (self.respondent, self.response, self.bid, *self.covariates)


class DiscreteChoiceColumns(BaseModel):
    model_config = STRICT_FROZEN
    respondent: str
    choice_set: str
    alternative: str
    chosen: str
    cost: str
    attributes: tuple[str, ...] = Field(min_length=1)

    @field_validator("respondent", "choice_set", "alternative", "chosen", "cost")
    @classmethod
    def primary(cls, value: str) -> str:
        return _canonical_name(value, "column")

    @field_validator("attributes")
    @classmethod
    def attribute_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _names(value, "attribute")

    @model_validator(mode="after")
    def coherent(self) -> DiscreteChoiceColumns:
        _unique(
            (
                self.respondent,
                self.choice_set,
                self.alternative,
                self.chosen,
                self.cost,
                *self.attributes,
            )
        )
        return self

    def required(self) -> tuple[str, ...]:
        return (
            self.respondent,
            self.choice_set,
            self.alternative,
            self.chosen,
            self.cost,
            *self.attributes,
        )


class _ValuationSpec(BaseModel):
    model_config = STRICT_FROZEN
    data_path: Path
    budget: ResourceBudget
    currency: str
    price_base: str
    time_basis: str
    population_basis: str

    @field_validator("data_path", mode="before")
    @classmethod
    def parse_path(cls, value: object, info: ValidationInfo) -> object:
        if isinstance(value, str):
            if not value or value != value.strip() or "\x00" in value:
                raise ValueError("data path must be canonical")
            return value if info.mode == "json" else Path(value)
        return value

    @field_validator("data_path")
    @classmethod
    def absolute_csv(cls, value: Path) -> Path:
        if not value.is_absolute() or value.suffix.lower() != ".csv":
            raise ValueError("data path must be an absolute CSV path")
        return value

    @field_validator("currency", "price_base", "time_basis", "population_basis")
    @classmethod
    def welfare_unit(cls, value: str, info: ValidationInfo) -> str:
        return _canonical_name(
            value, (info.field_name or "welfare unit").replace("_", " ")
        )

    @field_validator("confidence_level", check_fields=False)
    @classmethod
    def finite_confidence(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("confidence level must be finite")
        return value

    @field_validator(
        "max_condition_number",
        "max_sensitivity_change",
        "max_dispersion",
        "max_extreme_probability_share",
        "min_abs_cost_coefficient",
        check_fields=False,
    )
    @classmethod
    def finite_threshold(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("valuation thresholds must be finite")
        return value

    def required_columns(self) -> tuple[str, ...]:
        raise NotImplementedError


class HedonicSpec(_ValuationSpec):
    schema_version: Literal["econometrics.hedonic-pricing.v1"]
    method_id: Literal["hedonic-pricing"]
    columns: HedonicColumns
    confidence_level: float = Field(gt=0.0, lt=1.0)
    cluster_column: str | None = None
    functional_form: HedonicForm
    sensitivity_form: HedonicForm
    max_condition_number: float = Field(gt=1.0)
    max_sensitivity_change: float = Field(gt=0.0)

    @field_validator("cluster_column")
    @classmethod
    def cluster(cls, value: str | None) -> str | None:
        return None if value is None else _canonical_name(value, "cluster column")

    @model_validator(mode="after")
    def known_cluster(self) -> HedonicSpec:
        if (
            self.cluster_column is not None
            and self.cluster_column not in self.columns.required()
        ):
            raise ValueError("cluster column must be a declared source role")
        if self.sensitivity_form == self.functional_form:
            raise ValueError("hedonic sensitivity form must differ from primary form")
        return self

    def required_columns(self) -> tuple[str, ...]:
        return self.columns.required()


class TravelCostSpec(_ValuationSpec):
    schema_version: Literal["econometrics.travel-cost.v1"]
    method_id: Literal["travel-cost"]
    columns: TravelCostColumns
    confidence_level: float = Field(gt=0.0, lt=1.0)
    cluster_column: str | None = None
    family: CountFamily
    sensitivity: Literal["exclude-substitute-controls"]
    max_dispersion: float = Field(gt=0.0)
    max_sensitivity_change: float = Field(gt=0.0)

    @field_validator("cluster_column")
    @classmethod
    def cluster(cls, value: str | None) -> str | None:
        return None if value is None else _canonical_name(value, "cluster column")

    @model_validator(mode="after")
    def known_cluster(self) -> TravelCostSpec:
        if (
            self.cluster_column is not None
            and self.cluster_column not in self.columns.required()
        ):
            raise ValueError("cluster column must be a declared source role")
        if self.family == "negative-binomial" and self.cluster_column is not None:
            raise ValueError(
                "negative-binomial travel-cost does not support cluster inference"
            )
        return self

    def required_columns(self) -> tuple[str, ...]:
        return self.columns.required()


class ContingentValuationSpec(_ValuationSpec):
    schema_version: Literal["econometrics.contingent-valuation.v1"]
    method_id: Literal["contingent-valuation"]
    columns: ContingentValuationColumns
    confidence_level: float = Field(gt=0.0, lt=1.0)
    link: BinaryLink
    sensitivity: Literal["exclude-covariates"]
    max_extreme_probability_share: float = Field(gt=0.0, lt=1.0)
    max_sensitivity_change: float = Field(gt=0.0)

    def required_columns(self) -> tuple[str, ...]:
        return self.columns.required()


class DiscreteChoiceSpec(_ValuationSpec):
    schema_version: Literal["econometrics.dce-clogit.v1"]
    method_id: Literal["dce-clogit"]
    columns: DiscreteChoiceColumns
    confidence_level: float = Field(gt=0.0, lt=1.0)
    cluster_column: str
    sensitivity: Literal["include-alternative-specific-constants"]
    min_abs_cost_coefficient: float = Field(gt=0.0)
    max_sensitivity_change: float = Field(gt=0.0)

    @field_validator("cluster_column")
    @classmethod
    def cluster(cls, value: str) -> str:
        return _canonical_name(value, "cluster column")

    @model_validator(mode="after")
    def respondent_cluster(self) -> DiscreteChoiceSpec:
        if self.cluster_column != self.columns.respondent:
            raise ValueError("DCE cluster column must equal respondent column")
        return self

    def required_columns(self) -> tuple[str, ...]:
        return self.columns.required()
