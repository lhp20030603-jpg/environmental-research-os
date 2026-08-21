"""Frozen service-owned closure envelope for one paper draft revision."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._audit_lineage import ExactArtifactRef
from envresearch.paper._audit_store import audit_id
from envresearch.paper._audit_types import KIND_CODES, AuditCode, FindingKind
from envresearch.paper.audit_contracts import AuditTarget
from envresearch.paper.contracts import CANONICAL_ID, STRICT


def _exact_witness_input(value: object, info: ValidationInfo) -> object:
    if isinstance(value, FindingClosureWitness):
        return value.model_dump(mode="python")
    if isinstance(value, dict) and info.mode == "json":
        value = dict(value)
        claim_ids = value.get("claim_ids")
        if isinstance(claim_ids, list):
            value["claim_ids"] = tuple(claim_ids)
    return value


def revision_id(predecessor_ref: ArtifactRef) -> str:
    """Derive an exact collision-resistant identity from the predecessor ref."""
    digest = hashlib.sha256(
        json.dumps(
            predecessor_ref.model_dump(mode="json"),
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return f"paper-revision-{digest}"


class FindingClosureWitness(BaseModel):
    """Typed predecessor finding identity closed by one clean successor audit."""

    model_config = STRICT

    finding_id: str
    finding_kind: FindingKind
    code: AuditCode
    predecessor_target: AuditTarget
    claim_ids: tuple[str, ...] = Field(min_length=1)
    successor_validation: Literal["clean-independent-audit"]

    @field_validator("finding_id")
    @classmethod
    def require_finding_id(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("closure finding id must be canonical")
        return value

    @field_validator("claim_ids")
    @classmethod
    def require_claim_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if (
            value != tuple(sorted(value))
            or len(value) != len(set(value))
            or any(not CANONICAL_ID.fullmatch(item) for item in value)
        ):
            raise ValueError("closure claim ids must be unique and canonical")
        return value

    @model_validator(mode="after")
    def require_kind_code(self) -> FindingClosureWitness:
        if self.code != KIND_CODES[self.finding_kind]:
            raise ValueError("closure finding kind and code disagree")
        return self


class DraftRevision(BaseModel):
    """Complete immutable service-derived closure of a blocked predecessor."""

    model_config = STRICT

    schema_version: Literal["paper.draft-revision.v1"]
    revision_id: str
    producer: Literal["paper-builder-revision-v1"]
    predecessor_ref: ExactArtifactRef
    predecessor_audit_ref: ExactArtifactRef
    successor_ref: ExactArtifactRef
    successor_audit_ref: ExactArtifactRef
    predecessor_generation: int = Field(ge=1)
    successor_generation: int = Field(ge=2)
    map_ref: ExactArtifactRef
    ledger_ref: ExactArtifactRef
    citation_report_ref: ExactArtifactRef
    closed_finding_ids: tuple[str, ...] = Field(min_length=1)
    closure_witnesses: tuple[
        Annotated[FindingClosureWitness, BeforeValidator(_exact_witness_input)], ...
    ] = Field(min_length=1)

    @field_validator("revision_id")
    @classmethod
    def require_revision_id(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("paper revision id must be canonical")
        return value

    @field_validator("closed_finding_ids")
    @classmethod
    def require_closed_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if (
            value != tuple(sorted(value))
            or len(value) != len(set(value))
            or any(not CANONICAL_ID.fullmatch(item) for item in value)
        ):
            raise ValueError("closed finding ids must be unique and canonical")
        return value

    @model_validator(mode="after")
    def require_coherent_revision(self) -> DraftRevision:
        if self.revision_id != revision_id(self.predecessor_ref):
            raise ValueError("paper revision identity does not match predecessor")
        if (
            self.successor_generation != self.predecessor_generation + 1
            or self.predecessor_ref.artifact_version != self.predecessor_generation
            or self.successor_ref.artifact_version != self.successor_generation
            or self.predecessor_ref.artifact_id != self.successor_ref.artifact_id
            or self.predecessor_ref.content_hash == self.successor_ref.content_hash
        ):
            raise ValueError("paper revision draft generation chain is invalid")
        if (
            self.predecessor_audit_ref.artifact_version != 1
            or self.successor_audit_ref.artifact_version != 1
            or self.predecessor_audit_ref.artifact_id != audit_id(self.predecessor_ref)
            or self.successor_audit_ref.artifact_id != audit_id(self.successor_ref)
        ):
            raise ValueError("paper revision audit references are invalid")
        if (
            not self.map_ref.artifact_id.startswith("argument-map-")
            or not self.ledger_ref.artifact_id.startswith("valuation-core-")
            or self.citation_report_ref.artifact_id != "citation-integrity-report"
        ):
            raise ValueError("paper revision upstream roles are invalid")
        witness_ids = tuple(item.finding_id for item in self.closure_witnesses)
        if witness_ids != self.closed_finding_ids or self.closure_witnesses != tuple(
            sorted(self.closure_witnesses, key=lambda item: item.finding_id)
        ):
            raise ValueError("paper revision witnesses do not match closed findings")
        return self


__all__ = ["DraftRevision", "FindingClosureWitness", "revision_id"]
