"""Typed result evidence for the remaining Wave-1 method families."""

from __future__ import annotations

import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from envresearch.econometrics._wave1_evidence_models import (
    MeasurementQuantiles,
    MonitorCoverage,
    RctBalance,
    TemporalMean,
)
from envresearch.econometrics._wave1_package import WavePackageConfiguration
from envresearch.econometrics._wave1_synthesis_models import (
    DonorWeight,
    FunnelPoint,
    LeaveOneOutEffect,
    MetaAnalysisResult,
    PlaceboEffect,
    StudyEvidence,
    StudyWeight,
    SyntheticControlResult,
    SyntheticGap,
)
from envresearch.econometrics.causal_models import RegressionCoefficient
from envresearch.econometrics.contracts import STRICT_FROZEN

SHA256 = re.compile(r"^[0-9a-f]{64}$")
Digest = Annotated[str, Field(pattern=SHA256.pattern)]


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("result values must be finite")
    return value


class RctSupport(BaseModel):
    model_config = STRICT_FROZEN
    total: int = Field(gt=0)
    assigned_control: int = Field(gt=0)
    assigned_treated: int = Field(gt=0)
    control_outcomes_observed: int = Field(gt=0)
    treated_outcomes_observed: int = Field(gt=0)
    outcomes_observed: int = Field(gt=0)
    outcomes_missing: int = Field(ge=0)

    @model_validator(mode="after")
    def reconcile(self) -> RctSupport:
        if (
            self.assigned_control + self.assigned_treated != self.total
            or (self.outcomes_observed + self.outcomes_missing != self.total)
            or (
                self.control_outcomes_observed + self.treated_outcomes_observed
                != self.outcomes_observed
            )
            or (
                self.control_outcomes_observed > self.assigned_control
                or self.treated_outcomes_observed > self.assigned_treated
            )
        ):
            raise ValueError("RCT support counts do not reconcile")
        return self


class RctResult(BaseModel):
    model_config = STRICT_FROZEN
    method_id: Literal["rct-itt"]
    unadjusted: RegressionCoefficient
    ancova: RegressionCoefficient
    support: RctSupport
    balance: tuple[RctBalance, ...] = Field(min_length=1)
    attrition_rate: float = Field(ge=0.0, le=1.0)
    max_attrition_rate: float = Field(ge=0.0, lt=1.0)
    max_abs_balance_smd: float = Field(ge=0.0)
    balance_smd_threshold: float = Field(gt=0.0)
    configuration: WavePackageConfiguration
    figure_sha256: Digest

    @field_validator(
        "attrition_rate",
        "max_attrition_rate",
        "max_abs_balance_smd",
        "balance_smd_threshold",
    )
    @classmethod
    def finite_diagnostic(cls, value: float) -> float:
        return _finite(value)

    @model_validator(mode="after")
    def diagnostics_pass(self) -> RctResult:
        expected_attrition = self.support.outcomes_missing / self.support.total
        if not math.isclose(self.attrition_rate, expected_attrition, abs_tol=1e-12):
            raise ValueError("RCT attrition does not match support")
        if len({item.term for item in self.balance}) != len(self.balance):
            raise ValueError("RCT balance terms must be unique")
        observed_balance = max(abs(item.smd) for item in self.balance)
        if not math.isclose(self.max_abs_balance_smd, observed_balance, abs_tol=1e-12):
            raise ValueError("RCT balance maximum is inconsistent")
        if self.attrition_rate > self.max_attrition_rate:
            raise ValueError("RCT attrition exceeds its declared threshold")
        if self.max_abs_balance_smd > self.balance_smd_threshold:
            raise ValueError("RCT balance exceeds its declared threshold")
        if self.configuration.method_id != self.method_id:
            raise ValueError("result configuration selects another method")
        return self


class MeasurementSupport(BaseModel):
    model_config = STRICT_FROZEN
    total: int = Field(gt=0)
    valid: int = Field(ge=0)
    missing: int = Field(ge=0)
    monitors: int = Field(gt=0)

    @model_validator(mode="after")
    def reconcile(self) -> MeasurementSupport:
        if self.valid + self.missing != self.total:
            raise ValueError("measurement support counts do not reconcile")
        if self.monitors > self.total:
            raise ValueError("measurement monitor count exceeds rows")
        return self


class EnvironmentalMeasurementResult(BaseModel):
    model_config = STRICT_FROZEN
    method_id: Literal["environmental-measurement"]
    support: MeasurementSupport
    quantiles: MeasurementQuantiles
    temporal: tuple[TemporalMean, ...] = Field(min_length=1)
    monitor_coverage: tuple[MonitorCoverage, ...] = Field(min_length=1)
    mean: float
    minimum: float
    maximum: float
    exceedances: int = Field(ge=0)
    exceedance_threshold: float
    missing_rate: float = Field(ge=0.0, le=1.0)
    max_missing_rate: float = Field(ge=0.0, lt=1.0)
    declared_unit: str
    configuration: WavePackageConfiguration
    figure_sha256: Digest

    @field_validator("mean", "minimum", "maximum", "exceedance_threshold")
    @classmethod
    def finite(cls, value: float) -> float:
        return _finite(value)

    @model_validator(mode="after")
    def coherent(self) -> EnvironmentalMeasurementResult:
        if not self.minimum <= self.mean <= self.maximum:
            raise ValueError("measurement summary is incoherent")
        if self.missing_rate > self.max_missing_rate:
            raise ValueError("measurement missingness exceeds its threshold")
        if not math.isclose(
            self.missing_rate,
            self.support.missing / self.support.total,
            abs_tol=1e-12,
        ):
            raise ValueError("measurement missingness does not match support")
        if self.exceedances > self.support.valid:
            raise ValueError("measurement exceedances exceed valid observations")
        if len({item.date for item in self.temporal}) != len(self.temporal):
            raise ValueError("measurement temporal dates must be unique")
        if len({item.monitor for item in self.monitor_coverage}) != len(
            self.monitor_coverage
        ):
            raise ValueError("measurement monitor coverage must be unique")
        coverage = tuple(
            sum(getattr(item, name) for item in self.monitor_coverage)
            for name in ("total", "valid", "missing")
        )
        if coverage != (self.support.total, self.support.valid, self.support.missing):
            raise ValueError("measurement coverage does not match support")
        if self.configuration.method_id != self.method_id:
            raise ValueError("result configuration selects another method")
        return self


MeasurementResult = EnvironmentalMeasurementResult

__all__ = [
    "DonorWeight",
    "EnvironmentalMeasurementResult",
    "FunnelPoint",
    "LeaveOneOutEffect",
    "MeasurementResult",
    "MeasurementSupport",
    "MetaAnalysisResult",
    "PlaceboEffect",
    "RctResult",
    "RctSupport",
    "StudyEvidence",
    "StudyWeight",
    "SyntheticControlResult",
    "SyntheticGap",
    "WavePackageConfiguration",
]
