"""Strict V0.4 release payload and stable V1.0 handoff contract."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, ValidationInfo, model_validator

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._audit_lineage import (
    ExactAnalysisRef,
    ExactArtifactRef,
    ExactOutputRef,
)
from envresearch.paper.audit_contracts import PaperAuditReport
from envresearch.paper.contracts import STRICT
from envresearch.paper.revision_contracts import DraftRevision


def release_id(
    audit_ref: ArtifactRef,
    revision_refs: tuple[ArtifactRef, ...] = (),
) -> str:
    """Derive identity from the exact audit and optional revision closure."""
    digest = hashlib.sha256(
        json.dumps(
            {
                "audit_ref": audit_ref.model_dump(mode="json"),
                "revision_refs": [
                    item.model_dump(mode="json") for item in revision_refs
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return f"paper-release-{digest}"


def _audit_input(value: object, info: ValidationInfo) -> PaperAuditReport:
    if isinstance(value, PaperAuditReport):
        return PaperAuditReport.model_validate(value.model_dump(mode="python"))
    if isinstance(value, dict) and info.mode == "json":
        return PaperAuditReport.model_validate_json(
            json.dumps(value, separators=(",", ":"), sort_keys=True)
        )
    return PaperAuditReport.model_validate(value)


def _revision_input(value: object, info: ValidationInfo) -> DraftRevision | None:
    if value is None:
        return None
    if isinstance(value, DraftRevision):
        return DraftRevision.model_validate(value.model_dump(mode="python"))
    if isinstance(value, dict) and info.mode == "json":
        return DraftRevision.model_validate_json(
            json.dumps(value, separators=(",", ":"), sort_keys=True)
        )
    return DraftRevision.model_validate(value)


def _revisions_input(value: object, info: ValidationInfo) -> tuple[DraftRevision, ...]:
    if not isinstance(value, (list, tuple)):
        return tuple(value)  # type: ignore[arg-type]
    return tuple(_revision_input(item, info) for item in value)  # type: ignore[misc]


class PaperReleaseCandidate(BaseModel):
    """Complete immutable clean-audit payload handed to V1.0."""

    model_config = STRICT

    schema_version: Literal["paper.release-candidate.v1"]
    release_id: str
    producer: Literal["paper-builder-release-v1"]
    audit_ref: ExactArtifactRef
    draft_ref: ExactArtifactRef
    map_ref: ExactArtifactRef
    ledger_ref: ExactArtifactRef
    citation_report_ref: ExactArtifactRef
    revision_ref: ExactArtifactRef | None = None
    revision: Annotated[DraftRevision | None, BeforeValidator(_revision_input)] = None
    revision_refs: tuple[ExactArtifactRef, ...] = ()
    revisions: Annotated[
        tuple[DraftRevision, ...], BeforeValidator(_revisions_input)
    ] = ()
    transitive_refs: tuple[ExactArtifactRef, ...] = Field(min_length=4)
    analysis_refs: tuple[ExactAnalysisRef, ...] = Field(min_length=1)
    output_refs: tuple[ExactOutputRef, ...] = Field(min_length=1)
    audit_report: Annotated[PaperAuditReport, BeforeValidator(_audit_input)]
    verdict: Literal["current-green"]

    @model_validator(mode="after")
    def require_clean_exact_chain(self) -> PaperReleaseCandidate:
        report = self.audit_report
        expected_hash = hashlib.sha256(report.model_dump_json().encode()).hexdigest()
        if (
            self.release_id != release_id(self.audit_ref, self.revision_refs)
            or self.audit_ref.artifact_id != report.audit_id
            or self.audit_ref.artifact_version != 1
            or self.audit_ref.content_hash != expected_hash
        ):
            raise ValueError("release audit reference identity is invalid")
        if report.verdict != "clean" or report.findings:
            raise ValueError("release requires one clean audit with no findings")
        if (
            self.draft_ref != report.draft_ref
            or self.map_ref != report.map_ref
            or self.ledger_ref != report.ledger_ref
            or self.citation_report_ref != report.citation_report_ref
            or self.transitive_refs != report.transitive_refs
            or self.analysis_refs != report.analysis_refs
            or self.output_refs != report.output_refs
        ):
            raise ValueError("release exact audit chain is incomplete or inconsistent")
        if (self.revision_ref is None) != (self.revision is None):
            raise ValueError("release revision reference and payload must be paired")
        if len(self.revision_refs) != len(self.revisions):
            raise ValueError("release revision ancestry pairs are incomplete")
        if self.revision_refs and (
            self.revision_ref != self.revision_refs[-1]
            or self.revision != self.revisions[-1]
            or self.revisions[0].predecessor_generation != 1
            or any(
                earlier.successor_ref != later.predecessor_ref
                for earlier, later in zip(self.revisions, self.revisions[1:])
            )
        ):
            raise ValueError("release revision ancestry is invalid")
        if not self.revision_refs and self.revision_ref is not None:
            raise ValueError("release terminal revision lacks ancestry")
        if self.revision is not None and (
            self.revision_ref is None
            or self.revision_ref.artifact_id != self.revision.revision_id
            or self.revision_ref.content_hash
            != hashlib.sha256(self.revision.model_dump_json().encode()).hexdigest()
            or self.revision.successor_ref != self.draft_ref
            or self.revision.successor_audit_ref != self.audit_ref
        ):
            raise ValueError("release revision closure is inconsistent")
        return self


__all__ = ["PaperReleaseCandidate", "release_id"]
