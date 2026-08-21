"""Strict source-claim contracts for blinded benchmark validation."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from envresearch.models.artifact import ArtifactRef

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_ZOTERO_KEY = re.compile(r"^[A-Z0-9]{8}$")
_DOI = re.compile(r"^10\.\d{4,9}/[-._;()/:a-z0-9]+$")
_JSON_POINTER = re.compile(r"^(?:/(?:[^~/]|~[01])*)*$")


class _StrictModel(BaseModel):
    """Base contract for immutable, exact persisted benchmark data."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _parse_serialized_tuple(value: object) -> object:
    """Accept YAML/JSON arrays while retaining strict scalar validation."""
    if isinstance(value, list):
        return tuple(value)
    return value


SerializedStringTuple = Annotated[
    tuple[str, ...], BeforeValidator(_parse_serialized_tuple)
]


def _require_nonblank(value: str, field_name: str) -> str:
    """Reject blank and noncanonical human-readable persisted strings."""
    if not value.strip() or value != value.strip():
        raise ValueError(f"{field_name} must be a canonical nonblank value")
    return value


def _require_canonical_id(value: str, field_name: str) -> str:
    """Keep durable case, claim, and fact identifiers unambiguous."""
    if not _CANONICAL_ID.fullmatch(value):
        raise ValueError(f"{field_name} must be a canonical lowercase identifier")
    return value


def _require_sha256(value: str, field_name: str) -> str:
    """Require a lowercase SHA-256 digest at every evidence boundary."""
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a 64-character lowercase SHA-256")
    return value


def _require_unique_strings(
    value: tuple[str, ...], field_name: str, *, require_items: bool = False
) -> tuple[str, ...]:
    """Require persisted vocabulary lists to be reviewable and deterministic."""
    if require_items and not value:
        raise ValueError(f"{field_name} must contain at least one item")
    for item in value:
        _require_nonblank(item, field_name)
    if len(set(value)) != len(value):
        raise ValueError(f"{field_name} must not contain duplicate values")
    return value


class ClaimVerificationStatus(StrEnum):
    """The review state of one normalized source claim."""

    UNVERIFIED = "unverified"
    METADATA_VERIFIED = "metadata_verified"
    CLAIM_VERIFIED = "claim_verified"
    REJECTED = "rejected"
    STALE = "stale"


class SourceLocator(_StrictModel):
    """A human-reviewable location for evidence in an attached source."""

    page: StrictInt | None = Field(default=None, ge=1)
    section: str | None = None
    table: str | None = None
    paragraph: StrictInt | None = Field(default=None, ge=1)

    @field_validator("section", "table")
    @classmethod
    def require_nonblank_optional_text(cls, value: str | None) -> str | None:
        """Prevent a blank locator component from masquerading as evidence."""
        if value is not None:
            return _require_nonblank(value, "locator")
        return value

    @model_validator(mode="after")
    def require_reviewable_location(self) -> SourceLocator:
        """Ensure a reviewer can locate the cited evidence independently."""
        if all(
            value is None
            for value in (self.page, self.section, self.table, self.paragraph)
        ):
            raise ValueError(
                "locator must identify a page, section, table, or paragraph"
            )
        return self


class RestrictedTerm(_StrictModel):
    """A source-specific phrase that must not enter a blinded brief."""

    term: str
    rationale: str

    @field_validator("term", "rationale")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        """Keep leakage restrictions explicit and auditable."""
        return _require_nonblank(value, "restricted term")


class VerifiedClaim(_StrictModel):
    """One source-grounded claim with independent verification provenance."""

    claim_id: str
    normalized_claim: str
    source_item_key: str
    source_attachment_key: str
    source_content_hash: str
    locator: SourceLocator
    supporting_passage_hash: str
    status: ClaimVerificationStatus
    extractor_principal: str
    verifier_principal: str | None = None
    verified_at: datetime | None = None

    @field_validator("claim_id")
    @classmethod
    def require_claim_id(cls, value: str) -> str:
        """Use a stable identifier to connect claims to blind facts and citations."""
        return _require_canonical_id(value, "claim_id")

    @field_validator("normalized_claim", "extractor_principal")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        """Keep claims and responsible principals reviewable."""
        return _require_nonblank(value, "claim field")

    @field_validator("source_item_key", "source_attachment_key")
    @classmethod
    def require_zotero_key(cls, value: str) -> str:
        """Require canonical Zotero item and attachment identities."""
        if not _ZOTERO_KEY.fullmatch(value):
            raise ValueError(
                "Zotero keys must be 8-character uppercase alphanumeric values"
            )
        return value

    @field_validator("source_content_hash", "supporting_passage_hash")
    @classmethod
    def require_sha256(cls, value: str, info: object) -> str:
        """Bind both the source and its quoted passage to immutable content."""
        return _require_sha256(value, getattr(info, "field_name", "hash"))

    @field_validator("verifier_principal")
    @classmethod
    def require_optional_principal(cls, value: str | None) -> str | None:
        """Reject blank verifier identities before status validation."""
        if value is not None:
            return _require_nonblank(value, "verifier_principal")
        return value

    @field_validator("verified_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime | None) -> datetime | None:
        """Require a canonical review time when a review has occurred."""
        if value is not None and (
            value.utcoffset() != timedelta(0) or value.tzname() != "UTC"
        ):
            raise ValueError("timestamps must be UTC-aware")
        return value

    @model_validator(mode="after")
    def require_status_consistent_verification(self) -> VerifiedClaim:
        """Allow claim support only after an independent, timestamped review."""
        if self.status is ClaimVerificationStatus.CLAIM_VERIFIED:
            if self.verifier_principal is None or self.verified_at is None:
                raise ValueError(
                    "claim_verified status requires verifier_principal and verified_at"
                )
            if self.extractor_principal == self.verifier_principal:
                raise ValueError("extractor and verifier must differ")
        elif self.verifier_principal is not None or self.verified_at is not None:
            raise ValueError(
                "only claim_verified status may declare verifier_principal or verified_at"
            )
        return self


class ClaimUsage(_StrictModel):
    """A claim citation attached to one exact field in an accepted artifact."""

    claim_id: str
    statement_sha256: str
    json_pointer: str

    @field_validator("claim_id")
    @classmethod
    def require_claim_id(cls, value: str) -> str:
        """Keep every citation traceable to a stable verified claim identifier."""
        return _require_canonical_id(value, "claim_id")

    @field_validator("statement_sha256")
    @classmethod
    def require_statement_hash(cls, value: str) -> str:
        """Bind a citation to the exact serialized statement it supports."""
        return _require_sha256(value, "statement_sha256")

    @field_validator("json_pointer")
    @classmethod
    def require_json_pointer(cls, value: str) -> str:
        """Require RFC 6901 syntax for exact accepted-artifact locations."""
        if not _JSON_POINTER.fullmatch(value):
            raise ValueError("json_pointer must be an RFC 6901 pointer")
        return value


class ClaimFactMappingEntry(_StrictModel):
    """The one-to-one provenance link from a source claim to a blind fact."""

    claim_id: str
    fact_id: str

    @field_validator("claim_id", "fact_id")
    @classmethod
    def require_mapping_id(cls, value: str, info: object) -> str:
        """Keep mappings stable across independently generated artifacts."""
        return _require_canonical_id(value, getattr(info, "field_name", "mapping_id"))


class ClaimFactMap(_StrictModel):
    """A versioned mapping from verified source claims to blinded brief facts."""

    case_id: str
    source_sheet_ref: ArtifactRef
    blinded_brief_ref: ArtifactRef
    entries: tuple[ClaimFactMappingEntry, ...]
    mapper_principal: str

    @field_validator("case_id")
    @classmethod
    def require_case_id(cls, value: str) -> str:
        """Bind this map to one canonical benchmark case."""
        return _require_canonical_id(value, "case_id")

    @field_validator("mapper_principal")
    @classmethod
    def require_mapper_principal(cls, value: str) -> str:
        """Require an accountable principal for each fact-mapping decision."""
        return _require_nonblank(value, "mapper_principal")

    @model_validator(mode="after")
    def require_unique_mappings(self) -> ClaimFactMap:
        """Prevent ambiguous claim-to-fact provenance links."""
        claim_ids = tuple(entry.claim_id for entry in self.entries)
        fact_ids = tuple(entry.fact_id for entry in self.entries)
        if len(claim_ids) != len(set(claim_ids)) or len(fact_ids) != len(set(fact_ids)):
            raise ValueError("entries must not contain duplicate claim or fact IDs")
        return self


class CuratorSourceSheet(_StrictModel):
    """The immutable curator-facing evidence sheet for one blinded case."""

    schema_version: Literal["1.0"] = "1.0"
    case_id: str
    method_family: str
    zotero_item_key: str
    zotero_attachment_key: str
    doi: str
    title: str
    authors: SerializedStringTuple
    source_content_hash: str
    source_generation: StrictInt = Field(ge=1)
    institutional_context: SerializedStringTuple
    restricted_terms: tuple[RestrictedTerm, ...]
    distinctive_phrase_hashes: SerializedStringTuple
    claims: tuple[VerifiedClaim, ...]

    @field_validator("case_id")
    @classmethod
    def require_case_id(cls, value: str) -> str:
        """Use one stable case identity across all blinded artifacts."""
        return _require_canonical_id(value, "case_id")

    @field_validator("method_family", "title")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        """Keep source classification and title auditably meaningful."""
        return _require_nonblank(value, "source sheet field")

    @field_validator("zotero_item_key", "zotero_attachment_key")
    @classmethod
    def require_zotero_key(cls, value: str) -> str:
        """Require the exact Zotero identities used by every contained claim."""
        if not _ZOTERO_KEY.fullmatch(value):
            raise ValueError(
                "Zotero keys must be 8-character uppercase alphanumeric values"
            )
        return value

    @field_validator("doi")
    @classmethod
    def require_doi(cls, value: str) -> str:
        """Require a canonical lowercase DOI rather than a presentation URL."""
        if not _DOI.fullmatch(value):
            raise ValueError("doi must be a canonical lowercase DOI")
        return value

    @field_validator("source_content_hash")
    @classmethod
    def require_source_hash(cls, value: str) -> str:
        """Bind the sheet to its one immutable source attachment generation."""
        return _require_sha256(value, "source_content_hash")

    @field_validator("authors", "institutional_context")
    @classmethod
    def require_unique_text(
        cls, value: tuple[str, ...], info: object
    ) -> tuple[str, ...]:
        """Keep identity and contextual facts deterministic and reviewable."""
        return _require_unique_strings(
            value,
            getattr(info, "field_name", "source sheet values"),
            require_items=True,
        )

    @field_validator("distinctive_phrase_hashes")
    @classmethod
    def require_phrase_hashes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require unique canonical hashes for source phrases checked for leakage."""
        if len(value) != len(set(value)):
            raise ValueError(
                "distinctive_phrase_hashes must not contain duplicate values"
            )
        for item in value:
            _require_sha256(item, "distinctive_phrase_hashes")
        return value

    @field_validator("restricted_terms")
    @classmethod
    def require_unique_restricted_terms(
        cls, value: tuple[RestrictedTerm, ...]
    ) -> tuple[RestrictedTerm, ...]:
        """Prevent duplicate source-leakage rules with ambiguous rationales."""
        terms = tuple(item.term.casefold() for item in value)
        if len(terms) != len(set(terms)):
            raise ValueError("restricted_terms must not contain duplicate terms")
        return value

    @model_validator(mode="after")
    def require_claim_source_identity(self) -> CuratorSourceSheet:
        """Reject a sheet that mixes claims from separate source attachments."""
        claim_ids = tuple(claim.claim_id for claim in self.claims)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claims must not contain duplicate claim IDs")
        for claim in self.claims:
            if (
                claim.source_item_key != self.zotero_item_key
                or claim.source_attachment_key != self.zotero_attachment_key
                or claim.source_content_hash != self.source_content_hash
            ):
                raise ValueError("claim source identity mismatch")
        return self
