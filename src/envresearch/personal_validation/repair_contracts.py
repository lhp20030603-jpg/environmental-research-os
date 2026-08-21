"""Immutable repair and regression envelopes for Personal Validation."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    Field,
    field_validator,
    model_validator,
)

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation._strict import (
    STRICT,
    CaseKind,
    ReviewRole,
    SafeRelativePath,
    Sha256,
    StrictArtifactRef,
    model_payload,
    require_materialized_id,
    require_nonblank,
    require_sorted_unique_refs,
    require_sorted_unique_strings,
    strict_model_input,
)

CASE_ORDER = (
    "successful-end-to-end",
    "correct-stop",
    "data-method-incompatibility",
    "evidence-citation-challenge",
)


class ReviewPublicationBinding(BaseModel):
    model_config = STRICT
    role: ReviewRole
    publication_ref: StrictArtifactRef


RolePublicationBinding = Annotated[
    ReviewPublicationBinding, BeforeValidator(strict_model_input)
]


def require_role_publication_order(
    values: tuple[ReviewPublicationBinding, ...],
) -> None:
    if tuple(item.role for item in values) != ("scientific", "evidence", "synthesis"):
        raise ValueError("review bindings must use exact role publication order")
    if len({item.publication_ref for item in values}) != 3:
        raise ValueError("review bindings must reference three distinct publications")


def _identity(model: BaseModel, field: str) -> dict[str, object]:
    return model_payload(model, exclude=field)


def _content_ref_matches(
    reference: ArtifactRef, identity: str, model: BaseModel
) -> bool:
    return (
        reference.artifact_id == identity
        and reference.artifact_version == 1
        and reference.content_hash
        == hashlib.sha256(model.model_dump_json().encode()).hexdigest()
    )


class CanonicalReplacementBlob(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.replacement-blob.v1"]
    media_type: Literal["application/json", "text/plain"]
    utf8_content: str
    content_sha256: Sha256

    @model_validator(mode="after")
    def require_exact_content(self) -> CanonicalReplacementBlob:
        if (
            hashlib.sha256(self.utf8_content.encode()).hexdigest()
            != self.content_sha256
        ):
            raise ValueError("replacement digest does not bind exact UTF-8 content")
        return self


class ReplacementOperation(BaseModel):
    model_config = STRICT
    operation_kind: Literal["replace-canonical-file"]
    applicator_version: Literal["personal-replace-file-v1"]
    logical_target: SafeRelativePath
    target_ref: StrictArtifactRef
    before_sha256: Sha256
    replacement_blob_ref: StrictArtifactRef
    after_sha256: Sha256

    @model_validator(mode="after")
    def require_changed_bytes(self) -> ReplacementOperation:
        if self.before_sha256 == self.after_sha256:
            raise ValueError("replacement operation must change canonical bytes")
        return self


class RepairOperationsArtifact(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.repair-operations.v1"]
    operations_id: str
    failed_report_ref: StrictArtifactRef
    operations: tuple[
        Annotated[ReplacementOperation, BeforeValidator(strict_model_input)], ...
    ] = Field(min_length=1)

    def identity_payload(self) -> dict[str, object]:
        return _identity(self, "operations_id")

    @model_validator(mode="after")
    def require_canonical_identity(self) -> RepairOperationsArtifact:
        keys = tuple(item.logical_target for item in self.operations)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("repair operations must target unique canonical paths")
        require_materialized_id(
            self.operations_id, "personal-repair-operations-", self.identity_payload()
        )
        return self


class ProtectedScientificState(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.protected-scientific-state.v1"]
    protocol_ref: StrictArtifactRef
    case_ref: StrictArtifactRef
    input_snapshot_ref: StrictArtifactRef
    expected_behavior_ref: StrictArtifactRef
    estimand_sha256: Sha256
    source_authority_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)

    @field_validator("source_authority_refs")
    @classmethod
    def require_canonical_refs(
        cls, value: tuple[ArtifactRef, ...]
    ) -> tuple[ArtifactRef, ...]:
        return require_sorted_unique_refs(
            value, field="protected source authority refs"
        )


class RepairProposal(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.repair-proposal.v1"]
    proposal_id: str
    failed_attempt_ref: StrictArtifactRef
    failed_report_ref: StrictArtifactRef
    case_ref: StrictArtifactRef
    finding_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    operations_ref: StrictArtifactRef
    operations: Annotated[RepairOperationsArtifact, BeforeValidator(strict_model_input)]
    affected_target_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    expected_verification: tuple[str, ...] = Field(min_length=1)
    protected_state_ref: StrictArtifactRef
    protected_state: Annotated[
        ProtectedScientificState, BeforeValidator(strict_model_input)
    ]

    def identity_payload(self) -> dict[str, object]:
        return _identity(self, "proposal_id")

    @model_validator(mode="after")
    def require_canonical_identity(self) -> RepairProposal:
        require_sorted_unique_refs(self.finding_refs, field="repair finding refs")
        require_sorted_unique_refs(
            self.affected_target_refs, field="affected target refs"
        )
        require_sorted_unique_strings(
            self.expected_verification, field="expected verification"
        )
        if (
            self.operations.failed_report_ref != self.failed_report_ref
            or not _content_ref_matches(
                self.operations_ref, self.operations.operations_id, self.operations
            )
        ):
            raise ValueError("repair proposal does not bind its exact operations")
        if self.protected_state.case_ref != self.case_ref or not _content_ref_matches(
            self.protected_state_ref,
            self.protected_state_ref.artifact_id,
            self.protected_state,
        ):
            raise ValueError("repair proposal does not bind protected scientific state")
        require_materialized_id(
            self.proposal_id, "personal-repair-proposal-", self.identity_payload()
        )
        return self


class OwnerRepairDecision(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.owner-repair-decision.v1"]
    proposal_ref: StrictArtifactRef
    decision: Literal["approved", "rejected"]
    owner: str
    decided_at: AwareDatetime

    @field_validator("owner")
    @classmethod
    def require_owner(cls, value: str) -> str:
        return require_nonblank(value)


class RepairApproval(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.repair-approval.v1"]
    approval_id: str
    proposal_ref: StrictArtifactRef
    proposal: Annotated[RepairProposal, BeforeValidator(strict_model_input)]
    owner_decision_ref: StrictArtifactRef

    def identity_payload(self) -> dict[str, object]:
        return _identity(self, "approval_id")

    @model_validator(mode="after")
    def require_canonical_identity(self) -> RepairApproval:
        if not _content_ref_matches(
            self.proposal_ref, self.proposal.proposal_id, self.proposal
        ):
            raise ValueError("repair approval does not bind its exact proposal")
        require_materialized_id(
            self.approval_id, "personal-repair-approval-", self.identity_payload()
        )
        return self


class VerifiedFindingResolution(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.verified-finding-resolution.v1"]
    resolution_id: str
    finding_ref: StrictArtifactRef
    resolution: Literal["closed", "limited", "reopened"]
    successor_report_ref: StrictArtifactRef
    successor_evaluation_ref: StrictArtifactRef
    witness_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)

    def identity_payload(self) -> dict[str, object]:
        return _identity(self, "resolution_id")

    @model_validator(mode="after")
    def require_canonical_identity(self) -> VerifiedFindingResolution:
        require_sorted_unique_refs(self.witness_refs, field="resolution witness refs")
        require_materialized_id(
            self.resolution_id, "personal-finding-resolution-", self.identity_payload()
        )
        return self


class ProtocolRegressionCaseResult(BaseModel):
    model_config = STRICT
    kind: CaseKind
    case_ref: StrictArtifactRef
    attempt_ref: StrictArtifactRef
    report_ref: StrictArtifactRef
    evaluation_ref: StrictArtifactRef


class ProtocolRegressionReport(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.protocol-regression-report.v1"]
    regression_id: str
    protocol_ref: StrictArtifactRef
    session_ref: StrictArtifactRef
    case_results: tuple[
        Annotated[ProtocolRegressionCaseResult, BeforeValidator(strict_model_input)],
        Annotated[ProtocolRegressionCaseResult, BeforeValidator(strict_model_input)],
        Annotated[ProtocolRegressionCaseResult, BeforeValidator(strict_model_input)],
        Annotated[ProtocolRegressionCaseResult, BeforeValidator(strict_model_input)],
    ]

    def identity_payload(self) -> dict[str, object]:
        return _identity(self, "regression_id")

    @model_validator(mode="after")
    def require_canonical_identity(self) -> ProtocolRegressionReport:
        keys = tuple(item.case_ref for item in self.case_results)
        if len(keys) != len(set(keys)):
            raise ValueError("protocol regression must bind four distinct cases")
        if tuple(item.kind for item in self.case_results) != CASE_ORDER:
            raise ValueError("protocol regression must use exact four-case order")
        require_materialized_id(
            self.regression_id, "personal-protocol-regression-", self.identity_payload()
        )
        return self


class RepairClosure(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.repair-closure.v1"]
    closure_id: str
    before_attempt_ref: StrictArtifactRef
    before_report_ref: StrictArtifactRef
    approval_ref: StrictArtifactRef
    successor_attempt_ref: StrictArtifactRef
    successor_review_publication_refs: tuple[
        RolePublicationBinding, RolePublicationBinding, RolePublicationBinding
    ]
    successor_report_ref: StrictArtifactRef
    successor_evaluation_ref: StrictArtifactRef
    rerun_evidence_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    verified_resolution_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    new_finding_refs: tuple[StrictArtifactRef, ...]
    protocol_regression_ref: StrictArtifactRef

    def identity_payload(self) -> dict[str, object]:
        return _identity(self, "closure_id")

    @model_validator(mode="after")
    def require_canonical_identity(self) -> RepairClosure:
        if self.before_attempt_ref == self.successor_attempt_ref:
            raise ValueError(
                "repair closure requires a distinct, fully reviewed successor"
            )
        require_role_publication_order(self.successor_review_publication_refs)
        require_sorted_unique_refs(
            self.rerun_evidence_refs, field="rerun evidence refs"
        )
        require_sorted_unique_refs(
            self.verified_resolution_refs, field="verified resolution refs"
        )
        require_sorted_unique_refs(self.new_finding_refs, field="new finding refs")
        require_materialized_id(
            self.closure_id, "personal-repair-closure-", self.identity_payload()
        )
        return self


__all__ = [
    "CanonicalReplacementBlob",
    "OwnerRepairDecision",
    "ProtectedScientificState",
    "ProtocolRegressionCaseResult",
    "ProtocolRegressionReport",
    "RepairApproval",
    "RepairClosure",
    "RepairOperationsArtifact",
    "RepairProposal",
    "ReplacementOperation",
    "ReviewPublicationBinding",
    "VerifiedFindingResolution",
]
