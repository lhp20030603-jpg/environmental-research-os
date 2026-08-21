"""Typed detailed evidence shared by Wave-1 result records."""

from __future__ import annotations

import math

from pydantic import BaseModel, Field, field_validator, model_validator

from envresearch.econometrics.contracts import STRICT_FROZEN


class RctBalance(BaseModel):
    model_config = STRICT_FROZEN
    term: str
    smd: float

    @field_validator("term")
    @classmethod
    def canonical_term(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("balance term must be canonical")
        return value

    @field_validator("smd")
    @classmethod
    def finite_smd(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("balance SMD must be finite")
        return value


class TemporalMean(BaseModel):
    model_config = STRICT_FROZEN
    date: str
    mean: float

    @field_validator("date")
    @classmethod
    def canonical_date(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("temporal date must be canonical")
        return value

    @field_validator("mean")
    @classmethod
    def finite_mean(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("temporal mean must be finite")
        return value


class MeasurementQuantiles(BaseModel):
    model_config = STRICT_FROZEN
    q25: float
    median: float
    q75: float

    @field_validator("q25", "median", "q75")
    @classmethod
    def finite_quantile(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("measurement quantiles must be finite")
        return value

    @model_validator(mode="after")
    def ordered(self) -> MeasurementQuantiles:
        if not self.q25 <= self.median <= self.q75:
            raise ValueError("measurement quantiles must be ordered")
        return self


class MonitorCoverage(BaseModel):
    model_config = STRICT_FROZEN
    monitor: str
    total: int = Field(gt=0)
    valid: int = Field(ge=0)
    missing: int = Field(ge=0)

    @field_validator("monitor")
    @classmethod
    def canonical_monitor(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("monitor identity must be canonical")
        return value

    @model_validator(mode="after")
    def reconcile(self) -> MonitorCoverage:
        if self.valid + self.missing != self.total:
            raise ValueError("monitor coverage does not reconcile")
        return self
