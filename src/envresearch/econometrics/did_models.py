"""Strict scientific outputs for the local DiD/event-study recipe."""

from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

STRICT_FROZEN = ConfigDict(
    extra="forbid", frozen=True, strict=True, validate_default=True
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class EstimateRow(BaseModel):
    """One finite estimate with an internally coherent confidence interval."""

    model_config = STRICT_FROZEN

    term: str
    event_time: int | None
    group: int | None
    time: int | None
    estimate: float
    std_error: float = Field(ge=0.0)
    conf_low: float
    conf_high: float

    @field_validator("estimate", "std_error", "conf_low", "conf_high")
    @classmethod
    def require_finite(cls, value: float) -> float:
        """Reject NaN and infinite statistical values."""
        if not math.isfinite(value):
            raise ValueError("estimate values must be finite")
        return value

    @field_validator("term")
    @classmethod
    def require_term(cls, value: str) -> str:
        """Require one canonical estimator term."""
        if not value or value != value.strip():
            raise ValueError("estimate term must be canonical")
        return value

    @model_validator(mode="after")
    def require_interval(self) -> EstimateRow:
        """Require a confidence interval containing the point estimate."""
        if not self.conf_low <= self.estimate <= self.conf_high:
            raise ValueError("confidence interval must contain the estimate")
        return self


class EstimateTable(BaseModel):
    """One named nonempty estimator output."""

    model_config = STRICT_FROZEN

    estimator: str
    estimates: tuple[EstimateRow, ...]

    @field_validator("estimator")
    @classmethod
    def require_estimator(cls, value: str) -> str:
        """Require an explicit estimator identity."""
        if not value or value != value.strip():
            raise ValueError("estimator identity must be canonical")
        return value

    @field_validator("estimates")
    @classmethod
    def require_estimates(
        cls, value: tuple[EstimateRow, ...]
    ) -> tuple[EstimateRow, ...]:
        """Reject empty estimate tables."""
        if not value:
            raise ValueError("estimate table must not be empty")
        return value


class SupportDiagnostic(BaseModel):
    """Panel and comparison-group support used by both estimators."""

    model_config = STRICT_FROZEN

    observations: int = Field(gt=0)
    units: int = Field(gt=1)
    treated_units: int = Field(gt=0)
    comparison_units: int = Field(ge=0)
    cohorts: int = Field(gt=0)
    dropped_observations: int = Field(ge=0)
    duplicate_panel_keys: int = Field(ge=0)
    removal_rule: str

    @model_validator(mode="after")
    def require_support(self) -> SupportDiagnostic:
        """Require both treatment and declared comparison support."""
        if self.comparison_units == 0:
            raise ValueError("comparison support must be nonzero")
        if self.dropped_observations:
            raise ValueError("dropped observations were not declared by the recipe")
        if self.duplicate_panel_keys:
            raise ValueError("duplicate panel keys are not allowed")
        if self.removal_rule != "complete-declared-columns":
            raise ValueError("removal rule is not the declared recipe rule")
        return self


class SupportCell(BaseModel):
    """Effective treated/control support for one cohort-time ATT cell."""

    model_config = STRICT_FROZEN

    group: int
    time: int
    event_time: int
    treated_observations: int = Field(gt=0)
    comparison_observations: int = Field(gt=0)
    treated_units: int = Field(gt=0)
    comparison_units: int = Field(gt=0)

    @model_validator(mode="after")
    def require_event_key(self) -> SupportCell:
        """Bind the event-time key to group and calendar time."""
        if self.event_time != self.time - self.group:
            raise ValueError("support-cell event key is inconsistent")
        return self


class CohortTiming(BaseModel):
    """Treatment timing distribution for one treated cohort."""

    model_config = STRICT_FROZEN

    cohort: int
    units: int = Field(gt=0)
    first_period: int
    last_period: int


class CovariateBalance(BaseModel):
    """Simple predeclared overlap diagnostic for one covariate."""

    model_config = STRICT_FROZEN

    covariate: str
    treated_mean: float
    comparison_mean: float
    standardized_difference: float
    treated_n: int = Field(gt=0)
    comparison_n: int = Field(gt=0)

    @field_validator("treated_mean", "comparison_mean", "standardized_difference")
    @classmethod
    def require_finite_balance(cls, value: float) -> float:
        """Reject non-finite balance statistics."""
        if not math.isfinite(value):
            raise ValueError("covariate balance values must be finite")
        return value


class PackageConfiguration(BaseModel):
    """Exact estimator and inference configuration emitted by R."""

    model_config = STRICT_FROZEN

    r_version: str
    fixest_version: str
    did_version: str
    bootstrap_seed: int = Field(ge=0)
    comparison_group: Literal["never-treated", "not-yet-treated"]
    reference_period: int
    base_period: Literal["varying"]
    anticipation: int = Field(ge=0)
    confidence_level: float = Field(gt=0.0, lt=1.0)
    interval_mode: Literal["pointwise", "simultaneous"]
    baseline_interval_method: Literal["pointwise-normal", "bonferroni-normal"]
    did_interval_method: Literal["pointwise-normal", "multiplier-bootstrap"]
    cluster_column: str

    @field_validator(
        "r_version",
        "fixest_version",
        "did_version",
        "comparison_group",
        "base_period",
        "interval_mode",
        "baseline_interval_method",
        "did_interval_method",
        "cluster_column",
    )
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        """Reject missing or padded configuration strings."""
        if not value or value != value.strip():
            raise ValueError("package configuration values must be canonical")
        return value


class DidResult(BaseModel):
    """Complete useful DiD/event-study result, not a success boolean."""

    model_config = STRICT_FROZEN

    baseline: EstimateTable
    group_time_att: EstimateTable
    dynamic: EstimateTable
    support: SupportDiagnostic
    support_cells: tuple[SupportCell, ...]
    cohort_timing: tuple[CohortTiming, ...]
    covariate_balance: tuple[CovariateBalance, ...]
    packages: PackageConfiguration
    figure_sha256: str

    @model_validator(mode="after")
    def require_unique_event_times(self) -> DidResult:
        """Reject ambiguous event-time rows in event-study tables."""
        for table in (self.baseline, self.dynamic):
            times = tuple(row.event_time for row in table.estimates)
            if None in times or len(set(times)) != len(times):
                raise ValueError("duplicate event time or missing event-time key")
        group_keys: list[tuple[int, int]] = []
        for row in self.group_time_att.estimates:
            if row.group is None or row.time is None:
                raise ValueError("group-time estimates require panel keys")
            if row.event_time != row.time - row.group:
                raise ValueError("group-time event key is inconsistent")
            group_keys.append((row.group, row.time))
        if len(set(group_keys)) != len(group_keys):
            raise ValueError("group-time panel keys must be unique")
        support_keys = tuple((row.group, row.time) for row in self.support_cells)
        if len(set(support_keys)) != len(support_keys):
            raise ValueError("support-cell panel keys must be unique")
        if set(support_keys) != set(group_keys):
            raise ValueError("support cells must match group-time estimate keys")
        cohort_keys = tuple(row.cohort for row in self.cohort_timing)
        if len(set(cohort_keys)) != len(cohort_keys):
            raise ValueError("cohort timing keys must be unique")
        estimate_cohorts = {group for group, _ in group_keys}
        if set(cohort_keys) != estimate_cohorts or self.support.cohorts != len(
            cohort_keys
        ):
            raise ValueError("cohort timing must match estimate and aggregate support")
        if not self.support_cells or not self.cohort_timing:
            raise ValueError("cohort and event-time diagnostics must not be empty")
        if not SHA256.fullmatch(self.figure_sha256):
            raise ValueError("event-study figure SHA-256 is invalid")
        return self

    def all_estimates(self) -> tuple[EstimateRow, ...]:
        """Return every parsed estimate in deterministic table order."""
        return (
            *self.baseline.estimates,
            *self.group_time_att.estimates,
            *self.dynamic.estimates,
        )
