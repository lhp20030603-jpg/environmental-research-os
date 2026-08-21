"""Strict execution authorities for the remaining Wave-1 method families."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from envresearch.econometrics.causal_contracts import RegressionInferenceSpec
from envresearch.econometrics.contracts import (
    STRICT_FROZEN,
    ResourceBudget,
    _canonical_name,
)


def _names(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    result = tuple(_canonical_name(value, label) for value in values)
    if len(result) != len(set(result)):
        raise ValueError(f"{label} columns must be unique")
    return result


def _unique_roles(values: tuple[str, ...]) -> None:
    if len(values) != len(set(values)):
        raise ValueError("column roles must be unique")


class RctColumns(BaseModel):
    model_config = STRICT_FROZEN
    unit: str
    assignment: str
    outcome: str
    baseline_covariates: tuple[str, ...] = Field(min_length=1)

    @field_validator("unit", "assignment", "outcome")
    @classmethod
    def primary(cls, value: str) -> str:
        return _canonical_name(value, "column")

    @field_validator("baseline_covariates")
    @classmethod
    def covariates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _names(value, "baseline covariate")

    @model_validator(mode="after")
    def coherent(self) -> RctColumns:
        _unique_roles(
            (self.unit, self.assignment, self.outcome, *self.baseline_covariates)
        )
        return self

    def required(self) -> tuple[str, ...]:
        return (self.unit, self.assignment, self.outcome, *self.baseline_covariates)


class SyntheticControlColumns(BaseModel):
    model_config = STRICT_FROZEN
    unit: str
    time: str
    outcome: str
    predictors: tuple[str, ...] = ()

    @field_validator("unit", "time", "outcome")
    @classmethod
    def primary(cls, value: str) -> str:
        return _canonical_name(value, "column")

    @field_validator("predictors")
    @classmethod
    def predictor_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _names(value, "predictor")

    @model_validator(mode="after")
    def coherent(self) -> SyntheticControlColumns:
        _unique_roles((self.unit, self.time, self.outcome, *self.predictors))
        return self

    def required(self) -> tuple[str, ...]:
        return (self.unit, self.time, self.outcome, *self.predictors)


class MeasurementColumns(BaseModel):
    model_config = STRICT_FROZEN
    monitor: str
    timestamp: str
    value: str
    unit: str
    detection_flag: str | None = None

    @field_validator("monitor", "timestamp", "value", "unit", "detection_flag")
    @classmethod
    def names(cls, value: str | None) -> str | None:
        return None if value is None else _canonical_name(value, "column")

    @model_validator(mode="after")
    def coherent(self) -> MeasurementColumns:
        roles = (self.monitor, self.timestamp, self.value, self.unit)
        _unique_roles(
            roles + (() if self.detection_flag is None else (self.detection_flag,))
        )
        return self

    def required(self) -> tuple[str, ...]:
        return (self.monitor, self.timestamp, self.value, self.unit) + (
            () if self.detection_flag is None else (self.detection_flag,)
        )


class MetaAnalysisColumns(BaseModel):
    model_config = STRICT_FROZEN
    study: str
    effect: str
    variance: str

    @field_validator("study", "effect", "variance")
    @classmethod
    def names(cls, value: str) -> str:
        return _canonical_name(value, "column")

    @model_validator(mode="after")
    def coherent(self) -> MetaAnalysisColumns:
        _unique_roles((self.study, self.effect, self.variance))
        return self

    def required(self) -> tuple[str, ...]:
        return (self.study, self.effect, self.variance)


class _WaveSpec(BaseModel):
    model_config = STRICT_FROZEN
    data_path: Path
    budget: ResourceBudget

    @field_validator("data_path", mode="before")
    @classmethod
    def parse_path(cls, value: object, info: ValidationInfo) -> object:
        if isinstance(value, str):
            return value if info.mode == "json" else Path(value)
        return value

    @field_validator("data_path")
    @classmethod
    def absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("data path must be absolute")
        return value

    def required_columns(self) -> tuple[str, ...]:
        raise NotImplementedError


class RctSpec(_WaveSpec):
    schema_version: Literal["econometrics.rct-itt.v1"]
    method_id: Literal["rct-itt"]
    columns: RctColumns
    inference: RegressionInferenceSpec
    max_attrition_rate: float = Field(ge=0.0, lt=1.0)
    balance_smd_threshold: float = Field(gt=0.0)

    @field_validator("max_attrition_rate", "balance_smd_threshold")
    @classmethod
    def finite_threshold(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("RCT thresholds must be finite")
        return value

    @model_validator(mode="after")
    def individual_randomization_only(self) -> RctSpec:
        if self.inference.cluster_column is not None:
            raise ValueError("cluster-randomized RCT is not supported in V0.3")
        return self

    def required_columns(self) -> tuple[str, ...]:
        cluster = (
            ()
            if self.inference.cluster_column is None
            else (self.inference.cluster_column,)
        )
        return tuple(dict.fromkeys((*self.columns.required(), *cluster)))


class SyntheticControlSpec(_WaveSpec):
    schema_version: Literal["econometrics.synthetic-control.v1"]
    method_id: Literal["synthetic-control"]
    columns: SyntheticControlColumns
    treated_unit: str
    intervention_time: float
    max_pre_rmspe: float = Field(gt=0.0)
    max_leave_one_out_change: float = Field(gt=0.0)

    @field_validator("treated_unit")
    @classmethod
    def treated(cls, value: str) -> str:
        return _canonical_name(value, "treated unit")

    @field_validator("intervention_time")
    @classmethod
    def finite_time(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("intervention time must be finite")
        return value

    @field_validator("max_pre_rmspe", "max_leave_one_out_change")
    @classmethod
    def finite_threshold(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("synthetic-control thresholds must be finite")
        return value

    @model_validator(mode="after")
    def outcome_path_only(self) -> SyntheticControlSpec:
        if self.columns.predictors:
            raise ValueError(
                "predictor-adjusted synthetic control is not supported in V0.3"
            )
        return self

    def required_columns(self) -> tuple[str, ...]:
        return self.columns.required()


class EnvironmentalMeasurementSpec(_WaveSpec):
    schema_version: Literal["econometrics.environmental-measurement.v1"]
    method_id: Literal["environmental-measurement"]
    columns: MeasurementColumns
    declared_unit: str
    max_missing_rate: float = Field(ge=0.0, lt=1.0)
    valid_min: float
    valid_max: float
    exceedance_threshold: float

    @field_validator("declared_unit")
    @classmethod
    def unit(cls, value: str) -> str:
        return _canonical_name(value, "declared unit")

    @model_validator(mode="after")
    def coherent_range(self) -> EnvironmentalMeasurementSpec:
        values = (self.valid_min, self.valid_max, self.exceedance_threshold)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("measurement thresholds must be finite")
        if not self.valid_min < self.valid_max or not (
            self.valid_min <= self.exceedance_threshold <= self.valid_max
        ):
            raise ValueError("measurement range is incoherent")
        return self

    def required_columns(self) -> tuple[str, ...]:
        return self.columns.required()


class MetaAnalysisSpec(_WaveSpec):
    schema_version: Literal["econometrics.meta-analysis.v1"]
    method_id: Literal["meta-analysis"]
    columns: MetaAnalysisColumns
    confidence_level: float = Field(gt=0.0, lt=1.0)
    max_leave_one_out_change: float = Field(gt=0.0)
    model: Literal["fixed-and-dl-random"]

    @field_validator("confidence_level", "max_leave_one_out_change")
    @classmethod
    def finite_threshold(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("meta-analysis thresholds must be finite")
        return value

    def required_columns(self) -> tuple[str, ...]:
        return self.columns.required()
