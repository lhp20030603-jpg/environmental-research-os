"""Strict result evidence for the shared causal-policy recipes."""

from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from envresearch.econometrics.contracts import STRICT_FROZEN

SHA256 = re.compile(r"^[0-9a-f]{64}$")
MethodId = Literal["panel-fe", "iv-2sls", "rdd-local-linear"]


def _finite(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


class RegressionCoefficient(BaseModel):
    """One finite coefficient with its declared confidence interval."""

    model_config = STRICT_FROZEN

    term: str
    estimate: float
    std_error: float = Field(ge=0.0)
    conf_low: float
    conf_high: float

    @field_validator("term")
    @classmethod
    def require_term(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("coefficient term must be canonical")
        return value

    @field_validator("estimate", "std_error", "conf_low", "conf_high")
    @classmethod
    def require_finite(cls, value: float) -> float:
        return _finite(value, "coefficient value")

    @model_validator(mode="after")
    def require_ordered_interval(self) -> RegressionCoefficient:
        if self.conf_low > self.estimate or self.estimate > self.conf_high:
            raise ValueError("confidence interval must contain the estimate")
        return self


class RegressionSupport(BaseModel):
    """Minimal sample and cluster support for a regression result."""

    model_config = STRICT_FROZEN

    observations: int = Field(gt=0)
    clusters: int | None = Field(default=None, gt=0)
    units: int | None = Field(default=None, gt=0)
    time_periods: int | None = Field(default=None, gt=0)


class FitDiagnostics(BaseModel):
    """Finite fit evidence emitted by a fixed-effects estimator."""

    model_config = STRICT_FROZEN

    r_squared: float
    within_r_squared: float

    @field_validator("r_squared", "within_r_squared")
    @classmethod
    def require_finite(cls, value: float) -> float:
        return _finite(value, "fit statistic")


class CausalPackageConfiguration(BaseModel):
    """Exact local package and inference configuration emitted by R."""

    model_config = STRICT_FROZEN

    method_id: MethodId
    r_version: str
    fixest_version: str
    confidence_level: float = Field(gt=0.0, lt=1.0)
    cluster_column: str | None
    fixed_effects: tuple[str, ...]
    estimator_label: str
    cutoff: float | None
    bandwidth: float | None
    kernel: Literal["triangular"] | None
    donut_radius: float | None

    @field_validator("r_version", "fixest_version", "estimator_label")
    @classmethod
    def require_version(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("package versions must be canonical")
        return value

    @field_validator("cluster_column")
    @classmethod
    def require_cluster(cls, value: str | None) -> str | None:
        if value is not None and (not value or value != value.strip()):
            raise ValueError("cluster column must be canonical")
        return value

    @field_validator("fixed_effects")
    @classmethod
    def require_fixed_effects(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(
            not item or item != item.strip() for item in value
        ):
            raise ValueError("fixed-effect names must be canonical and unique")
        return value

    @field_validator("cutoff", "bandwidth", "donut_radius")
    @classmethod
    def require_finite_design(cls, value: float | None) -> float | None:
        return None if value is None else _finite(value, "design value")

    @model_validator(mode="after")
    def require_method_configuration(self) -> CausalPackageConfiguration:
        design = (self.cutoff, self.bandwidth, self.kernel, self.donut_radius)
        if self.method_id == "rdd-local-linear":
            if (
                self.fixed_effects
                or self.estimator_label != "sharp-local-linear"
                or any(value is None for value in design)
                or self.bandwidth is None
                or self.bandwidth <= 0
                or self.donut_radius is None
                or self.donut_radius < 0
            ):
                raise ValueError("RDD configuration is incomplete")
        elif any(value is not None for value in design):
            raise ValueError("non-RDD configuration contains an RDD design")
        return self


class PanelFeResult(BaseModel):
    """Complete typed output for one declared fixed-effects model."""

    model_config = STRICT_FROZEN

    method_id: Literal["panel-fe"]
    coefficients: tuple[RegressionCoefficient, ...] = Field(min_length=1)
    support: RegressionSupport
    fit: FitDiagnostics
    configuration: CausalPackageConfiguration
    figure_sha256: str

    @field_validator("figure_sha256")
    @classmethod
    def require_figure_digest(cls, value: str) -> str:
        if not SHA256.fullmatch(value):
            raise ValueError("figure digest must be canonical")
        return value

    @model_validator(mode="after")
    def require_configuration_method(self) -> PanelFeResult:
        if self.support.units is None or self.support.time_periods is None:
            raise ValueError("Panel FE result requires unit and time support")
        if self.configuration.method_id != self.method_id:
            raise ValueError("result configuration selects another method")
        return self


class FirstStageDiagnostic(BaseModel):
    """Predeclared weak-instrument evidence for one endogenous variable."""

    model_config = STRICT_FROZEN

    endogenous: str
    instruments: tuple[str, ...] = Field(min_length=1)
    f_statistic: float = Field(ge=0.0)
    threshold: float = Field(gt=0.0)

    @field_validator("endogenous")
    @classmethod
    def require_endogenous(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("endogenous term must be canonical")
        return value

    @field_validator("instruments")
    @classmethod
    def require_instruments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or item != item.strip() for item in value) or len(
            set(value)
        ) != len(value):
            raise ValueError("instrument names must be canonical and unique")
        return value

    @field_validator("f_statistic", "threshold")
    @classmethod
    def require_finite(cls, value: float) -> float:
        return _finite(value, "first-stage evidence")

    @model_validator(mode="after")
    def require_strong_instrument(self) -> FirstStageDiagnostic:
        if self.f_statistic < self.threshold:
            raise ValueError("weak instrument evidence blocks a green IV result")
        return self


class OveridentificationDiagnostic(BaseModel):
    """Sargan evidence emitted only for an overidentified IV design."""

    model_config = STRICT_FROZEN

    test: Literal["Sargan"]
    statistic: float = Field(ge=0.0)
    p_value: float = Field(ge=0.0, le=1.0)
    degrees_of_freedom: int = Field(gt=0)

    @field_validator("statistic", "p_value")
    @classmethod
    def require_finite(cls, value: float) -> float:
        return _finite(value, "overidentification evidence")


class Iv2slsResult(BaseModel):
    """Complete typed output for one declared IV/2SLS model."""

    model_config = STRICT_FROZEN

    method_id: Literal["iv-2sls"]
    structural: tuple[RegressionCoefficient, ...] = Field(min_length=1)
    first_stage: tuple[FirstStageDiagnostic, ...] = Field(min_length=1)
    reduced_form: tuple[RegressionCoefficient, ...] = Field(min_length=1)
    overidentification: OveridentificationDiagnostic | None
    support: RegressionSupport
    configuration: CausalPackageConfiguration
    figure_sha256: str

    @field_validator("figure_sha256")
    @classmethod
    def require_figure_digest(cls, value: str) -> str:
        if not SHA256.fullmatch(value):
            raise ValueError("figure digest must be canonical")
        return value

    @model_validator(mode="after")
    def require_complete_first_stages(self) -> Iv2slsResult:
        structural = {item.term for item in self.structural}
        first_stages = {item.endogenous for item in self.first_stage}
        if structural != first_stages:
            raise ValueError("first-stage coverage must match structural terms")
        if len(first_stages) != len(self.first_stage):
            raise ValueError("first-stage endogenous terms must be unique")
        if any(
            item.instruments != self.first_stage[0].instruments
            for item in self.first_stage
        ):
            raise ValueError("first-stage instrument sets must be identical")
        if self.configuration.method_id != self.method_id:
            raise ValueError("result configuration selects another method")
        instrument_count = len(self.first_stage[0].instruments)
        overidentified = instrument_count > len(self.first_stage)
        if overidentified != (self.overidentification is not None):
            raise ValueError("overidentification evidence does not match the IV design")
        return self


class BandwidthEstimate(BaseModel):
    """One local-linear cutoff estimate under a fixed bandwidth multiplier."""

    model_config = STRICT_FROZEN

    multiplier: float
    coefficient: RegressionCoefficient

    @field_validator("multiplier")
    @classmethod
    def require_multiplier(cls, value: float) -> float:
        value = _finite(value, "bandwidth multiplier")
        if value not in {0.5, 1.0, 1.5}:
            raise ValueError("bandwidth multiplier is not registered")
        return value


class RddSupport(BaseModel):
    """Two-sided main and donut support for a sharp local-linear RDD."""

    model_config = STRICT_FROZEN

    observations: int = Field(gt=0)
    left_observations: int = Field(gt=0)
    right_observations: int = Field(gt=0)
    left_unique_running: int = Field(ge=4)
    right_unique_running: int = Field(ge=4)
    donut_left_observations: int = Field(gt=0)
    donut_right_observations: int = Field(gt=0)

    @model_validator(mode="after")
    def require_coherent_windows(self) -> RddSupport:
        if self.left_observations + self.right_observations != self.observations:
            raise ValueError("RDD side support must sum to observations")
        if (
            self.donut_left_observations > self.left_observations
            or self.donut_right_observations > self.right_observations
        ):
            raise ValueError("RDD donut support cannot exceed main support")
        return self


class RddResult(BaseModel):
    """Complete typed output for the bounded sharp local-linear RDD."""

    model_config = STRICT_FROZEN

    method_id: Literal["rdd-local-linear"]
    main: RegressionCoefficient
    bandwidth_sensitivity: tuple[BandwidthEstimate, ...]
    donut: RegressionCoefficient
    covariate_continuity: tuple[RegressionCoefficient, ...]
    support: RddSupport
    configuration: CausalPackageConfiguration
    figure_sha256: str
    inference_limitation: Literal[
        "local-linear conventional inference; rdrobust RBC not included"
    ]

    @field_validator("figure_sha256")
    @classmethod
    def require_figure_digest(cls, value: str) -> str:
        if not SHA256.fullmatch(value):
            raise ValueError("figure digest must be canonical")
        return value

    @model_validator(mode="after")
    def require_registered_sensitivity(self) -> RddResult:
        multipliers = tuple(item.multiplier for item in self.bandwidth_sensitivity)
        if multipliers != (0.5, 1.0, 1.5):
            raise ValueError("RDD requires bandwidth multipliers 0.5, 1.0, and 1.5")
        if self.configuration.method_id != self.method_id:
            raise ValueError("result configuration selects another method")
        return self
