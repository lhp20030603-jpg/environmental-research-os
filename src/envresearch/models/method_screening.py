"""Structured evidence for rejecting incompatible methodology profiles."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MethodRequirementKind(StrEnum):
    """Profile requirement categories that can make a method incompatible."""

    ESTIMAND_TYPE = "estimand_type"
    DATA_STRUCTURE_SET = "data_structure_set"
    FEATURE_SET = "feature_set"


class MethodRejectionEvidence(BaseModel):
    """One machine-verifiable unmet requirement plus a human explanation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    requirement_kind: MethodRequirementKind
    requirement_refs: tuple[str, ...] = Field(min_length=1)
    explanation: str

    @field_validator("requirement_kind", mode="before")
    @classmethod
    def parse_serialized_kind(cls, value: object) -> object:
        if isinstance(value, str):
            return MethodRequirementKind(value)
        return value

    @field_validator("requirement_refs", mode="before")
    @classmethod
    def parse_serialized_refs(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("requirement_refs")
    @classmethod
    def require_unique_nonblank_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value) or len(value) != len(set(value)):
            raise ValueError("rejection requirement refs must be unique and nonblank")
        return value

    @field_validator("explanation")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rejection explanation must not be blank")
        return value
