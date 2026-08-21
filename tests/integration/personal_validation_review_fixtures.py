"""Shared real Task 5 case fixture for Task 6 integration tests."""

from __future__ import annotations

import multiprocessing
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation._strict import canonical_json, materialize_id
from envresearch.personal_validation.canonical_cases import (
    DEFAULT_PROTOCOL_ROOT,
    DisposableAttemptRoots,
    load_protocol_v1,
    run_case,
)
from envresearch.personal_validation.contracts import (
    PersonalValidationAttempt,
    SystemSnapshot,
)
from envresearch.personal_validation.private_store import PersonalValidationStore
from envresearch.personal_validation.review_contracts import (
    AgentDispatchObservation,
    AgentFindingResponse,
    AgentReviewResponse,
    ExternalAccessRequest,
    ReviewAssignment,
)
from envresearch.personal_validation.roots import RootExclusionSet
from envresearch.personal_validation.service import PersonalValidationService

Role = Literal["scientific", "evidence", "synthesis"]
SHA = "a" * 64
CRASH_EXIT = 86


@dataclass(frozen=True, slots=True)
class ReviewProcessConfig:
    private_root: Path
    exclusions: RootExclusionSet
    system_snapshot_ref: ArtifactRef
    attempt_inventory_ref: ArtifactRef


@dataclass(slots=True)
class ReviewCase:
    store: PersonalValidationStore
    roots: DisposableAttemptRoots
    validation: PersonalValidationService
    attempt_ref: ArtifactRef
    oracle_digest: str
    bundles: dict[str, ArtifactRef] = field(default_factory=dict)

    @property
    def service(self) -> PersonalValidationService:
        return self.validation

    def close(self) -> None:
        self.roots.close()
        self.store.close()

    def process_config(self) -> ReviewProcessConfig:
        return ReviewProcessConfig(
            self.store.root.lexical_path,
            self.store.exclusions,
            self.service.system_snapshot_ref,
            self.service.attempt_inventory_ref,
        )

    def transaction_arguments(self, action: str, boundary: str) -> tuple[Any, ...]:
        if action == "bundle":
            return self.attempt_ref, "scientific", ()
        if action == "evaluation":
            return (self.attempt_ref,)
        if action == "report":
            scientific = self.record("scientific", invocation_id="report-science")
            evidence = self.record("evidence", invocation_id="report-evidence")
            synthesis = self.record(
                "synthesis",
                invocation_id="report-synthesis",
                primary_publication_refs=(
                    scientific.publication_ref,
                    evidence.publication_ref,
                ),
            )
            evaluation = self.service.evaluate_case(self.attempt_ref)
            return (
                self.attempt_ref,
                evaluation,
                scientific.publication_ref,
                evidence.publication_ref,
                synthesis.publication_ref,
            )
        prepared = self.prepare("scientific")
        assignment = self.service.assign_review(
            self.attempt_ref,
            prepared.bundle_ref,
            role="scientific",
            invocation_id=f"spawn-{boundary}",
        )
        if action == "assignment":
            return (
                self.attempt_ref,
                prepared.bundle_ref,
                "scientific",
                f"spawn-{boundary}",
            )
        if action == "dispatch":
            model = self.store.load(assignment, ReviewAssignment)
            observed = AgentDispatchObservation(
                schema_version="personal.agent-dispatch-observation.v1",
                invocation_id=model.invocation_id,
                observed_model_id="review-model-v1",
                observed_runtime_id="review-runtime-v1",
                dispatched_at=datetime(2026, 8, 21, tzinfo=UTC),
            )
            return assignment, canonical_json(observed.model_dump(mode="json"))
        if action in {"external-dispatch", "external-receipt"}:
            request = ExternalAccessRequest(
                provider="web",
                operation="read-official-documentation",
                source_locator="https://example.org/policy",
                local_finding_keys=(),
            )
            raw = canonical_json(request.model_dump(mode="json"))
            if action == "external-dispatch":
                return assignment, assignment, raw
            dispatch_ref = self.service.record_external_access_dispatch(
                assignment, assignment, raw
            )
            return (
                dispatch_ref,
                b'{"status":"observed"}',
                "success",
                datetime(2026, 8, 21, tzinfo=UTC),
            )
        if action == "review":
            receipt = self.record_dispatch(assignment)
            return (
                assignment,
                receipt,
                self.canonical_response_bytes(role="scientific"),
                (),
            )
        raise AssertionError(f"unknown crash action: {action}")

    def prepare(self, role: Role):  # type: ignore[no-untyped-def]
        prepared = self.service.prepare_bundle(self.attempt_ref, role=role)
        self.bundles[role] = prepared.bundle_ref
        return prepared

    def assign(self, role: Role, *, invocation_id: str) -> ArtifactRef:
        bundle_ref = self.bundles.get(role) or self.prepare(role).bundle_ref
        return self.service.assign_review(
            self.attempt_ref,
            bundle_ref,
            role=role,
            invocation_id=invocation_id,
        )

    def record_dispatch(self, assignment_ref: ArtifactRef) -> ArtifactRef:
        assignment = self.store.load(assignment_ref, ReviewAssignment)
        observed = AgentDispatchObservation(
            schema_version="personal.agent-dispatch-observation.v1",
            invocation_id=assignment.invocation_id,
            observed_model_id="review-model-v1",
            observed_runtime_id="review-runtime-v1",
            dispatched_at=datetime(2026, 8, 21, tzinfo=UTC),
        )
        return self.service.record_dispatch(
            assignment_ref, canonical_json(observed.model_dump(mode="json"))
        )

    def record(
        self,
        role: Role,
        *,
        invocation_id: str,
        findings: tuple[AgentFindingResponse, ...] = (),
        primary_publication_refs: tuple[ArtifactRef, ...] = (),
    ):
        prepared = self.service.prepare_bundle(
            self.attempt_ref,
            role=role,
            primary_publication_refs=primary_publication_refs,
        )
        self.bundles[role] = prepared.bundle_ref
        assignment = self.assign(role, invocation_id=invocation_id)
        receipt = self.record_dispatch(assignment)
        raw = self.canonical_response_bytes(role=role, findings=findings)
        return self.service.record_review(assignment, receipt, raw)

    @staticmethod
    def canonical_response_bytes(
        *, role: Role, findings: tuple[AgentFindingResponse, ...] = ()
    ) -> bytes:
        response = AgentReviewResponse(
            schema_version="personal.agent-review-response.v1",
            role=role,
            findings=findings,
            external_access_requests=(),
            completion_status="complete",
        )
        return canonical_json(response.model_dump(mode="json"))


def make_review_case(
    tmp_path: Path,
    *,
    projection_canary: str | None = None,
    case_kind: str = "correct-stop",
) -> ReviewCase:
    loaded = load_protocol_v1()
    repository = DEFAULT_PROTOCOL_ROOT.parents[2]
    git_common = tmp_path / "git-common"
    vault = tmp_path / "vault"
    git_common.mkdir(parents=True)
    vault.mkdir(parents=True)
    exclusions = RootExclusionSet(
        repository=repository,
        git_common_dir=git_common,
        worktrees=(repository,),
        obsidian_roots=(vault,),
    )
    store = PersonalValidationStore.create(tmp_path / "private", exclusions)
    system_payload: dict[str, object] = {
        "schema_version": "personal.system-snapshot.v1",
        "git_commit": projection_canary or "task6-real-review",
        "execution_tree_sha256": SHA,
        "uv_lock_sha256": SHA,
        "capability_manifest_sha256": SHA,
        "method_profile_sha256": SHA,
        "protocol_ref": loaded.protocol_ref,
        "runtime_versions": (("python", "3.13"),),
        "clean_worktree": True,
    }
    system_payload["snapshot_id"] = materialize_id(
        "personal-system-snapshot-", system_payload
    )
    system = SystemSnapshot.model_validate(system_payload)
    system_ref = store.publish(system.snapshot_id, system)
    position = tuple(case.kind for case in loaded.cases).index(case_kind)
    case_ref = loaded.protocol.cases[position].case_ref
    roots = DisposableAttemptRoots.for_case(
        case_ref=case_ref,
        repository_root=repository,
        protocol_ref=loaded.protocol_ref,
        store=store,
        exclusions=exclusions,
        session_nonce="review-session-nonce",
        system_snapshot_ref=system_ref,
    )
    try:
        prepared = run_case(case_ref, roots)
        attempt = store.load(prepared.attempt_ref, PersonalValidationAttempt)
        factory = (
            roots.research.factory_service(roots.paper.release_service)
            if attempt.target.target_type == "completed-factory-run"
            else None
        )
        service = PersonalValidationService(
            store=store,
            factory_service=factory,
            session_nonce="review-session-nonce",
            system_snapshot_ref=system_ref,
            attempt_inventory_ref=attempt.attempt_inventory_ref,
        )
        return ReviewCase(
            store,
            roots,
            service,
            prepared.attempt_ref,
            loaded.cases[position].expected_behavior_ref.content_hash,
        )
    except BaseException:
        roots.close()
        store.close()
        raise


def open_process_service(
    config: ReviewProcessConfig,
    *,
    failure_boundary: str | None = None,
) -> tuple[PersonalValidationStore, PersonalValidationService]:
    store = PersonalValidationStore.create(config.private_root, config.exclusions)

    def terminate(boundary: str) -> None:
        if boundary == failure_boundary:
            os._exit(CRASH_EXIT)

    service = PersonalValidationService(
        store=store,
        factory_service=None,
        session_nonce="review-session-nonce",
        system_snapshot_ref=config.system_snapshot_ref,
        attempt_inventory_ref=config.attempt_inventory_ref,
        failure_injector=terminate if failure_boundary else None,
    )
    return store, service


def terminate_transaction_process(
    config: ReviewProcessConfig,
    action: str,
    boundary: str,
    arguments: tuple[Any, ...],
) -> int:
    process = multiprocessing.get_context("spawn").Process(
        target=_transaction_worker,
        args=(config, action, boundary, arguments),
    )
    process.start()
    process.join(30)
    if process.is_alive():
        process.terminate()
        process.join(5)
        raise AssertionError(f"crash worker did not stop at {boundary}")
    assert process.exitcode is not None
    return process.exitcode


def _transaction_worker(
    config: ReviewProcessConfig,
    action: str,
    boundary: str,
    arguments: tuple[Any, ...],
) -> None:
    store, service = open_process_service(config, failure_boundary=boundary)
    try:
        run_transaction(service, action, arguments)
        if action == "assignment":
            os._exit(CRASH_EXIT)
    finally:
        store.close()


def run_transaction(
    service: PersonalValidationService, action: str, arguments: tuple[Any, ...]
) -> Any:
    if action == "bundle":
        return service.prepare_bundle(
            arguments[0], role=arguments[1], primary_publication_refs=arguments[2]
        )
    if action == "assignment":
        return service.assign_review(
            *arguments[:2], role=arguments[2], invocation_id=arguments[3]
        )
    if action == "dispatch":
        return service.record_dispatch(*arguments)
    if action == "external-dispatch":
        return service.record_external_access_dispatch(*arguments)
    if action == "external-receipt":
        return service.record_external_access_receipt(
            arguments[0], arguments[1], outcome=arguments[2], observed_at=arguments[3]
        )
    if action == "review":
        return service.record_review(
            arguments[0],
            arguments[1],
            arguments[2],
            external_access_receipt_refs=arguments[3],
        )
    if action == "evaluation":
        return service.evaluate_case(*arguments)
    if action == "report":
        return service.finalize_report(*arguments)
    raise AssertionError(f"unknown crash action: {action}")
