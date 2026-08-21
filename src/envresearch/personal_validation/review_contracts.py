"""Immutable review DAG contracts and the pure advisory state reducer."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    model_validator,
)

from envresearch.personal_validation import _strict as _strict_contracts
from envresearch.personal_validation import repair_contracts as _repair_contracts
from envresearch.personal_validation._strict import (
    STRICT,
    CaseBehaviorObservation,
    CaseState,
    FindingDomain,
    ReviewRole,
    Sha256,
    StrictArtifactRef,
    canonical_json,
    content_ref_matches,
    model_payload,
    require_materialized_id,
    require_nonblank,
    require_sorted_unique_refs,
    strict_model_input,
)
from envresearch.personal_validation.errors import (
    derive_review_disagreements,
    require_authenticated_review_closure,
)
from envresearch.personal_validation.external_access import (
    ExternalAccessReceipt,
    ExternalAccessRequest,
)
from envresearch.personal_validation.repair_contracts import (
    RolePublicationBinding,
    require_role_publication_order,
)

AgentDispatchObservation = _strict_contracts.AgentDispatchObservation
AgentDispatchReceipt = _strict_contracts.AgentDispatchReceipt
ReviewAssignment = _strict_contracts.ReviewAssignment
ReviewPublicationBinding = _repair_contracts.ReviewPublicationBinding


class ExternalAccessRecord(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.external-access-record.v1"]
    review_ref: StrictArtifactRef
    request: Annotated[ExternalAccessRequest, BeforeValidator(strict_model_input)]
    receipt_ref: StrictArtifactRef
    finding_refs: tuple[StrictArtifactRef, ...]

    @model_validator(mode="after")
    def require_exact_findings(self) -> ExternalAccessRecord:
        require_sorted_unique_refs(
            self.finding_refs, field="external access finding refs"
        )
        if len(self.finding_refs) != len(self.request.local_finding_keys):
            raise ValueError(
                "external access finding refs do not resolve all local keys"
            )
        return self


class AuthenticatedExternalAccessRecord(BaseModel):
    """Registry reference paired with the exact reopened access record."""

    model_config = STRICT
    record_ref: StrictArtifactRef
    record: Annotated[ExternalAccessRecord, BeforeValidator(strict_model_input)]
    receipt: Annotated[ExternalAccessReceipt, BeforeValidator(strict_model_input)]

    @model_validator(mode="after")
    def require_exact_record(self) -> AuthenticatedExternalAccessRecord:
        if not content_ref_matches(
            self.record_ref, self.record_ref.artifact_id, self.record
        ):
            raise ValueError("external access record ref does not bind exact record")
        if (
            not content_ref_matches(
                self.record.receipt_ref,
                self.receipt.receipt_id,
                self.receipt,
            )
            or self.receipt.outcome != "success"
            or self.receipt.request != self.record.request
        ):
            raise ValueError("external access record lacks its successful receipt")
        return self


class AgentFindingResponse(BaseModel):
    model_config = STRICT
    local_finding_key: str
    domain: FindingDomain
    severity: Literal["minor", "important", "critical"]
    target_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    evidence_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    problem: str
    impact: str
    repair_proposal: str

    @model_validator(mode="after")
    def require_canonical_finding(self) -> AgentFindingResponse:
        require_nonblank(self.local_finding_key)
        require_sorted_unique_refs(self.target_refs, field="finding target refs")
        require_sorted_unique_refs(self.evidence_refs, field="finding evidence refs")
        for value in (self.problem, self.impact, self.repair_proposal):
            require_nonblank(value)
        return self


class AgentReviewResponse(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.agent-review-response.v1"]
    role: ReviewRole
    findings: tuple[
        Annotated[AgentFindingResponse, BeforeValidator(strict_model_input)], ...
    ]
    external_access_requests: tuple[
        Annotated[ExternalAccessRequest, BeforeValidator(strict_model_input)], ...
    ]
    completion_status: Literal["complete", "external-verification-pending"]

    @model_validator(mode="after")
    def require_canonical_response(self) -> AgentReviewResponse:
        keys = tuple(item.local_finding_key for item in self.findings)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("raw findings must use unique canonical local keys")
        assessments = tuple((item.domain, item.target_refs) for item in self.findings)
        if len(assessments) != len(set(assessments)):
            raise ValueError("raw findings duplicate one domain and target assessment")
        access_keys = tuple(
            (item.provider, item.operation, item.source_locator)
            for item in self.external_access_requests
        )
        if access_keys != tuple(sorted(access_keys)) or len(access_keys) != len(
            set(access_keys)
        ):
            raise ValueError("external access responses must be unique and canonical")
        if any(
            set(item.local_finding_keys) - set(keys)
            for item in self.external_access_requests
        ):
            raise ValueError("external access refers to an unknown local finding key")
        return self


class AgentReview(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.agent-review.v1"]
    review_id: str
    assignment_ref: StrictArtifactRef
    attempt_ref: StrictArtifactRef
    bundle_ref: StrictArtifactRef
    role: ReviewRole
    policy_sha256: Sha256
    dispatch_receipt_ref: StrictArtifactRef
    raw_response_sha256: Sha256
    response: Annotated[AgentReviewResponse, BeforeValidator(strict_model_input)]

    def identity_payload(self) -> dict[str, object]:
        return model_payload(self, exclude="review_id")

    @model_validator(mode="after")
    def require_role_and_identity(self) -> AgentReview:
        if self.role != self.response.role:
            raise ValueError("durable review role differs from raw response")
        response_digest = hashlib.sha256(
            canonical_json(self.response.model_dump(mode="json"))
        ).hexdigest()
        if self.raw_response_sha256 != response_digest:
            raise ValueError(
                "raw response digest does not bind canonical response bytes"
            )
        require_materialized_id(
            self.review_id, "personal-review-", self.identity_payload()
        )
        return self


class PersonalFinding(BaseModel):
    model_config = STRICT
    finding_id: str
    domain: FindingDomain
    severity: Literal["minor", "important", "critical"]
    target_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    evidence_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    problem: str
    impact: str
    repair_proposal: str
    source_review_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)

    def identity_payload(self) -> dict[str, object]:
        return model_payload(self, exclude="finding_id")

    @model_validator(mode="after")
    def require_canonical_identity(self) -> PersonalFinding:
        require_sorted_unique_refs(self.target_refs, field="finding target refs")
        require_sorted_unique_refs(self.evidence_refs, field="finding evidence refs")
        require_sorted_unique_refs(
            self.source_review_refs, field="finding source review refs"
        )
        for value in (self.problem, self.impact, self.repair_proposal):
            require_nonblank(value)
        require_materialized_id(
            self.finding_id, "personal-finding-", self.identity_payload()
        )
        return self


class ReviewPublication(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.review-publication.v1"]
    publication_id: str
    assignment_ref: StrictArtifactRef
    review_ref: StrictArtifactRef
    finding_refs: tuple[StrictArtifactRef, ...]
    external_access_record_refs: tuple[StrictArtifactRef, ...]

    def identity_payload(self) -> dict[str, object]:
        return model_payload(self, exclude="publication_id")

    @model_validator(mode="after")
    def require_canonical_identity(self) -> ReviewPublication:
        require_sorted_unique_refs(self.finding_refs, field="publication finding refs")
        require_sorted_unique_refs(
            self.external_access_record_refs, field="publication access refs"
        )
        require_materialized_id(
            self.publication_id, "personal-review-publication-", self.identity_payload()
        )
        return self


class CaseBehaviorEvaluation(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.case-behavior-evaluation.v1"]
    evaluation_id: str
    case_ref: StrictArtifactRef
    attempt_ref: StrictArtifactRef
    expected_behavior_ref: StrictArtifactRef
    target_ref: StrictArtifactRef
    inventory_ref: StrictArtifactRef
    verifier_version: Literal["personal-case-verifier-v1"]
    observations: tuple[
        Annotated[CaseBehaviorObservation, BeforeValidator(strict_model_input)], ...
    ] = Field(min_length=1)
    verdict: Literal["expected-behavior-observed", "behavior-deviation"]

    def identity_payload(self) -> dict[str, object]:
        return model_payload(self, exclude="evaluation_id")

    @model_validator(mode="after")
    def require_canonical_identity(self) -> CaseBehaviorEvaluation:
        keys = tuple(
            (
                item.observation_kind,
                item.exact_code or "",
                item.exact_finding_kind or "",
            )
            for item in self.observations
        )
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("case observations must be unique and canonical")
        require_materialized_id(
            self.evaluation_id, "personal-evaluation-", self.identity_payload()
        )
        return self


class PersonalValidationReport(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.validation-report.v1"]
    report_id: str
    attempt_ref: StrictArtifactRef
    evaluation_ref: StrictArtifactRef
    review_publication_refs: tuple[
        RolePublicationBinding, RolePublicationBinding, RolePublicationBinding
    ]
    finding_refs: tuple[StrictArtifactRef, ...]
    state: CaseState
    scope: Literal["personal-advisory-only"]
    blocks: tuple[()] = ()
    hidden_evaluation_status: Literal["not-run"]
    product_release_status: Literal["scientific_release_pending"]

    def identity_payload(self) -> dict[str, object]:
        return model_payload(self, exclude="report_id")

    @model_validator(mode="after")
    def require_canonical_identity(self) -> PersonalValidationReport:
        require_role_publication_order(self.review_publication_refs)
        require_sorted_unique_refs(self.finding_refs, field="report finding refs")
        require_materialized_id(
            self.report_id, "personal-report-", self.identity_payload()
        )
        return self


class AuthenticatedReviewPublication(BaseModel):
    """Reopened exact publication closure consumed by the pure reducer."""

    model_config = STRICT
    assignment: Annotated[ReviewAssignment, BeforeValidator(strict_model_input)]
    dispatch_receipt: Annotated[
        AgentDispatchReceipt, BeforeValidator(strict_model_input)
    ]
    publication: Annotated[ReviewPublication, BeforeValidator(strict_model_input)]
    review: Annotated[AgentReview, BeforeValidator(strict_model_input)]
    findings: tuple[
        Annotated[PersonalFinding, BeforeValidator(strict_model_input)], ...
    ]
    external_access_records: tuple[
        Annotated[
            AuthenticatedExternalAccessRecord, BeforeValidator(strict_model_input)
        ],
        ...,
    ]

    @model_validator(mode="after")
    def require_exact_closure(self) -> AuthenticatedReviewPublication:
        require_authenticated_review_closure(
            assignment=self.assignment,
            dispatch_receipt=self.dispatch_receipt,
            publication=self.publication,
            review=self.review,
            findings=self.findings,
            external_access_records=self.external_access_records,
        )
        return self


def reduce_case_state(
    evaluation: CaseBehaviorEvaluation,
    publications: tuple[AuthenticatedReviewPublication, ...],
) -> CaseState:
    """Reduce one immutable attempt without scores, closures, or compensation."""
    evaluation = CaseBehaviorEvaluation.model_validate(
        evaluation.model_dump(mode="python")
    )
    publications = tuple(
        AuthenticatedReviewPublication.model_validate(item.model_dump(mode="python"))
        for item in publications
    )
    if len(publications) != 3 or tuple(item.review.role for item in publications) != (
        "scientific",
        "evidence",
        "synthesis",
    ):
        raise ValueError(
            "reducer requires scientific, evidence, and synthesis publications"
        )
    if (
        len({item.review.attempt_ref for item in publications}) != 1
        or publications[0].review.attempt_ref != evaluation.attempt_ref
    ):
        raise ValueError(
            "review publications do not authenticate one evaluated attempt"
        )
    if any(
        finding.severity in {"important", "critical"}
        for item in publications
        for finding in item.findings
    ):
        return "needs-revision"
    if derive_review_disagreements(publications):
        return "review-required"
    if evaluation.verdict != "expected-behavior-observed" or any(
        item.review.response.completion_status != "complete" for item in publications
    ):
        return "review-required"
    return "personal-baseline-passed"
