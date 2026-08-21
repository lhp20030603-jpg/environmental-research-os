"""Strict foundational contracts for advisory Personal Validation."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, model_validator

from envresearch.factory.contracts import ResearchFactoryRun
from envresearch.personal_validation import _strict as _strict_contracts
from envresearch.personal_validation._strict import (
    STRICT,
    CaseKind,
    CorrectStopExpectedBehavior,
    EvidenceChallengeExpectedBehavior,
    IncompatibilityExpectedBehavior,
    Sha256,
    StrictArtifactRef,
    SuccessfulRunExpectedBehavior,
    model_payload,
    require_materialized_id,
    require_nonblank,
    require_sorted_unique_strings,
    strict_model_input,
)
from envresearch.research.stop_contracts import ResearchStopInspection

ExpectedObservationRequirement = _strict_contracts.ExpectedObservationRequirement
ReviewerBehavioralContract = _strict_contracts.ReviewerBehavioralContract
materialize_id = _strict_contracts.materialize_id

CASE_ORDER = (
    "successful-end-to-end",
    "correct-stop",
    "data-method-incompatibility",
    "evidence-citation-challenge",
)
PERSONAL_ATTEMPT_ROOTS_V1 = (
    "research-design",
    "research-citation",
    "v03",
    "v031",
    "paper",
    "factory",
    "local-analysis",
    "citation-control",
    "valuation-control",
)


def _identity(model: BaseModel, field: str) -> dict[str, object]:
    return model_payload(model, exclude=field)


def require_exact_case_order(cases: tuple[PersonalCanonicalCaseBinding, ...]) -> None:
    if tuple(item.kind for item in cases) != CASE_ORDER:
        raise ValueError("protocol cases must contain the exact four-case order")


class PersonalCanonicalCaseBinding(BaseModel):
    model_config = STRICT
    case_ref: StrictArtifactRef
    kind: CaseKind


class PersonalValidationProtocol(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.validation-protocol.v1"]
    protocol_id: str
    protocol_version: Literal["1"]
    cases: tuple[
        Annotated[PersonalCanonicalCaseBinding, BeforeValidator(strict_model_input)],
        ...,
    ] = Field(min_length=4, max_length=4)
    scientific_policy_sha256: Sha256
    evidence_policy_sha256: Sha256
    synthesis_policy_sha256: Sha256
    rubric_sha256: Sha256
    report_schema_sha256: Sha256
    external_access_policy_sha256: Sha256
    scope: Literal["personal-advisory-only"]
    blocks: tuple[()] = ()
    hidden_evaluation_status: Literal["not-run"]
    product_release_status: Literal["scientific_release_pending"]

    def identity_payload(self) -> dict[str, object]:
        return _identity(self, "protocol_id")

    @model_validator(mode="after")
    def require_order_and_identity(self) -> PersonalValidationProtocol:
        require_exact_case_order(self.cases)
        require_materialized_id(
            self.protocol_id, "personal-protocol-", self.identity_payload()
        )
        return self


class InputEntry(BaseModel):
    model_config = STRICT
    logical_name: str
    kind: Literal["file", "directory", "symlink", "submodule"]
    sha256: Sha256 | None
    size_bytes: int = Field(ge=0)
    mode: int = Field(ge=0)
    symlink_target: str | None = None

    @model_validator(mode="after")
    def require_kind_evidence(self) -> InputEntry:
        require_nonblank(self.logical_name)
        if self.kind in {"file", "submodule"} and self.sha256 is None:
            raise ValueError("file and submodule inputs require a digest")
        if self.kind in {"directory", "symlink"} and self.sha256 is not None:
            raise ValueError("directory and symlink inputs cannot claim a byte digest")
        if (self.kind == "symlink") != (self.symlink_target is not None):
            raise ValueError("input symlink metadata is incomplete")
        return self


class InputSnapshot(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.input-snapshot.v1"]
    snapshot_id: str
    entries: tuple[Annotated[InputEntry, BeforeValidator(strict_model_input)], ...] = (
        Field(min_length=1)
    )

    def identity_payload(self) -> dict[str, object]:
        return _identity(self, "snapshot_id")

    @model_validator(mode="after")
    def require_canonical_identity(self) -> InputSnapshot:
        keys = tuple(item.logical_name for item in self.entries)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("input entries must be unique and canonically sorted")
        require_materialized_id(
            self.snapshot_id, "personal-input-snapshot-", self.identity_payload()
        )
        return self


class SystemSnapshot(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.system-snapshot.v1"]
    snapshot_id: str
    git_commit: str
    execution_tree_sha256: Sha256
    uv_lock_sha256: Sha256
    capability_manifest_sha256: Sha256
    method_profile_sha256: Sha256
    protocol_ref: StrictArtifactRef
    runtime_versions: tuple[tuple[str, str], ...] = Field(min_length=1)
    clean_worktree: bool

    def identity_payload(self) -> dict[str, object]:
        return _identity(self, "snapshot_id")

    @model_validator(mode="after")
    def require_canonical_identity(self) -> SystemSnapshot:
        require_nonblank(self.git_commit)
        keys = tuple(item[0] for item in self.runtime_versions)
        if (
            self.runtime_versions != tuple(sorted(self.runtime_versions))
            or len(keys) != len(set(keys))
            or any(not key or not value for key, value in self.runtime_versions)
        ):
            raise ValueError("runtime versions must be unique, nonblank, and canonical")
        require_materialized_id(
            self.snapshot_id, "personal-system-snapshot-", self.identity_payload()
        )
        return self


class RootInventoryEntry(BaseModel):
    model_config = STRICT
    logical_root: str
    relative_path: str
    kind: Literal["file", "directory", "symlink"]
    sha256: Sha256 | None
    size_bytes: int = Field(ge=0)
    owner: int = Field(ge=0)
    mode: int = Field(ge=0)
    link_count: int = Field(ge=1)
    symlink_target: str | None = None

    @model_validator(mode="after")
    def require_kind_evidence(self) -> RootInventoryEntry:
        require_nonblank(self.logical_root)
        if self.relative_path.startswith("/") or ".." in self.relative_path.split("/"):
            raise ValueError("inventory path must be descriptor-relative")
        if self.kind == "file" and self.sha256 is None:
            raise ValueError("inventory file requires a digest")
        if self.kind != "file" and self.sha256 is not None:
            raise ValueError("non-file inventory entry cannot claim a digest")
        if (self.kind == "symlink") != (self.symlink_target is not None):
            raise ValueError("inventory symlink metadata is incomplete")
        return self


class RootIdentity(BaseModel):
    model_config = STRICT
    logical_root: str
    device: int = Field(ge=0)
    inode: int = Field(ge=0)
    tree_sha256: Sha256
    entry_count: int = Field(ge=0)


class AttemptRootInventory(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.attempt-root-inventory.v1"]
    inventory_id: str
    root_identities: tuple[
        Annotated[RootIdentity, BeforeValidator(strict_model_input)], ...
    ] = Field(min_length=len(PERSONAL_ATTEMPT_ROOTS_V1))
    entries: tuple[
        Annotated[RootInventoryEntry, BeforeValidator(strict_model_input)], ...
    ]

    def identity_payload(self) -> dict[str, object]:
        return _identity(self, "inventory_id")

    @model_validator(mode="after")
    def require_canonical_identity(self) -> AttemptRootInventory:
        root_keys = tuple(item.logical_root for item in self.root_identities)
        if root_keys != tuple(sorted(root_keys)) or len(root_keys) != len(
            set(root_keys)
        ):
            raise ValueError("root identities must be unique and canonically sorted")
        entry_keys = tuple(
            (item.logical_root, item.relative_path) for item in self.entries
        )
        if entry_keys != tuple(sorted(entry_keys)) or len(entry_keys) != len(
            set(entry_keys)
        ):
            raise ValueError(
                "root inventory entries must be unique and canonically sorted"
            )
        if {item.logical_root for item in self.entries} - set(root_keys):
            raise ValueError("inventory entry names an unknown logical root")
        require_materialized_id(
            self.inventory_id,
            "personal-attempt-root-inventory-",
            self.identity_payload(),
        )
        return self


class CompletedFactoryRunTarget(BaseModel):
    model_config = STRICT
    target_type: Literal["completed-factory-run"]
    run_ref: StrictArtifactRef
    run: Annotated[ResearchFactoryRun, BeforeValidator(strict_model_input)]

    @model_validator(mode="after")
    def require_exact_run(self) -> CompletedFactoryRunTarget:
        expected = hashlib.sha256(self.run.model_dump_json().encode()).hexdigest()
        if (
            self.run_ref.artifact_id != self.run.factory_run_id
            or self.run_ref.artifact_version != 1
            or self.run_ref.content_hash != expected
        ):
            raise ValueError("completed target reference does not bind exact run bytes")
        return self


class CorrectStopTarget(BaseModel):
    model_config = STRICT
    target_type: Literal["correct-stop"]
    inspection_ref: StrictArtifactRef
    inspection: Annotated[ResearchStopInspection, BeforeValidator(strict_model_input)]
    attempt_inventory_ref: StrictArtifactRef

    @model_validator(mode="after")
    def require_exact_inspection(self) -> CorrectStopTarget:
        expected = hashlib.sha256(
            self.inspection.model_dump_json().encode()
        ).hexdigest()
        if (
            self.inspection_ref.artifact_version != 1
            or self.inspection_ref.content_hash != expected
        ):
            raise ValueError(
                "correct-stop target reference does not bind exact inspection bytes"
            )
        return self


AttemptTarget = Annotated[
    CompletedFactoryRunTarget | CorrectStopTarget,
    BeforeValidator(strict_model_input),
    Field(discriminator="target_type"),
]


class PersonalCanonicalCase(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.canonical-case.v1"]
    case_id: str
    kind: CaseKind
    input_snapshot_ref: StrictArtifactRef
    expected_behavior_ref: StrictArtifactRef
    reviewer_contract_ref: StrictArtifactRef
    required_logical_roots: tuple[str, ...]
    intended_use: str
    data_boundary: Literal["synthetic", "trusted-local"]
    required_factory_stages: tuple[str, ...]
    expected_terminal_kind: Literal["factory-run", "correct-stop"]
    prohibited_outcomes: tuple[str, ...]

    def identity_payload(self) -> dict[str, object]:
        return _identity(self, "case_id")

    @model_validator(mode="after")
    def require_canonical_identity(self) -> PersonalCanonicalCase:
        require_nonblank(self.intended_use)
        require_sorted_unique_strings(
            self.required_logical_roots, field="required logical roots"
        )
        require_sorted_unique_strings(
            self.required_factory_stages, field="required factory stages"
        )
        require_sorted_unique_strings(
            self.prohibited_outcomes, field="prohibited outcomes"
        )
        require_materialized_id(self.case_id, "personal-case-", self.identity_payload())
        return self


ExpectedBehaviorContract = Annotated[
    SuccessfulRunExpectedBehavior
    | CorrectStopExpectedBehavior
    | IncompatibilityExpectedBehavior
    | EvidenceChallengeExpectedBehavior,
    BeforeValidator(strict_model_input),
    Field(discriminator="case_kind"),
]


class PersonalValidationSession(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.validation-session.v1"]
    session_id: str
    session_nonce: str
    protocol_ref: StrictArtifactRef
    cases: tuple[
        Annotated[PersonalCanonicalCaseBinding, BeforeValidator(strict_model_input)],
        ...,
    ] = Field(min_length=4, max_length=4)

    def identity_payload(self) -> dict[str, object]:
        return _identity(self, "session_id")

    @model_validator(mode="after")
    def require_order_and_identity(self) -> PersonalValidationSession:
        require_nonblank(self.session_nonce)
        require_exact_case_order(self.cases)
        require_materialized_id(
            self.session_id, "personal-session-", self.identity_payload()
        )
        return self


class PersonalValidationAttempt(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.validation-attempt.v1"]
    attempt_id: str
    protocol_ref: StrictArtifactRef
    case_ref: StrictArtifactRef
    input_snapshot_ref: StrictArtifactRef
    system_snapshot_ref: StrictArtifactRef
    attempt_inventory_ref: StrictArtifactRef
    target: AttemptTarget
    start_event_id: str
    predecessor_attempt_ref: StrictArtifactRef | None = None

    def identity_payload(self) -> dict[str, object]:
        return _identity(self, "attempt_id")

    @model_validator(mode="after")
    def require_canonical_identity(self) -> PersonalValidationAttempt:
        require_nonblank(self.start_event_id)
        if (
            isinstance(self.target, CorrectStopTarget)
            and self.target.attempt_inventory_ref != self.attempt_inventory_ref
        ):
            raise ValueError(
                "correct-stop target inventory reference disagrees with attempt"
            )
        require_materialized_id(
            self.attempt_id, "personal-attempt-", self.identity_payload()
        )
        return self
