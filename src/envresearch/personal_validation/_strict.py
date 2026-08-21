"""Shared strict canonical primitives for Personal Validation contracts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic_core import to_jsonable_python

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._audit_lineage import strict_model_input as _model_input

STRICT = ConfigDict(
    extra="forbid", frozen=True, strict=True, revalidate_instances="always"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def require_sha256(value: str) -> str:
    if not _SHA256.fullmatch(value):
        raise ValueError("digest must be a full lowercase SHA-256")
    return value


Sha256 = Annotated[str, AfterValidator(require_sha256)]


def strict_model_input(value: object) -> object:
    return _model_input(value)


def strict_artifact_input(value: object) -> object:
    if isinstance(value, ArtifactRef):
        value = {
            "artifact_id": value.artifact_id,
            "artifact_version": value.artifact_version,
            "content_hash": value.content_hash,
        }
    fields = ("artifact_id", "artifact_version", "content_hash")
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("strict artifact ref input is malformed")
    if any(
        type(value[field]) is not expected
        for field, expected in zip(fields, (str, int, str), strict=True)
    ):
        raise ValueError("strict artifact ref fields cannot be coerced")
    return value


StrictArtifactRef = Annotated[ArtifactRef, BeforeValidator(strict_artifact_input)]
CaseKind: TypeAlias = Literal[
    "successful-end-to-end",
    "correct-stop",
    "data-method-incompatibility",
    "evidence-citation-challenge",
]
ObservationKind: TypeAlias = Literal[
    "factory-chain-coherent",
    "correct-stop-blocker",
    "method-rejected",
    "compatible-method-retained",
    "predecessor-audit-blocked",
    "revision-closure-complete",
    "successor-release-clean",
    "namespace-absent",
]
FindingDomain: TypeAlias = Literal[
    "research-question-estimand",
    "method-identification",
    "data-compatibility",
    "assumptions-threats",
    "diagnostics-robustness",
    "evidence-numbers-citations",
    "paper-usefulness",
]
ReviewRole: TypeAlias = Literal["scientific", "evidence", "synthesis"]
CaseState: TypeAlias = Literal[
    "personal-baseline-passed", "review-required", "needs-revision"
]


def artifact_ref_key(reference: ArtifactRef) -> tuple[str, int, str]:
    return (
        reference.artifact_id,
        reference.artifact_version,
        reference.content_hash,
    )


def canonical_json(payload: object) -> bytes:
    payload = to_jsonable_python(payload)
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()


def materialize_id(prefix: str, identity_payload: object) -> str:
    return f"{prefix}{hashlib.sha256(canonical_json(identity_payload)).hexdigest()}"


def require_materialized_id(
    supplied: str, prefix: str, identity_payload: object
) -> None:
    if supplied != materialize_id(prefix, identity_payload):
        raise ValueError(f"{prefix} identity does not bind its complete payload")


def model_payload(model: BaseModel, *, exclude: str) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude={exclude})


def content_ref_matches(
    reference: ArtifactRef, identity: str, model: BaseModel
) -> bool:
    return (
        reference.artifact_id == identity
        and reference.artifact_version == 1
        and reference.content_hash
        == hashlib.sha256(model.model_dump_json().encode()).hexdigest()
    )


def require_nonblank(value: str) -> str:
    if not value or value != value.strip():
        raise ValueError("value must be canonical and nonblank")
    return value


def require_sorted_unique_strings(
    values: tuple[str, ...], *, field: str
) -> tuple[str, ...]:
    if (
        values != tuple(sorted(values))
        or len(values) != len(set(values))
        or any(not value or value != value.strip() for value in values)
    ):
        raise ValueError(f"{field} must be unique, nonblank, and canonically sorted")
    return values


def require_sorted_unique_refs(
    values: tuple[ArtifactRef, ...], *, field: str
) -> tuple[ArtifactRef, ...]:
    if values != tuple(sorted(values, key=artifact_ref_key)) or len(values) != len(
        set(values)
    ):
        raise ValueError(f"{field} must be unique and canonically sorted")
    return values


def require_safe_relative_posix_path(value: str) -> str:
    if (
        not value
        or value != value.strip()
        or "\\" in value
        or PurePosixPath(value).is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("path must be a safe canonical relative POSIX path")
    return value


SafeRelativePath = Annotated[str, AfterValidator(require_safe_relative_posix_path)]


class ReviewerBehavioralContract(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.reviewer-behavioral-contract.v1"]
    contract_id: str
    case_kind: CaseKind
    review_question: str
    correct_stop_is_valid: Literal[True]
    advisory_only: Literal[True]
    withheld_fields: tuple[str, ...] = Field(min_length=1)

    def identity_payload(self) -> dict[str, object]:
        return model_payload(self, exclude="contract_id")

    @model_validator(mode="after")
    def require_canonical_identity(self) -> ReviewerBehavioralContract:
        require_nonblank(self.review_question)
        require_sorted_unique_strings(self.withheld_fields, field="withheld fields")
        require_materialized_id(
            self.contract_id, "personal-reviewer-contract-", self.identity_payload()
        )
        return self


class ReviewAssignment(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.review-assignment.v1"]
    assignment_id: str
    attempt_ref: StrictArtifactRef
    bundle_ref: StrictArtifactRef
    role: ReviewRole
    policy_sha256: Sha256
    invocation_id: str
    primary_publication_refs: tuple[StrictArtifactRef, ...] = ()

    def identity_payload(self) -> dict[str, object]:
        return model_payload(self, exclude="assignment_id")

    @model_validator(mode="after")
    def require_role_inputs_and_identity(self) -> ReviewAssignment:
        require_nonblank(self.invocation_id)
        require_sorted_unique_refs(
            self.primary_publication_refs, field="primary publication refs"
        )
        expected = 2 if self.role == "synthesis" else 0
        if len(self.primary_publication_refs) != expected:
            raise ValueError("review assignment inputs disagree with its role")
        require_materialized_id(
            self.assignment_id,
            "personal-review-assignment-",
            self.identity_payload(),
        )
        return self


class AgentDispatchReceipt(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.agent-dispatch-receipt.v1"]
    assignment_ref: StrictArtifactRef
    invocation_id: str
    observed_model_id: str
    observed_runtime_id: str
    dispatched_at: AwareDatetime

    @field_validator("invocation_id", "observed_model_id", "observed_runtime_id")
    @classmethod
    def require_canonical_identity(cls, value: str) -> str:
        return require_nonblank(value)


class AgentDispatchObservation(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.agent-dispatch-observation.v1"]
    invocation_id: str
    observed_model_id: str
    observed_runtime_id: str
    dispatched_at: AwareDatetime

    @field_validator("invocation_id", "observed_model_id", "observed_runtime_id")
    @classmethod
    def require_canonical_identity(cls, value: str) -> str:
        return require_nonblank(value)


class CaseBehaviorObservation(BaseModel):
    model_config = STRICT
    observation_kind: ObservationKind
    evidence_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    exact_code: str | None = None
    exact_finding_kind: str | None = None

    @field_validator("evidence_refs")
    @classmethod
    def require_canonical_refs(
        cls, value: tuple[ArtifactRef, ...]
    ) -> tuple[ArtifactRef, ...]:
        return require_sorted_unique_refs(value, field="observation evidence refs")


class ReviewDisagreement(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.review-disagreement.v1"]
    disagreement_id: str
    left_review_ref: StrictArtifactRef
    right_review_ref: StrictArtifactRef
    domain: FindingDomain
    target_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    left_finding_ref: StrictArtifactRef
    right_finding_ref: StrictArtifactRef
    disagreement_kind: Literal["assessment-conflict"]

    def identity_payload(self) -> dict[str, object]:
        return model_payload(self, exclude="disagreement_id")

    @model_validator(mode="after")
    def require_canonical_identity(self) -> ReviewDisagreement:
        require_sorted_unique_refs(self.target_refs, field="disagreement target refs")
        require_materialized_id(
            self.disagreement_id,
            "personal-review-disagreement-",
            self.identity_payload(),
        )
        return self


class ExpectedObservationRequirement(BaseModel):
    model_config = STRICT
    observation_kind: ObservationKind
    exact_code: str | None = None
    exact_finding_kind: str | None = None


Requirement = Annotated[
    ExpectedObservationRequirement, BeforeValidator(strict_model_input)
]


class _ExpectedBehaviorBase(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.expected-behavior.v1"]
    behavior_id: str
    prohibited_outcomes: tuple[str, ...]

    def identity_payload(self) -> dict[str, object]:
        return model_payload(self, exclude="behavior_id")

    @model_validator(mode="after")
    def require_canonical_identity(self) -> _ExpectedBehaviorBase:
        require_sorted_unique_strings(
            self.prohibited_outcomes, field="prohibited outcomes"
        )
        requirements = getattr(self, "requirements")  # noqa: B009
        keys = tuple(
            (
                item.observation_kind,
                item.exact_code or "",
                item.exact_finding_kind or "",
            )
            for item in requirements
        )
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError(
                "expected observations must be unique and canonically sorted"
            )
        require_materialized_id(
            self.behavior_id,
            "personal-expected-behavior-",
            self.identity_payload(),
        )
        return self


class SuccessfulRunExpectedBehavior(_ExpectedBehaviorBase):
    case_kind: Literal["successful-end-to-end"]
    requirements: tuple[Requirement, Requirement]


class CorrectStopExpectedBehavior(_ExpectedBehaviorBase):
    case_kind: Literal["correct-stop"]
    blocker_code: str
    blocker_finding_kind: str
    blocker_gate: str
    expected_checkpoint_ref: StrictArtifactRef
    expected_checkpoint_sha256: Sha256
    requirements: tuple[Requirement, Requirement]


class IncompatibilityExpectedBehavior(_ExpectedBehaviorBase):
    case_kind: Literal["data-method-incompatibility"]
    rejected_method: Literal["rdd"]
    retained_method: Literal["hedonic"]
    estimand_anchor_ref: StrictArtifactRef
    estimand_sha256: Sha256
    unmet_requirements: tuple[str, ...] = Field(min_length=1)
    requirements: tuple[Requirement, Requirement]

    @field_validator("unmet_requirements")
    @classmethod
    def require_canonical_unmet(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return require_sorted_unique_strings(value, field="unmet requirements")


class EvidenceChallengeExpectedBehavior(_ExpectedBehaviorBase):
    case_kind: Literal["evidence-citation-challenge"]
    predecessor_finding_kinds: tuple[str, ...] = Field(min_length=1)
    requirements: tuple[Requirement, Requirement, Requirement]

    @field_validator("predecessor_finding_kinds")
    @classmethod
    def require_canonical_findings(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return require_sorted_unique_strings(value, field="predecessor finding kinds")
