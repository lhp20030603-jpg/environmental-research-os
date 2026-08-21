"""Strict result evidence for SCM and meta-analysis synthesis methods."""

from __future__ import annotations

import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from envresearch.econometrics._wave1_package import WavePackageConfiguration
from envresearch.econometrics.causal_models import RegressionCoefficient
from envresearch.econometrics.contracts import STRICT_FROZEN

SHA256 = re.compile(r"^[0-9a-f]{64}$")
Digest = Annotated[str, Field(pattern=SHA256.pattern)]


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("result values must be finite")
    return value


class DonorWeight(BaseModel):
    model_config = STRICT_FROZEN
    donor: str
    weight: float = Field(ge=0.0, le=1.0)

    @field_validator("weight")
    @classmethod
    def finite(cls, value: float) -> float:
        return _finite(value)

    @field_validator("donor")
    @classmethod
    def donor_name(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("donor name must be canonical")
        return value


class LeaveOneOutEffect(BaseModel):
    model_config = STRICT_FROZEN
    omitted: str
    effect: float
    absolute_change: float = Field(ge=0.0)

    @field_validator("omitted")
    @classmethod
    def omitted_name(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("omitted identity must be canonical")
        return value

    @field_validator("effect", "absolute_change")
    @classmethod
    def finite(cls, value: float) -> float:
        return _finite(value)


class SyntheticGap(BaseModel):
    model_config = STRICT_FROZEN
    time: float
    treated: float
    synthetic: float
    gap: float
    period: Literal["pre", "post"]

    @field_validator("time", "treated", "synthetic", "gap")
    @classmethod
    def finite(cls, value: float) -> float:
        return _finite(value)

    @model_validator(mode="after")
    def reconcile(self) -> SyntheticGap:
        if not math.isclose(self.gap, self.treated - self.synthetic, abs_tol=1e-10):
            raise ValueError("synthetic-control gap does not reconcile")
        return self


class PlaceboEffect(BaseModel):
    model_config = STRICT_FROZEN
    unit: str
    effect: float

    @field_validator("unit")
    @classmethod
    def name(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("placebo unit must be canonical")
        return value

    @field_validator("effect")
    @classmethod
    def finite(cls, value: float) -> float:
        return _finite(value)


class SyntheticControlResult(BaseModel):
    model_config = STRICT_FROZEN
    method_id: Literal["synthetic-control"]
    effect: RegressionCoefficient
    donor_weights: tuple[DonorWeight, ...] = Field(min_length=2)
    gaps: tuple[SyntheticGap, ...] = Field(min_length=3)
    placebos: tuple[PlaceboEffect, ...] = Field(min_length=2)
    leave_one_out: tuple[LeaveOneOutEffect, ...] = Field(min_length=2)
    pre_periods: int = Field(gt=1)
    post_periods: int = Field(gt=0)
    pre_rmspe: float = Field(ge=0.0)
    post_rmspe: float = Field(ge=0.0)
    post_pre_ratio: float = Field(ge=0.0)
    intervention_time: float
    package_version: str
    max_pre_rmspe: float = Field(gt=0.0)
    max_leave_one_out_change: float = Field(ge=0.0)
    leave_one_out_threshold: float = Field(gt=0.0)
    configuration: WavePackageConfiguration
    figure_sha256: Digest

    @field_validator(
        "pre_rmspe",
        "post_rmspe",
        "post_pre_ratio",
        "intervention_time",
        "max_pre_rmspe",
        "max_leave_one_out_change",
        "leave_one_out_threshold",
    )
    @classmethod
    def finite_diagnostic(cls, value: float) -> float:
        return _finite(value)

    @field_validator("package_version")
    @classmethod
    def version(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("package version must be canonical")
        return value

    @model_validator(mode="after")
    def coherent(self) -> SyntheticControlResult:
        donors = tuple(item.donor for item in self.donor_weights)
        omitted = tuple(item.omitted for item in self.leave_one_out)
        if len(set(donors)) != len(donors) or len(set(omitted)) != len(omitted):
            raise ValueError("synthetic-control donor identities must be unique")
        placebo_units = tuple(item.unit for item in self.placebos)
        if (
            set(omitted) != set(donors)
            or set(placebo_units) != set(donors)
            or len(placebo_units) != len(set(placebo_units))
        ):
            raise ValueError("synthetic-control sensitivity must cover every donor")
        if not math.isclose(
            sum(item.weight for item in self.donor_weights), 1.0, abs_tol=1e-8
        ):
            raise ValueError("synthetic-control donor weights must sum to one")
        if (
            sum(item.period == "pre" for item in self.gaps) != self.pre_periods
            or sum(item.period == "post" for item in self.gaps) != self.post_periods
        ):
            raise ValueError("synthetic-control period support is inconsistent")
        if len({item.time for item in self.gaps}) != len(self.gaps):
            raise ValueError("synthetic-control gap times must be unique")
        observed_pre = math.sqrt(
            sum(item.gap**2 for item in self.gaps if item.period == "pre")
            / self.pre_periods
        )
        observed_post = math.sqrt(
            sum(item.gap**2 for item in self.gaps if item.period == "post")
            / self.post_periods
        )
        if not math.isclose(
            self.pre_rmspe, observed_pre, rel_tol=1e-10
        ) or not math.isclose(self.post_rmspe, observed_post, rel_tol=1e-10):
            raise ValueError("synthetic-control RMSPE does not match gaps")
        if self.pre_rmspe > self.max_pre_rmspe:
            raise ValueError("synthetic-control pre-fit exceeds its threshold")
        if self.pre_rmspe == 0 or not math.isclose(
            self.post_pre_ratio,
            self.post_rmspe / self.pre_rmspe,
            rel_tol=1e-10,
        ):
            raise ValueError("synthetic-control RMSPE ratio is inconsistent")
        observed = max(item.absolute_change for item in self.leave_one_out)
        if any(
            not math.isclose(
                item.absolute_change,
                abs(item.effect - self.effect.estimate),
                abs_tol=1e-12,
            )
            for item in self.leave_one_out
        ):
            raise ValueError("sensitivity change does not match the central effect")
        if not math.isclose(self.max_leave_one_out_change, observed, abs_tol=1e-12):
            raise ValueError("synthetic-control sensitivity maximum is inconsistent")
        if observed > self.leave_one_out_threshold:
            raise ValueError("synthetic-control sensitivity exceeds its threshold")
        if self.configuration.method_id != self.method_id:
            raise ValueError("result configuration selects another method")
        return self


class StudyEvidence(BaseModel):
    model_config = STRICT_FROZEN
    study: str
    effect: float
    std_error: float = Field(gt=0.0)
    weight: float = Field(ge=0.0, le=1.0)

    @field_validator("study")
    @classmethod
    def study_name(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("study name must be canonical")
        return value

    @field_validator("effect", "std_error", "weight")
    @classmethod
    def finite(cls, value: float) -> float:
        return _finite(value)


StudyWeight = StudyEvidence


class FunnelPoint(BaseModel):
    model_config = STRICT_FROZEN
    study: str
    effect: float
    std_error: float = Field(gt=0.0)

    @field_validator("effect", "std_error")
    @classmethod
    def finite(cls, value: float) -> float:
        return _finite(value)


class MetaAnalysisResult(BaseModel):
    model_config = STRICT_FROZEN
    method_id: Literal["meta-analysis"]
    fixed: RegressionCoefficient
    random: RegressionCoefficient
    study_weights: tuple[StudyEvidence, ...] = Field(min_length=2)
    funnel: tuple[FunnelPoint, ...] = Field(min_length=2)
    leave_one_out: tuple[LeaveOneOutEffect, ...] = Field(min_length=2)
    studies: int = Field(gt=1)
    q: float = Field(ge=0.0)
    i_squared: float = Field(ge=0.0, le=100.0)
    tau_squared: float = Field(ge=0.0)
    inverse_variance_support: float = Field(gt=0.0)
    prediction_low: float
    prediction_high: float
    package_version: str
    model: Literal["fixed-and-dl-random"]
    max_leave_one_out_change: float = Field(ge=0.0)
    leave_one_out_threshold: float = Field(gt=0.0)
    configuration: WavePackageConfiguration
    figure_sha256: Digest

    @field_validator(
        "q",
        "i_squared",
        "tau_squared",
        "inverse_variance_support",
        "prediction_low",
        "prediction_high",
        "max_leave_one_out_change",
        "leave_one_out_threshold",
    )
    @classmethod
    def finite_diagnostic(cls, value: float) -> float:
        return _finite(value)

    @field_validator("package_version")
    @classmethod
    def version(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("package version must be canonical")
        return value

    @model_validator(mode="after")
    def coherent(self) -> MetaAnalysisResult:
        studies = tuple(item.study for item in self.study_weights)
        omitted = tuple(item.omitted for item in self.leave_one_out)
        if len(set(studies)) != len(studies) or len(self.study_weights) != self.studies:
            raise ValueError("meta-analysis study evidence is inconsistent")
        funnel_studies = tuple(item.study for item in self.funnel)
        if (
            set(omitted) != set(studies)
            or set(funnel_studies) != set(studies)
            or len(funnel_studies) != len(set(funnel_studies))
        ):
            raise ValueError("meta-analysis sensitivity must cover every study")
        if not math.isclose(
            sum(item.weight for item in self.study_weights), 1.0, abs_tol=1e-8
        ):
            raise ValueError("meta-analysis study weights do not reconcile")
        if (
            self.prediction_low > self.random.estimate
            or self.prediction_high < self.random.estimate
        ):
            raise ValueError("prediction interval must contain random estimate")
        observed = max(item.absolute_change for item in self.leave_one_out)
        if any(
            not math.isclose(
                item.absolute_change,
                abs(item.effect - self.random.estimate),
                abs_tol=1e-12,
            )
            for item in self.leave_one_out
        ):
            raise ValueError("sensitivity change does not match the central effect")
        if not math.isclose(self.max_leave_one_out_change, observed, abs_tol=1e-12):
            raise ValueError("meta-analysis sensitivity maximum is inconsistent")
        if observed > self.leave_one_out_threshold:
            raise ValueError("meta-analysis influence exceeds its threshold")
        if self.configuration.method_id != self.method_id:
            raise ValueError("result configuration selects another method")
        return self
