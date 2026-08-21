"""Strict authorities for Panel FE, IV/2SLS, and local-linear RDD."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from envresearch.econometrics.contracts import (
    STRICT_FROZEN,
    ResourceBudget,
    _canonical_name,
)


def _canonical_tuple(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    canonical = tuple(_canonical_name(value, label) for value in values)
    if len(set(canonical)) != len(canonical):
        raise ValueError(f"{label} columns must be unique")
    return canonical


def _ordered_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


class RegressionInferenceSpec(BaseModel):
    """Predeclared inference shared by the causal-policy recipes."""

    model_config = STRICT_FROZEN

    confidence_level: float = Field(gt=0.0, lt=1.0)
    cluster_column: str | None = None

    @field_validator("cluster_column")
    @classmethod
    def require_cluster_name(cls, value: str | None) -> str | None:
        return None if value is None else _canonical_name(value, "cluster column")


class PanelFeColumns(BaseModel):
    """Explicit panel roles for a fixed-effects regression."""

    model_config = STRICT_FROZEN

    unit: str
    time: str
    outcome: str
    regressors: tuple[str, ...] = Field(min_length=1)
    fixed_effects: tuple[str, ...] = Field(min_length=1)

    @field_validator("unit", "time", "outcome")
    @classmethod
    def require_primary_name(cls, value: str) -> str:
        return _canonical_name(value, "column")

    @field_validator("regressors", "fixed_effects")
    @classmethod
    def require_column_tuple(
        cls, value: tuple[str, ...], info: ValidationInfo
    ) -> tuple[str, ...]:
        label = (info.field_name or "column").replace("_", " ")
        return _canonical_tuple(value, label)

    @model_validator(mode="after")
    def require_coherent_roles(self) -> PanelFeColumns:
        primary = (self.unit, self.time, self.outcome, *self.regressors)
        if len(set(primary)) != len(primary):
            raise ValueError("column roles must be unique")
        if self.outcome in self.fixed_effects or set(self.regressors) & set(
            self.fixed_effects
        ):
            raise ValueError("column roles must be unique")
        return self

    def required(self) -> tuple[str, ...]:
        return _ordered_unique(
            (self.unit, self.time, self.outcome, *self.regressors, *self.fixed_effects)
        )


class Iv2slsColumns(BaseModel):
    """Explicit structural, excluded-instrument, and adjustment roles."""

    model_config = STRICT_FROZEN

    outcome: str
    endogenous: tuple[str, ...] = Field(min_length=1)
    instruments: tuple[str, ...] = Field(min_length=1)
    controls: tuple[str, ...] = ()
    fixed_effects: tuple[str, ...] = ()

    @field_validator("outcome")
    @classmethod
    def require_outcome(cls, value: str) -> str:
        return _canonical_name(value, "outcome")

    @field_validator("endogenous", "instruments", "controls", "fixed_effects")
    @classmethod
    def require_column_tuple(
        cls, value: tuple[str, ...], info: ValidationInfo
    ) -> tuple[str, ...]:
        label = (info.field_name or "column").replace("_", " ")
        return _canonical_tuple(value, label)

    @model_validator(mode="after")
    def require_unique_roles(self) -> Iv2slsColumns:
        roles = (
            self.outcome,
            *self.endogenous,
            *self.instruments,
            *self.controls,
            *self.fixed_effects,
        )
        if len(set(roles)) != len(roles):
            raise ValueError("column roles must be unique")
        if len(self.instruments) < len(self.endogenous):
            raise ValueError("excluded instruments must identify every endogenous role")
        return self

    def required(self) -> tuple[str, ...]:
        return (
            self.outcome,
            *self.endogenous,
            *self.instruments,
            *self.controls,
            *self.fixed_effects,
        )


class RddColumns(BaseModel):
    """Explicit outcome, running variable, and continuity roles."""

    model_config = STRICT_FROZEN

    outcome: str
    running: str
    covariates: tuple[str, ...] = ()

    @field_validator("outcome", "running")
    @classmethod
    def require_primary_name(cls, value: str) -> str:
        return _canonical_name(value, "column")

    @field_validator("covariates")
    @classmethod
    def require_covariates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_tuple(value, "covariate")

    @model_validator(mode="after")
    def require_unique_roles(self) -> RddColumns:
        roles = (self.outcome, self.running, *self.covariates)
        if len(set(roles)) != len(roles):
            raise ValueError("column roles must be unique")
        return self

    def required(self) -> tuple[str, ...]:
        return (self.outcome, self.running, *self.covariates)


class RddDesign(BaseModel):
    """Frozen sharp local-linear design choices."""

    model_config = STRICT_FROZEN

    cutoff: float
    bandwidth: float = Field(gt=0.0)
    donut_radius: float = Field(ge=0.0)
    kernel: Literal["triangular"]

    @model_validator(mode="after")
    def require_internal_donut(self) -> RddDesign:
        if self.donut_radius >= self.bandwidth:
            raise ValueError("donut radius must be smaller than the bandwidth")
        return self


class _CausalSpec(BaseModel):
    model_config = STRICT_FROZEN

    data_path: Path
    inference: RegressionInferenceSpec
    budget: ResourceBudget

    @field_validator("data_path", mode="before")
    @classmethod
    def parse_serialized_path(cls, value: object, info: ValidationInfo) -> object:
        if isinstance(value, str):
            if not value or value != value.strip() or "\x00" in value:
                raise ValueError("data path must be a canonical nonblank path")
            return value if info.mode == "json" else Path(value)
        return value

    @field_validator("data_path")
    @classmethod
    def require_absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("data path must be absolute")
        return value

    def required_columns(self) -> tuple[str, ...]:
        raise NotImplementedError


class PanelFeSpec(_CausalSpec):
    """Complete execution authority for a fixed-effects panel model."""

    schema_version: Literal["econometrics.panel-fe.v1"]
    method_id: Literal["panel-fe"]
    columns: PanelFeColumns

    def required_columns(self) -> tuple[str, ...]:
        cluster = (
            ()
            if self.inference.cluster_column is None
            else (self.inference.cluster_column,)
        )
        return _ordered_unique((*self.columns.required(), *cluster))


class Iv2slsSpec(_CausalSpec):
    """Complete execution authority for an IV/2SLS model."""

    schema_version: Literal["econometrics.iv-2sls.v1"]
    method_id: Literal["iv-2sls"]
    columns: Iv2slsColumns
    weak_instrument_f_threshold: float = Field(gt=0.0)

    def required_columns(self) -> tuple[str, ...]:
        cluster = (
            ()
            if self.inference.cluster_column is None
            else (self.inference.cluster_column,)
        )
        return _ordered_unique((*self.columns.required(), *cluster))


class RddSpec(_CausalSpec):
    """Complete execution authority for a sharp local-linear RDD."""

    schema_version: Literal["econometrics.rdd-local-linear.v1"]
    method_id: Literal["rdd-local-linear"]
    columns: RddColumns
    design: RddDesign

    def required_columns(self) -> tuple[str, ...]:
        cluster = (
            ()
            if self.inference.cluster_column is None
            else (self.inference.cluster_column,)
        )
        return _ordered_unique((*self.columns.required(), *cluster))
