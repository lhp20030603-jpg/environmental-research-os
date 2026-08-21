"""Strict method-neutral contracts for trusted local econometrics."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

STRICT_FROZEN = ConfigDict(
    extra="forbid", frozen=True, strict=True, validate_default=True
)


def _canonical_name(value: str, field_name: str) -> str:
    """Require one nonblank, unpadded column-name spelling."""
    if (
        not value
        or value != value.strip()
        or any(character in value for character in ("`", "\x00", "\r", "\n"))
    ):
        raise ValueError(f"{field_name} must be a canonical nonblank name")
    return value


class ColumnMapping(BaseModel):
    """Explicit semantic roles for one panel dataset."""

    model_config = STRICT_FROZEN

    unit: str
    time: str
    outcome: str
    treatment_cohort: str
    covariates: tuple[str, ...] = ()

    @field_validator("unit", "time", "outcome", "treatment_cohort")
    @classmethod
    def require_primary_name(cls, value: str) -> str:
        """Reject ambiguous blank or whitespace-padded primary columns."""
        return _canonical_name(value, "column")

    @field_validator("covariates")
    @classmethod
    def require_covariate_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require unique, canonical covariate names."""
        canonical = tuple(_canonical_name(item, "covariate") for item in value)
        if len(set(canonical)) != len(canonical):
            raise ValueError("covariate columns must be unique")
        return canonical

    @model_validator(mode="after")
    def require_unique_roles(self) -> ColumnMapping:
        """Prevent one column from carrying conflicting semantic roles."""
        roles = (
            self.unit,
            self.time,
            self.outcome,
            self.treatment_cohort,
            *self.covariates,
        )
        if len(set(roles)) != len(roles):
            raise ValueError("column roles must be unique")
        return self

    def required(self) -> tuple[str, ...]:
        """Return every declared source column in deterministic order."""
        return (
            self.unit,
            self.time,
            self.outcome,
            self.treatment_cohort,
            *self.covariates,
        )


class InferenceSpec(BaseModel):
    """Predeclared inference configuration for a local analysis."""

    model_config = STRICT_FROZEN

    confidence_level: float = Field(gt=0.0, lt=1.0)
    cluster_column: str
    interval_mode: Literal["pointwise", "simultaneous"]
    bootstrap_seed: int = Field(ge=0)

    @field_validator("cluster_column")
    @classmethod
    def require_cluster_name(cls, value: str) -> str:
        """Require an explicit canonical clustering column."""
        return _canonical_name(value, "cluster column")


class ResourceBudget(BaseModel):
    """Hard local execution and evidence-size ceilings."""

    model_config = STRICT_FROZEN

    inactivity_seconds: int = Field(gt=0)
    max_output_bytes: int = Field(gt=0)
    max_workspace_bytes: int = Field(gt=0)


class LocalAnalysisSpec(BaseModel):
    """Complete executable authority for one trusted local analysis."""

    model_config = STRICT_FROZEN

    schema_version: Literal["econometrics.local-analysis.v1"]
    method_id: Literal["did-event-study"]
    data_path: Path
    columns: ColumnMapping
    comparison_group: Literal["never-treated", "not-yet-treated"]
    reference_period: int
    max_pretrend_abs: float = Field(default=1.0, gt=0.0)
    inference: InferenceSpec
    budget: ResourceBudget

    @field_validator("data_path", mode="before")
    @classmethod
    def parse_serialized_data_path(cls, value: object, info: ValidationInfo) -> object:
        """Accept one explicit YAML path string without relaxing other fields."""
        if isinstance(value, str):
            if not value or value != value.strip() or "\x00" in value:
                raise ValueError("data path must be a canonical nonblank path")
            return value if info.mode == "json" else Path(value)
        return value

    @field_validator("data_path")
    @classmethod
    def require_absolute_data_path(cls, value: Path) -> Path:
        """Remove current-working-directory ambiguity from durable authority."""
        if not value.is_absolute():
            raise ValueError("data path must be absolute")
        return value

    @field_validator("max_pretrend_abs")
    @classmethod
    def require_finite_pretrend_threshold(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("pre-trend threshold must be finite")
        return value

    @model_validator(mode="after")
    def require_unit_clustering(self) -> LocalAnalysisSpec:
        """V0.3-A clusters at the declared panel-unit level only."""
        if self.inference.cluster_column != self.columns.unit:
            raise ValueError("cluster column must equal the panel unit column")
        return self

    def required_columns(self) -> tuple[str, ...]:
        """Return every source role used by the approved DiD analysis."""
        return self.columns.required()
