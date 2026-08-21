"""Strict planning contracts for methodology families."""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

_PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CANONICAL_VALUE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_REFERENCE_PATTERNS = (
    re.compile(r"^DOI:10\.\d{4,9}/\S+$", re.IGNORECASE),
    re.compile(r"^ISBN:(?:\d[\d-]{8,16}\d)$", re.IGNORECASE),
    re.compile(r"^(?:JSTOR|PMID|SSRN):[A-Za-z0-9.-]+$", re.IGNORECASE),
    re.compile(r"^arXiv:\d{4}\.\d{4,5}(?:v\d+)?$", re.IGNORECASE),
)
_EXECUTION_FIELD_TOKENS = ("command", "script", "entrypoint")


def _serialized_frozenset(value: object) -> object:
    """Accept YAML/JSON arrays, preserving duplicate detection before freezing."""
    items = _serialized_string_items(value)
    if items is None:
        return value
    return frozenset(items)


def _serialized_tuple(value: object) -> object:
    """Accept YAML/JSON arrays while keeping the stored collection immutable."""
    items = _serialized_string_items(value)
    if items is None:
        return value
    return tuple(items)


def _serialized_string_items(value: object) -> list[str] | None:
    """Validate serialized collection items before any hash-based conversion."""
    if not isinstance(value, list):
        return None
    items: list[str] = []
    for item in value:
        if type(item) is not str:
            raise ValueError("list must contain strings only")
        items.append(item)
    if any(not item.strip() for item in items):
        raise ValueError("list must not contain blank values")
    if len(items) != len(set(items)):
        raise ValueError("list must not contain duplicate values")
    return items


SerializedStringFrozenSet = Annotated[
    frozenset[StrictStr], BeforeValidator(_serialized_frozenset)
]
SerializedStringTuple = Annotated[
    tuple[StrictStr, ...], BeforeValidator(_serialized_tuple)
]


class MethodProfile(BaseModel):
    """A frozen, non-executable checklist for one methodology family."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    profile_id: StrictStr
    version: StrictStr
    family: StrictStr
    compatible_estimands: SerializedStringFrozenSet = Field(min_length=1)
    required_data_structures: SerializedStringFrozenSet = Field(min_length=1)
    required_features: SerializedStringFrozenSet = Field(min_length=1)
    identifying_assumptions: SerializedStringTuple = Field(min_length=2)
    incompatibility_rules: SerializedStringTuple = Field(min_length=1)
    mandatory_diagnostics: SerializedStringTuple = Field(min_length=2)
    falsification_checks: SerializedStringTuple = Field(min_length=1)
    fallback_profiles: SerializedStringTuple
    analysis_plan_fields: SerializedStringTuple = Field(min_length=1)
    methodological_references: SerializedStringTuple = Field(min_length=1)
    estimator_entrypoint: None = None

    @model_validator(mode="before")
    @classmethod
    def forbid_execution_content(cls, value: Any) -> Any:
        """Reject fields that could turn a planning profile into executable content."""
        if not isinstance(value, dict):
            return value
        forbidden = [
            str(key)
            for key in value
            if key != "estimator_entrypoint"
            and any(token in str(key).casefold() for token in _EXECUTION_FIELD_TOKENS)
        ]
        if forbidden:
            raise ValueError(
                "method profiles must not contain commands, scripts, or entrypoints: "
                + ", ".join(sorted(forbidden))
            )
        return value

    @field_validator("profile_id")
    @classmethod
    def require_safe_profile_id(cls, value: str) -> str:
        """Keep profile identity safe for directory and registry use."""
        if not _PROFILE_ID_PATTERN.fullmatch(value):
            raise ValueError("profile_id must be a canonical kebab-case identifier")
        return value

    @field_validator("version")
    @classmethod
    def require_semver(cls, value: str) -> str:
        """Use a complete SemVer value for manifest consistency checks."""
        if not _SEMVER_PATTERN.fullmatch(value):
            raise ValueError("version must be a valid SemVer version")
        return value

    @field_validator("family")
    @classmethod
    def require_canonical_family(cls, value: str) -> str:
        """Use one safe snake-case value for the method family."""
        if not _CANONICAL_VALUE_PATTERN.fullmatch(value):
            raise ValueError("family must be a canonical snake-case value")
        return value

    @field_validator("compatible_estimands")
    @classmethod
    def require_known_estimands(cls, value: frozenset[str]) -> frozenset[str]:
        """Align profile claims with the kernel's causal/descriptive vocabulary."""
        if not value <= {"causal", "descriptive"}:
            raise ValueError("compatible_estimands contains an unknown value")
        cls._require_nonblank_collection(value, "compatible_estimands")
        return value

    @field_validator("required_data_structures", "required_features")
    @classmethod
    def require_canonical_capabilities(
        cls, value: frozenset[str], info: object
    ) -> frozenset[str]:
        """Reject ambiguous or unsafe structure and feature tokens."""
        field_name = getattr(info, "field_name", "capabilities")
        cls._require_nonblank_collection(value, field_name)
        if any(not _CANONICAL_VALUE_PATTERN.fullmatch(item) for item in value):
            raise ValueError(f"{field_name} must contain canonical snake-case values")
        return value

    @field_validator(
        "identifying_assumptions",
        "incompatibility_rules",
        "mandatory_diagnostics",
        "falsification_checks",
        "fallback_profiles",
        "analysis_plan_fields",
        "methodological_references",
    )
    @classmethod
    def require_nonblank_tuple(
        cls, value: tuple[str, ...], info: object
    ) -> tuple[str, ...]:
        """Keep each planning checklist readable and unambiguous."""
        field_name = getattr(info, "field_name", "list")
        cls._require_nonblank_collection(value, field_name)
        if len(set(value)) != len(value):
            raise ValueError(f"{field_name} must not contain duplicate values")
        return value

    @field_validator("fallback_profiles")
    @classmethod
    def require_safe_fallback_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Keep fallback links resolvable by the same profile registry."""
        if any(not _PROFILE_ID_PATTERN.fullmatch(item) for item in value):
            raise ValueError("fallback_profiles must contain canonical profile IDs")
        return value

    @field_validator("methodological_references")
    @classmethod
    def require_stable_references(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require identifiers that remain resolvable independently of prose titles."""
        if any(
            not any(pattern.fullmatch(reference) for pattern in _REFERENCE_PATTERNS)
            for reference in value
        ):
            raise ValueError(
                "methodological_references must use DOI, ISBN, JSTOR, PMID, SSRN, "
                "or arXiv identifiers"
            )
        return value

    @model_validator(mode="after")
    def forbid_self_fallback(self) -> MethodProfile:
        """Prevent a fallback cycle that cannot change the proposed design."""
        if self.profile_id in self.fallback_profiles:
            raise ValueError("fallback_profiles must not reference the profile itself")
        return self

    @staticmethod
    def _require_nonblank_collection(
        value: tuple[str, ...] | frozenset[str], field_name: str
    ) -> None:
        if any(not item.strip() for item in value):
            raise ValueError(f"{field_name} must not contain blank values")

    def is_compatible(
        self,
        estimand_type: str,
        data_structure: str,
        features: frozenset[str],
    ) -> bool:
        """Require estimand, structure, and every feature without heuristic matching."""
        self._validate_compatibility_inputs(
            estimand_type=estimand_type,
            data_structure=data_structure,
            features=features,
        )
        return (
            estimand_type in self.compatible_estimands
            and data_structure in self.required_data_structures
            and self.required_features <= features
        )

    @staticmethod
    def _validate_compatibility_inputs(
        *,
        estimand_type: str,
        data_structure: str,
        features: frozenset[str],
    ) -> None:
        if type(estimand_type) is not str or estimand_type not in {
            "causal",
            "descriptive",
        }:
            raise ValueError("estimand_type must be causal or descriptive")
        if type(data_structure) is not str or not _CANONICAL_VALUE_PATTERN.fullmatch(
            data_structure
        ):
            raise ValueError("data_structure must be a canonical snake-case value")
        if type(features) is not frozenset:
            raise TypeError("features must be a frozenset of canonical strings")
        if any(
            type(feature) is not str
            or not _CANONICAL_VALUE_PATTERN.fullmatch(feature)
            for feature in features
        ):
            raise ValueError("features must contain canonical snake-case strings")
