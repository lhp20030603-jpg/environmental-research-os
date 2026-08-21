"""Strict source-blind brief and leakage-validation contracts.

These records form the handoff boundary between source-aware curation and
source-blind method recommendation.  They intentionally carry only opaque
artifact references and method-relevant facts; source identity belongs solely
in the curator-only source sheet.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    field_validator,
    model_validator,
)

from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_claims import ClaimVerificationStatus

__all__ = [
    "BlindedBrief",
    "BlindedFact",
    "ClaimVerificationStatus",
    "LeakageCategory",
    "LeakageFinding",
    "LeakageReport",
    "LeakageSeverity",
]

_CANONICAL_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_JSON_POINTER = re.compile(r"^(?:/(?:[^~/]|~[01])*)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class _StrictModel(BaseModel):
    """Base contract for immutable, strictly parsed blinded artifacts."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _parse_serialized_tuple(value: object) -> object:
    """Accept serialized arrays without relaxing scalar type validation."""
    if isinstance(value, list):
        return tuple(value)
    return value


SerializedStringTuple = Annotated[
    tuple[str, ...], BeforeValidator(_parse_serialized_tuple)
]


def _require_nonblank(value: str, field_name: str) -> str:
    """Reject blank or noncanonical persisted human-readable text."""
    if not value.strip() or value != value.strip():
        raise ValueError(f"{field_name} must be a canonical nonblank value")
    return value


def _require_canonical_id(value: str, field_name: str) -> str:
    """Keep opaque fact and case identifiers unambiguous across artifacts."""
    if not _CANONICAL_ID.fullmatch(value):
        raise ValueError(f"{field_name} must be a canonical lowercase identifier")
    return value


def _require_sha256(value: str, field_name: str) -> str:
    """Require a lowercase SHA-256 digest at immutable validation boundaries."""
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a 64-character lowercase SHA-256")
    return value


def _require_unique_strings(
    value: tuple[str, ...], field_name: str, *, require_items: bool
) -> tuple[str, ...]:
    """Keep serialized method inputs complete, reviewable, and deterministic."""
    if require_items and not value:
        raise ValueError(f"{field_name} must contain at least one item")
    for item in value:
        _require_nonblank(item, field_name)
    if len(value) != len(set(value)):
        raise ValueError(f"{field_name} must not contain duplicate values")
    return value


class LeakageCategory(StrEnum):
    """The source-identity channel examined by a leakage validation finding."""

    IDENTITY = "identity"
    CITATION = "citation"
    DATASET = "dataset"
    METHOD = "method"
    RESULT = "result"
    PHRASE = "phrase"
    HIDDEN_METADATA = "hidden_metadata"


class LeakageSeverity(StrEnum):
    """The urgency of remediating a source-identity disclosure."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class BlindedFact(_StrictModel):
    """One opaque, method-relevant fact available to blind reviewers."""

    fact_id: str
    statement: str
    fact_kind: Literal["institution", "timing", "unit", "outcome", "data", "constraint"]

    @field_validator("fact_id")
    @classmethod
    def require_fact_id(cls, value: str) -> str:
        """Bind downstream method reasoning to a stable opaque fact identifier."""
        return _require_canonical_id(value, "fact_id")

    @field_validator("statement")
    @classmethod
    def require_statement(cls, value: str) -> str:
        """Keep each disclosed fact readable without permitting blank content."""
        return _require_nonblank(value, "statement")


class BlindedBrief(_StrictModel):
    """The masker-produced, source-blind input to method recommendation."""

    schema_version: Literal["1.0"] = "1.0"
    case_id: str
    source_sheet_ref: ArtifactRef
    policy_setting: str
    population: str
    unit: str
    treatment_or_exposure: str
    timing: str
    candidate_outcomes: SerializedStringTuple
    data_structures: SerializedStringTuple
    available_variables: SerializedStringTuple
    institutional_rules: SerializedStringTuple
    constraints: SerializedStringTuple
    facts: tuple[BlindedFact, ...]
    missing_facts: SerializedStringTuple = ()
    masker_principal: str

    @field_validator("case_id")
    @classmethod
    def require_case_id(cls, value: str) -> str:
        """Keep the source-independent benchmark case identity opaque and stable."""
        return _require_canonical_id(value, "case_id")

    @field_validator(
        "policy_setting",
        "population",
        "unit",
        "treatment_or_exposure",
        "timing",
        "masker_principal",
    )
    @classmethod
    def require_nonblank_text(cls, value: str, info: object) -> str:
        """Reject blank required method inputs and accountable identities."""
        return _require_nonblank(value, getattr(info, "field_name", "brief field"))

    @field_validator(
        "candidate_outcomes",
        "data_structures",
        "available_variables",
        "institutional_rules",
        "constraints",
    )
    @classmethod
    def require_complete_unique_inputs(
        cls, value: tuple[str, ...], info: object
    ) -> tuple[str, ...]:
        """Require all method-choice inputs to be nonempty and unambiguous."""
        return _require_unique_strings(
            value, getattr(info, "field_name", "brief values"), require_items=True
        )

    @field_validator("missing_facts")
    @classmethod
    def require_unique_missing_facts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Record unknowns explicitly without duplicate or blank placeholders."""
        return _require_unique_strings(value, "missing_facts", require_items=False)

    @field_validator("facts")
    @classmethod
    def require_facts(cls, value: tuple[BlindedFact, ...]) -> tuple[BlindedFact, ...]:
        """Require at least one uniquely identified fact for traceable reasoning."""
        if not value:
            raise ValueError("facts must contain at least one item")
        fact_ids = tuple(fact.fact_id for fact in value)
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("facts must not contain duplicate fact IDs")
        return value


class LeakageFinding(_StrictModel):
    """One locator-bound leakage observation and its remediation disposition."""

    category: LeakageCategory
    severity: LeakageSeverity
    locator: str
    disposition: str
    resolved: bool = False

    @field_validator("locator")
    @classmethod
    def require_locator(cls, value: str) -> str:
        """Use an exact RFC 6901 path without storing source text in the finding."""
        if not _JSON_POINTER.fullmatch(value):
            raise ValueError("locator must be an RFC 6901 pointer")
        return value

    @field_validator("disposition")
    @classmethod
    def require_disposition(cls, value: str) -> str:
        """Require a reviewable action or recorded resolution for every finding."""
        return _require_nonblank(value, "disposition")


class LeakageReport(_StrictModel):
    """A validator's immutable verdict over one exact source/brief pair."""

    source_sheet_ref: ArtifactRef
    blinded_brief_ref: ArtifactRef
    findings: tuple[LeakageFinding, ...]
    verdict: Literal["pass", "rejected"]
    validator_principal: str
    scanner_version: Literal["blind-leakage-v1", "blind-leakage-v2"]
    scanner_config_sha256: str
    checked_at: datetime

    @field_validator("validator_principal")
    @classmethod
    def require_validator_principal(cls, value: str) -> str:
        """Keep the independently accountable validator identity explicit."""
        return _require_nonblank(value, "validator_principal")

    @field_validator("scanner_config_sha256")
    @classmethod
    def require_scanner_config_hash(cls, value: str) -> str:
        """Bind each verdict to the validator's exact immutable configuration."""
        return _require_sha256(value, "scanner_config_sha256")

    @field_validator("checked_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """Reject ambiguous report times at the blind-handoff trust boundary."""
        if value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
            raise ValueError("timestamps must be UTC-aware")
        return value

    @model_validator(mode="after")
    def require_exact_refs_and_consistent_verdict(self) -> LeakageReport:
        """Prevent self-referential reports and fail closed on open findings."""
        if self.source_sheet_ref == self.blinded_brief_ref:
            raise ValueError("source_sheet_ref and blinded_brief_ref must differ")
        if self.verdict == "pass" and any(
            not finding.resolved for finding in self.findings
        ):
            raise ValueError("PASS report cannot contain open findings")
        return self
