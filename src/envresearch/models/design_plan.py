"""Terminal analysis-plan and research-quality contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictInt, field_validator, model_validator

from envresearch.models.design import (
    ClaimMode,
    EstimandSpecPayload,
    EstimandType,
    SerializedStringTuple,
    _require_nonblank,
    _require_nonblank_unique,
    _StrictModel,
)


class AnalysisPlanPayload(_StrictModel):
    """The terminal V0.2 planning artifact; it authorizes no analysis execution."""

    estimand_ref: str
    estimand_type: EstimandType | None = None
    estimand: EstimandSpecPayload | None = None
    primary_method_profile_ref: str
    alternative_method_profile_refs: SerializedStringTuple = Field(min_length=1)
    data_boundaries: SerializedStringTuple = Field(min_length=1)
    assumptions: SerializedStringTuple = Field(min_length=1)
    diagnostics: SerializedStringTuple = Field(min_length=1)
    exclusion_rules: SerializedStringTuple = Field(min_length=1)
    robustness_plan: SerializedStringTuple = Field(min_length=1)
    fallback_rules: SerializedStringTuple = Field(min_length=1)
    claim_mode: ClaimMode
    stop_rule: Literal["approved_plan_only"] = "approved_plan_only"

    @field_validator("estimand_type", mode="before")
    @classmethod
    def parse_serialized_plan_estimand_type(cls, value: object) -> object:
        if isinstance(value, str):
            return EstimandType(value)
        return value

    @field_validator("claim_mode", mode="before")
    @classmethod
    def parse_serialized_claim_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return ClaimMode(value)
        return value

    @field_validator("estimand_ref", "primary_method_profile_ref")
    @classmethod
    def require_nonblank_plan_identity_ref(cls, value: str) -> str:
        return _require_nonblank(value)

    @field_validator(
        "alternative_method_profile_refs",
        "data_boundaries",
        "assumptions",
        "diagnostics",
        "exclusion_rules",
        "robustness_plan",
        "fallback_rules",
    )
    @classmethod
    def require_complete_plan_sections(
        cls, value: tuple[str, ...], info: object
    ) -> tuple[str, ...]:
        return _require_nonblank_unique(
            value,
            field_name=getattr(info, "field_name", "plan section"),
            require_items=True,
        )

    @model_validator(mode="after")
    def require_claim_support_and_distinct_methods(self) -> AnalysisPlanPayload:
        resolved_estimand_type = self.estimand_type
        if self.estimand is not None:
            if (
                resolved_estimand_type is not None
                and resolved_estimand_type is not self.estimand.estimand_type
            ):
                raise ValueError("estimand_type must match the embedded estimand")
            resolved_estimand_type = self.estimand.estimand_type
        if (
            self.claim_mode is ClaimMode.CAUSAL
            and resolved_estimand_type is not EstimandType.CAUSAL
        ):
            raise ValueError("causal claim requires a causal estimand")
        if self.primary_method_profile_ref in self.alternative_method_profile_refs:
            raise ValueError("primary method profile must not duplicate alternatives")
        return self


class ResearchQualityScores(_StrictModel):
    """The fixed six-dimension, integer-only V0.2 quality rubric."""

    contribution_clarity: StrictInt = Field(ge=1, le=5)
    evidence_coverage: StrictInt = Field(ge=1, le=5)
    data_feasibility: StrictInt = Field(ge=1, le=5)
    estimand_precision: StrictInt = Field(ge=1, le=5)
    identification_credibility: StrictInt = Field(ge=1, le=5)
    uncertainty_disclosure: StrictInt = Field(ge=1, le=5)
