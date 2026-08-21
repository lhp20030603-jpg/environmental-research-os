"""Frozen contracts for independent V0.4 paper audit artifacts."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from envresearch.econometrics.report import LocalAnalysisReference
from envresearch.models.artifact import ArtifactRef
from envresearch.paper._audit_lineage import (
    ExactAnalysisRef,
    ExactArtifactRef,
    ExactOutputRef,
    analysis_ref_key,
    artifact_ref_key,
    output_ref_key,
    require_lineage_role,
    strict_model_input,
)
from envresearch.paper._audit_types import KIND_CODES, AuditCode, FindingKind
from envresearch.paper.contracts import CANONICAL_ID, STRICT, AnalysisOutputRef

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _exact_finding_input(value: object, info: ValidationInfo) -> object:
    if isinstance(value, PaperAuditFinding):
        return value.model_dump(mode="python")
    if isinstance(value, dict) and info.mode == "json":
        value = dict(value)
        for field in ("claim_ids", "upstream_refs", "analysis_refs", "output_refs"):
            items = value.get(field)
            if isinstance(items, list):
                value[field] = tuple(items)
    return value


class TextSpan(BaseModel):
    """One exact immutable text target in the audited draft."""

    model_config = STRICT

    target_type: Literal["text-span"]
    paragraph_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text_sha256: str

    @field_validator("paragraph_id")
    @classmethod
    def require_paragraph_id(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("audit paragraph id must be canonical")
        return value

    @field_validator("text_sha256")
    @classmethod
    def require_digest(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("audit text span digest must be canonical SHA-256")
        return value

    @model_validator(mode="after")
    def require_positive_span(self) -> TextSpan:
        if self.end <= self.start:
            raise ValueError("audit span end must follow start")
        return self


class OutputBindingTarget(BaseModel):
    """One exact table or figure binding target in the audited draft."""

    model_config = STRICT

    target_type: Literal["output-binding"]
    kind: Literal["table", "figure"]
    binding_id: str

    @field_validator("binding_id")
    @classmethod
    def require_binding_id(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("audit output binding id must be canonical")
        return value


class DraftBindingTarget(BaseModel):
    """One exact invalid claim or citation binding without a valid text span."""

    model_config = STRICT

    target_type: Literal["claim-binding", "citation-binding"]
    paragraph_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    binding_sha256: str

    @field_validator("paragraph_id")
    @classmethod
    def require_paragraph_id(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("audit binding paragraph id must be canonical")
        return value

    @field_validator("binding_sha256")
    @classmethod
    def require_binding_digest(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("audit binding digest must be canonical SHA-256")
        return value

    @model_validator(mode="after")
    def require_positive_span(self) -> DraftBindingTarget:
        if self.end <= self.start:
            raise ValueError("audit binding end must follow start")
        return self


AuditTarget = Annotated[
    TextSpan | OutputBindingTarget | DraftBindingTarget,
    BeforeValidator(strict_model_input),
    Field(discriminator="target_type"),
]


class PaperAuditFinding(BaseModel):
    """One deterministic finding bound to an exact draft and evidence set."""

    model_config = STRICT

    finding_id: str
    finding_kind: FindingKind
    code: AuditCode
    draft_ref: ExactArtifactRef
    target: AuditTarget
    claim_ids: tuple[str, ...] = Field(min_length=1)
    upstream_refs: tuple[ExactArtifactRef, ...] = Field(min_length=2)
    analysis_refs: tuple[ExactAnalysisRef, ...]
    output_refs: tuple[ExactOutputRef, ...]

    @field_validator("finding_id")
    @classmethod
    def require_finding_id(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("audit finding id must be canonical")
        return value

    @field_validator("claim_ids")
    @classmethod
    def require_claim_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if (
            value != tuple(sorted(value))
            or len(value) != len(set(value))
            or any(not CANONICAL_ID.fullmatch(item) for item in value)
        ):
            raise ValueError("audit claim ids must be unique and canonical")
        return value

    @field_validator("upstream_refs")
    @classmethod
    def require_upstream_refs(
        cls, value: tuple[ArtifactRef, ...]
    ) -> tuple[ArtifactRef, ...]:
        if value != tuple(sorted(value, key=artifact_ref_key)) or len(value) != len(
            set(value)
        ):
            raise ValueError("audit upstream refs must be unique and canonical")
        return value

    @field_validator("analysis_refs")
    @classmethod
    def require_analysis_refs(
        cls, value: tuple[LocalAnalysisReference, ...]
    ) -> tuple[LocalAnalysisReference, ...]:
        if value != tuple(sorted(value, key=analysis_ref_key)) or len(value) != len(
            set(value)
        ):
            raise ValueError("finding analysis refs must be unique and canonical")
        return value

    @field_validator("output_refs")
    @classmethod
    def require_output_refs(
        cls, value: tuple[AnalysisOutputRef, ...]
    ) -> tuple[AnalysisOutputRef, ...]:
        if value != tuple(sorted(value, key=output_ref_key)) or len(value) != len(
            set(value)
        ):
            raise ValueError("finding output refs must be unique and canonical")
        return value

    @model_validator(mode="after")
    def require_coherent_finding(self) -> PaperAuditFinding:
        if self.code != KIND_CODES[self.finding_kind]:
            raise ValueError("audit finding kind and stable code disagree")
        if self.draft_ref not in self.upstream_refs:
            raise ValueError("audit finding must bind its exact draft ref")
        if any(
            item.analysis_ref not in self.analysis_refs for item in self.output_refs
        ):
            raise ValueError("finding output refs must bind registered analysis refs")
        return self


class PaperAuditReport(BaseModel):
    """One immutable complete audit over exact current paper authorities."""

    model_config = STRICT

    schema_version: Literal["paper.audit-report.v1"]
    audit_id: str
    producer: Literal["paper-builder-auditor-v1"]
    draft_ref: ExactArtifactRef
    map_ref: ExactArtifactRef
    ledger_ref: ExactArtifactRef
    citation_report_ref: ExactArtifactRef
    transitive_refs: tuple[ExactArtifactRef, ...] = Field(min_length=4)
    transition_refs: tuple[ExactArtifactRef, ...] = Field(min_length=1)
    snapshot_refs: tuple[ExactArtifactRef, ...] = Field(min_length=1)
    citation_source_refs: tuple[ExactArtifactRef, ...] = Field(min_length=1)
    claim_fact_map_refs: tuple[ExactArtifactRef, ...] = Field(min_length=1)
    blinded_brief_refs: tuple[ExactArtifactRef, ...] = Field(min_length=1)
    accepted_artifact_refs: tuple[ExactArtifactRef, ...] = Field(min_length=1)
    analysis_refs: tuple[ExactAnalysisRef, ...] = Field(min_length=1)
    output_refs: tuple[ExactOutputRef, ...] = Field(min_length=1)
    findings: tuple[
        Annotated[PaperAuditFinding, BeforeValidator(_exact_finding_input)], ...
    ]
    verdict: Literal["clean", "blocked"]

    @field_validator("audit_id")
    @classmethod
    def require_audit_id(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("paper audit id must be canonical")
        return value

    @field_validator(
        "transitive_refs",
        "transition_refs",
        "snapshot_refs",
        "citation_source_refs",
        "claim_fact_map_refs",
        "blinded_brief_refs",
        "accepted_artifact_refs",
    )
    @classmethod
    def require_transitive_refs(
        cls, value: tuple[ArtifactRef, ...], info: ValidationInfo
    ) -> tuple[ArtifactRef, ...]:
        if value != tuple(sorted(value, key=artifact_ref_key)) or len(value) != len(
            set(value)
        ):
            raise ValueError("audit lineage refs must be unique and canonical")
        if info.field_name != "transitive_refs":
            if info.field_name is None:
                raise ValueError("audit lineage role is missing")
            require_lineage_role(info.field_name, value)
        return value

    @model_validator(mode="after")
    def require_complete_report(self) -> PaperAuditReport:
        expected_verdict = "blocked" if self.findings else "clean"
        if self.verdict != expected_verdict:
            raise ValueError("audit verdict and findings disagree")
        if self.findings != tuple(
            sorted(self.findings, key=lambda item: item.finding_id)
        ):
            raise ValueError("audit findings must use canonical order")
        finding_ids = tuple(item.finding_id for item in self.findings)
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("audit finding ids must be unique")
        required = {
            self.draft_ref,
            self.map_ref,
            self.ledger_ref,
            self.citation_report_ref,
        }
        available = set(self.transitive_refs)
        lineage = {
            *self.transition_refs,
            *self.snapshot_refs,
            *self.citation_source_refs,
            *self.claim_fact_map_refs,
            *self.blinded_brief_refs,
            *self.accepted_artifact_refs,
        }
        primary_roles = required
        role_groups = (
            set(self.transition_refs),
            set(self.snapshot_refs),
            set(self.citation_source_refs),
            set(self.claim_fact_map_refs),
            set(self.blinded_brief_refs),
            set(self.accepted_artifact_refs),
        )
        if any(group & primary_roles for group in role_groups):
            raise ValueError("audit lineage roles cannot reuse primary refs")
        if any(
            left & right
            for index, left in enumerate(role_groups)
            for right in role_groups[index + 1 :]
        ):
            raise ValueError("audit lineage roles must be disjoint")
        if available != required | lineage:
            raise ValueError("audit report lineage is incomplete or untyped")
        for finding in self.findings:
            if (
                finding.draft_ref != self.draft_ref
                or set(finding.upstream_refs) != available
            ):
                raise ValueError("audit finding authority does not match its report")
            if not set(finding.analysis_refs).issubset(self.analysis_refs) or not set(
                finding.output_refs
            ).issubset(self.output_refs):
                raise ValueError("audit finding exact evidence is absent from report")
        if any(
            item.analysis_ref not in self.analysis_refs for item in self.output_refs
        ):
            raise ValueError("report output refs must bind registered analysis refs")
        return self

    @field_validator("analysis_refs")
    @classmethod
    def require_report_analysis_refs(
        cls, value: tuple[LocalAnalysisReference, ...]
    ) -> tuple[LocalAnalysisReference, ...]:
        if value != tuple(sorted(value, key=analysis_ref_key)) or len(value) != len(
            set(value)
        ):
            raise ValueError("report analysis refs must be unique and canonical")
        return value

    @field_validator("output_refs")
    @classmethod
    def require_report_output_refs(
        cls, value: tuple[AnalysisOutputRef, ...]
    ) -> tuple[AnalysisOutputRef, ...]:
        if value != tuple(sorted(value, key=output_ref_key)) or len(value) != len(
            set(value)
        ):
            raise ValueError("report output refs must be unique and canonical")
        return value


__all__ = [
    "AuditCode",
    "AuditTarget",
    "DraftBindingTarget",
    "ExactArtifactRef",
    "FindingKind",
    "OutputBindingTarget",
    "PaperAuditFinding",
    "PaperAuditReport",
    "TextSpan",
    "analysis_ref_key",
    "artifact_ref_key",
    "output_ref_key",
]
