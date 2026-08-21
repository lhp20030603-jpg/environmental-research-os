"""Strict parsing for executable intake and non-executable dry proposals."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, HttpUrl, field_validator, model_validator
from yaml import YAMLError

from envresearch.replication._service_support import restore_proposal
from envresearch.replication.contracts import Tier2IntakeProposal

_STRICT_FROZEN = ConfigDict(
    extra="forbid", frozen=True, strict=True, validate_default=True
)
DryBlockerCode = Literal[
    "archive-direct-locator-unapproved",
    "archive-sha256-unobserved",
    "license-scope-unverified",
    "self-contained-status-unverified",
    "declared-input-inventory-incomplete",
    "expected-output-map-incomplete",
    "pinned-runtime-image-unapproved",
    "runtime-lock-compatibility-unverified",
]
_REQUIRED_DRY_BLOCKERS = frozenset(
    {
        "archive-direct-locator-unapproved",
        "archive-sha256-unobserved",
        "license-scope-unverified",
        "self-contained-status-unverified",
        "pinned-runtime-image-unapproved",
    }
)


def _canonical_text(value: str, field_name: str) -> str:
    if not value.strip() or value != value.strip():
        raise ValueError(f"{field_name} must be canonical nonblank text")
    return value


class DryTargetWork(BaseModel):
    """Verified public bibliographic identity without acquisition authority."""

    model_config = _STRICT_FROZEN

    title: str
    authors: tuple[str, ...]
    journal: str
    publication_year: int
    volume: int
    issue: int
    pages: str
    doi: str
    article_url: HttpUrl
    package_landing_url: HttpUrl

    @field_validator("title", "journal", "pages", "doi")
    @classmethod
    def require_canonical_text(cls, value: str) -> str:
        return _canonical_text(value, "target-work metadata")

    @field_validator("authors")
    @classmethod
    def require_authors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        canonical = tuple(_canonical_text(item, "author") for item in value)
        if not canonical or len(canonical) != len(set(canonical)):
            raise ValueError("target-work authors must be nonempty and unique")
        return canonical

    @field_validator("publication_year", "volume", "issue")
    @classmethod
    def require_positive_numbers(cls, value: int) -> int:
        if value < 1:
            raise ValueError("publication metadata numbers must be positive")
        return value


class DryRuntimeRequirement(BaseModel):
    """Requested language profile, explicitly without an executable image claim."""

    model_config = _STRICT_FROZEN

    language: Literal["R"]
    profile_id: Literal["r-did-v1"]


class DryProposalBlocker(BaseModel):
    """One unresolved fact that prevents external admission or execution."""

    model_config = _STRICT_FROZEN

    code: DryBlockerCode
    detail: str

    @field_validator("detail")
    @classmethod
    def require_detail(cls, value: str) -> str:
        return _canonical_text(value, "dry-proposal blocker detail")


class Tier2DryProposal(BaseModel):
    """Public-metadata proposal that deliberately carries no execution authority."""

    model_config = _STRICT_FROZEN

    schema_version: Literal["tier2-dry-proposal-v1"]
    proposal_kind: Literal["dry"]
    package_id: str
    admission_status: Literal["proposed"]
    target_work: DryTargetWork
    runtime_requirement: DryRuntimeRequirement
    metadata_verified_on: date
    metadata_source_urls: tuple[HttpUrl, ...]
    unresolved_blockers: tuple[DryProposalBlocker, ...]

    @field_validator("package_id")
    @classmethod
    def require_package_id(cls, value: str) -> str:
        return _canonical_text(value, "dry proposal package_id")

    @field_validator("metadata_source_urls")
    @classmethod
    def require_unique_sources(cls, value: tuple[HttpUrl, ...]) -> tuple[HttpUrl, ...]:
        rendered = tuple(str(item) for item in value)
        if not rendered or len(rendered) != len(set(rendered)):
            raise ValueError(
                "dry proposal metadata sources must be nonempty and unique"
            )
        return value

    @model_validator(mode="after")
    def require_blocking_boundary(self) -> Tier2DryProposal:
        codes = tuple(item.code for item in self.unresolved_blockers)
        if len(codes) != len(set(codes)):
            raise ValueError("dry proposal blocker codes must be unique")
        missing = _REQUIRED_DRY_BLOCKERS - set(codes)
        if missing:
            raise ValueError("dry proposal must retain every admission blocker")
        return self


ReplicationProposal = Tier2DryProposal | Tier2IntakeProposal


def load_replication_proposal(path: Path) -> ReplicationProposal:
    """Load one strict proposal without writing artifacts or contacting its URLs."""
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, YAMLError) as error:
        raise ValueError(f"replication proposal could not be read: {error}") from error
    if not isinstance(payload, dict):
        raise TypeError("replication proposal must contain a YAML mapping")
    schema_version = payload.get("schema_version")
    try:
        encoded = json.dumps(payload, allow_nan=False)
        if schema_version == "tier2-dry-proposal-v1":
            return Tier2DryProposal.model_validate_json(encoded)
        if schema_version == "tier2-intake-v1":
            return restore_proposal(payload)
    except (TypeError, ValueError) as error:
        raise ValueError(f"replication proposal is invalid: {error}") from error
    raise ValueError("replication proposal has an unsupported schema_version")
