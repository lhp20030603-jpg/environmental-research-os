"""Frozen contracts for the V0.4 claim-evidence ledger."""

from __future__ import annotations

import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from envresearch.econometrics.report import LocalAnalysisReference
from envresearch.models.artifact import ArtifactRef

STRICT = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)
CANONICAL_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")

ClaimType = Literal[
    "welfare-estimate",
    "effect-estimate",
    "descriptive-quantity",
    "diagnostic",
    "robustness",
]
ClaimStrength = Literal[
    "descriptive",
    "associational",
    "design-based-causal",
    "model-conditional-valuation",
]


class AnalysisOutputRef(BaseModel):
    """One exact output whose authority is its authenticated parent report."""

    model_config = STRICT

    analysis_ref: LocalAnalysisReference
    name: str
    sha256: str
    size_bytes: int = Field(ge=0)
    result_pointers: tuple[str, ...] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def require_output_name(cls, value: str) -> str:
        if not value or value != value.strip() or "/" in value or "\\" in value:
            raise ValueError("analysis output name must be one canonical filename")
        return value

    @field_validator("sha256")
    @classmethod
    def require_digest(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("analysis output digest is invalid")
        return value

    @field_validator("result_pointers")
    @classmethod
    def require_result_pointers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(
            not item.startswith("/") or item == "/" or item.endswith("/")
            for item in value
        ):
            raise ValueError("result pointers must be unique canonical JSON pointers")
        return value


class ClaimUncertainty(BaseModel):
    """Exact uncertainty attached to one numeric paper claim."""

    model_config = STRICT

    std_error: float = Field(ge=0.0)
    confidence_low: float
    confidence_high: float
    confidence_level: float = Field(gt=0.0, lt=1.0)

    @field_validator(
        "std_error", "confidence_low", "confidence_high", "confidence_level"
    )
    @classmethod
    def require_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("claim uncertainty must be finite")
        return value

    @model_validator(mode="after")
    def require_ordered_interval(self) -> ClaimUncertainty:
        if self.confidence_low > self.confidence_high:
            raise ValueError("claim confidence interval is reversed")
        return self


class EstimatedClaimValue(BaseModel):
    """One reconstructed estimate with its registered uncertainty."""

    model_config = STRICT

    kind: Literal["estimate"]
    estimate: float
    uncertainty: ClaimUncertainty

    @field_validator("estimate")
    @classmethod
    def require_finite_estimate(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("claim estimate must be finite")
        return value

    @model_validator(mode="after")
    def require_interval_contains_estimate(self) -> EstimatedClaimValue:
        if not (
            self.uncertainty.confidence_low
            <= self.estimate
            <= self.uncertainty.confidence_high
        ):
            raise ValueError("claim estimate must lie within its confidence interval")
        return self


class DescriptiveRangeValue(BaseModel):
    """One reconstructed descriptive range without estimator uncertainty."""

    model_config = STRICT

    kind: Literal["descriptive-range"]
    minimum: float
    maximum: float
    uncertainty_status: Literal["not-estimated"]

    @field_validator("minimum", "maximum")
    @classmethod
    def require_finite_bound(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("descriptive range must be finite")
        return value

    @model_validator(mode="after")
    def require_ordered_range(self) -> DescriptiveRangeValue:
        if self.minimum > self.maximum:
            raise ValueError("descriptive range is reversed")
        return self


class DescriptiveSeriesPoint(BaseModel):
    """One exact counted point in a reconstructed descriptive series."""

    model_config = STRICT

    x: float
    numerator: int = Field(ge=0)
    denominator: int = Field(gt=0)
    value: float

    @field_validator("x", "value")
    @classmethod
    def require_finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("counted-series coordinates must be finite")
        return value

    @model_validator(mode="after")
    def require_reconciled_count(self) -> DescriptiveSeriesPoint:
        if self.numerator > self.denominator or not math.isclose(
            self.value,
            self.numerator / self.denominator,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError("counted-series value does not reconcile with counts")
        return self


class DescriptiveSeriesValue(BaseModel):
    """One ordered reconstructed series whose points bind exact counts."""

    model_config = STRICT

    kind: Literal["counted-series"]
    x_name: str
    x_unit: str
    y_name: str
    y_unit: str
    points: tuple[DescriptiveSeriesPoint, ...] = Field(min_length=1)
    uncertainty_status: Literal["not-estimated"]

    @field_validator("x_name", "y_name")
    @classmethod
    def require_axis_name(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("series axis names must be canonical identifiers")
        return value

    @field_validator("x_unit", "y_unit")
    @classmethod
    def require_axis_unit(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("series axis units must be canonical nonblank values")
        return value

    @model_validator(mode="after")
    def require_ordered_unique_points(self) -> DescriptiveSeriesValue:
        coordinates = tuple(item.x for item in self.points)
        if coordinates != tuple(sorted(set(coordinates))):
            raise ValueError("counted-series coordinates must be unique and ordered")
        return self


ClaimValue = Annotated[
    EstimatedClaimValue | DescriptiveRangeValue | DescriptiveSeriesValue,
    Field(discriminator="kind"),
]


class ClaimEvidenceRow(BaseModel):
    """One independently reconstructed empirical claim and its exact evidence."""

    model_config = STRICT

    claim_id: str
    claim_type: ClaimType
    method_id: str
    quantity: str
    value: ClaimValue
    transition_ref: ArtifactRef
    analysis_ref: LocalAnalysisReference
    snapshot_ref: ArtifactRef
    output_evidence: tuple[AnalysisOutputRef, ...] = Field(min_length=1)
    reconstruction_status: Literal["independently-reconstructed"]
    welfare_transformation: str | None
    unit: str
    population_basis: str
    time_basis: str
    price_base: str
    allowed_strength: ClaimStrength
    limitations: tuple[str, ...] = Field(min_length=1)

    @field_validator("claim_id")
    @classmethod
    def require_claim_id(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("claim id must be canonical lowercase kebab-case")
        return value

    @field_validator("method_id", "quantity")
    @classmethod
    def require_method_and_estimand(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("method and quantity must be canonical identifiers")
        return value

    @field_validator(
        "unit",
        "population_basis",
        "time_basis",
        "price_base",
    )
    @classmethod
    def require_nonblank_basis(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("claim basis fields must be canonical nonblank values")
        return value

    @field_validator("welfare_transformation")
    @classmethod
    def require_transformation(cls, value: str | None) -> str | None:
        if value is not None and (
            not CANONICAL_ID.fullmatch(value) or value != value.strip()
        ):
            raise ValueError("welfare transformation must be canonical")
        return value

    @field_validator("limitations")
    @classmethod
    def require_limitations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or item != item.strip() for item in value):
            raise ValueError("claim limitations must be canonical nonblank values")
        if len(set(value)) != len(value):
            raise ValueError("claim limitations must be unique")
        return value

    @model_validator(mode="after")
    def require_coherent_evidence(self) -> ClaimEvidenceRow:
        if self.claim_id != f"{self.method_id}-{self.quantity}":
            raise ValueError("claim id must bind its method and quantity")
        if self.claim_type == "welfare-estimate" and (
            not isinstance(self.value, EstimatedClaimValue)
            or self.welfare_transformation is None
            or self.allowed_strength != "model-conditional-valuation"
        ):
            raise ValueError(
                "welfare claims require an estimated transformed valuation strength"
            )
        if self.claim_type == "effect-estimate" and not isinstance(
            self.value, EstimatedClaimValue
        ):
            raise ValueError("effect claims require an estimated value")
        if self.claim_type == "descriptive-quantity" and (
            isinstance(self.value, EstimatedClaimValue)
            or self.allowed_strength != "descriptive"
            or self.welfare_transformation is not None
        ):
            raise ValueError("descriptive claims require descriptive scope")
        names = tuple(item.name for item in self.output_evidence)
        if len(names) != len(set(names)):
            raise ValueError("claim output evidence names must be unique")
        if any(item.analysis_ref != self.analysis_ref for item in self.output_evidence):
            raise ValueError("claim outputs must bind the same analysis reference")
        return self


class ClaimEvidenceLedger(BaseModel):
    """Immutable set of writable claims derived from one exact transition."""

    model_config = STRICT

    schema_version: Literal["paper.claim-evidence-ledger.v1"]
    ledger_id: str
    producer: Literal["paper-builder-ledger-v1"]
    transition_ref: ArtifactRef
    claims: tuple[ClaimEvidenceRow, ...] = Field(min_length=1)

    @field_validator("ledger_id")
    @classmethod
    def require_ledger_id(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("ledger id must be canonical lowercase kebab-case")
        return value

    @model_validator(mode="after")
    def require_exact_rows(self) -> ClaimEvidenceLedger:
        identifiers = tuple(item.claim_id for item in self.claims)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("ledger claim ids must be unique")
        if any(item.transition_ref != self.transition_ref for item in self.claims):
            raise ValueError("ledger rows must bind the same transition")
        return self


__all__ = [
    "AnalysisOutputRef",
    "ClaimEvidenceLedger",
    "ClaimEvidenceRow",
    "ClaimStrength",
    "ClaimType",
    "ClaimUncertainty",
    "ClaimValue",
    "DescriptiveRangeValue",
    "DescriptiveSeriesPoint",
    "DescriptiveSeriesValue",
    "EstimatedClaimValue",
]
