"""Shared real-storage fixtures for Personal Validation integration tests."""

from __future__ import annotations

import hashlib
import multiprocessing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation.contracts import (
    PERSONAL_ATTEMPT_ROOTS_V1,
    PersonalCanonicalCase,
    PersonalCanonicalCaseBinding,
    PersonalValidationProtocol,
    PersonalValidationSession,
    SystemSnapshot,
    materialize_id,
)
from envresearch.personal_validation.private_store import PersonalValidationStore
from envresearch.personal_validation.roots import RootExclusionSet
from envresearch.personal_validation.service import PersonalValidationService
from envresearch.personal_validation.snapshots import snapshot_inputs, snapshot_roots
from envresearch.research.stop_contracts import (
    ResearchCheckpointEvidence,
    ResearchFileEvidence,
    ResearchStopInspection,
)

SHA = "a" * 64
CASE_KINDS = (
    "successful-end-to-end",
    "correct-stop",
    "data-method-incompatibility",
    "evidence-citation-challenge",
)


@dataclass(frozen=True, slots=True)
class AuthorityCase:
    private_root: Path
    repository: Path
    git_common: Path
    vault: Path
    exclusions: RootExclusionSet


@dataclass(frozen=True, slots=True)
class PreparedContext:
    authority: AuthorityCase
    store: PersonalValidationStore
    service: PersonalValidationService
    protocol_ref: ArtifactRef
    case_ref: ArtifactRef
    inventory_ref: ArtifactRef
    inspection_ref: ArtifactRef
    inspection: ResearchStopInspection


class InjectedCrash(RuntimeError):
    """Test-only crash marker raised after one durable boundary."""


def authority_case(tmp_path: Path) -> AuthorityCase:
    repository = tmp_path / "repository"
    git_common = tmp_path / "git-common"
    vault = tmp_path / "vault"
    for root in (repository, git_common, vault):
        root.mkdir(mode=0o700, parents=True)
    exclusions = RootExclusionSet(
        repository=repository,
        git_common_dir=git_common,
        worktrees=(repository,),
        obsidian_roots=(vault,),
    )
    return AuthorityCase(
        private_root=tmp_path / "private-validation",
        repository=repository,
        git_common=git_common,
        vault=vault,
        exclusions=exclusions,
    )


def prepared_context(
    tmp_path: Path,
    *,
    selected_kind: str = "correct-stop",
    factory_service: Any = None,
    failure_boundary: str | None = None,
    session_nonce: str = "persisted-session-nonce",
) -> PreparedContext:
    authority = authority_case(tmp_path)
    store = PersonalValidationStore.create(authority.private_root, authority.exclusions)
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "case.json").write_bytes(b'{"fixture":"canonical"}')
    input_snapshot = snapshot_inputs(input_root)
    input_ref = store.publish(input_snapshot.snapshot_id, input_snapshot)

    roots = {
        name: tmp_path / "attempt-roots" / name for name in PERSONAL_ATTEMPT_ROOTS_V1
    }
    for root in roots.values():
        root.mkdir(parents=True)
    inventory = snapshot_roots(roots)
    inventory_ref = store.publish(inventory.inventory_id, inventory)

    case_refs = {
        kind: store.publish(case.case_id, case)
        for kind in CASE_KINDS
        for case in (_case(kind, input_ref),)
    }
    selected_ref = case_refs[selected_kind]
    protocol = _protocol(case_refs)
    protocol_ref = store.publish(protocol.protocol_id, protocol)
    system = _system_snapshot(protocol_ref)
    system_ref = store.publish(system.snapshot_id, system)
    inspection = stop_inspection()
    inspection_ref = store.publish("blocked-inspection", inspection)

    def fail(boundary: str) -> None:
        if boundary == failure_boundary:
            raise InjectedCrash(boundary)

    service = PersonalValidationService(
        store=store,
        factory_service=factory_service,
        session_nonce=session_nonce,
        system_snapshot_ref=system_ref,
        attempt_inventory_ref=inventory_ref,
        failure_injector=fail if failure_boundary else None,
    )
    return PreparedContext(
        authority=authority,
        store=store,
        service=service,
        protocol_ref=protocol_ref,
        case_ref=selected_ref,
        inventory_ref=inventory_ref,
        inspection_ref=inspection_ref,
        inspection=inspection,
    )


def fresh_service(
    context: PreparedContext,
    *,
    writable: bool,
    system_snapshot_ref: ArtifactRef | None = None,
) -> tuple[PersonalValidationStore, PersonalValidationService]:
    constructor = (
        PersonalValidationStore.create
        if writable
        else PersonalValidationStore.open_existing
    )
    store = constructor(context.authority.private_root, context.authority.exclusions)
    return store, PersonalValidationService(
        store=store,
        factory_service=None,
        session_nonce="persisted-session-nonce",
        system_snapshot_ref=system_snapshot_ref or context.service.system_snapshot_ref,
        attempt_inventory_ref=context.inventory_ref,
    )


def tree_state(*roots: Path) -> tuple[tuple[Any, ...], ...]:
    state: list[tuple[Any, ...]] = []
    for root in roots:
        if not root.exists() and not root.is_symlink():
            continue
        for path in (root, *sorted(root.rglob("*"))):
            metadata = path.lstat()
            state.append(
                (
                    str(path),
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_mode,
                    metadata.st_uid,
                    metadata.st_nlink,
                    path.read_bytes() if path.is_file() else None,
                )
            )
    return tuple(state)


def ref(name: str, digest: str = SHA) -> ArtifactRef:
    return ArtifactRef(artifact_id=name, artifact_version=1, content_hash=digest)


def stop_inspection() -> ResearchStopInspection:
    return ResearchStopInspection(
        schema_version="research.stop-inspection.v1",
        run_id="blocked-run",
        phase="blocked",
        stop_code="RESEARCH_RUN_BLOCKED",
        findings=(ref("blocking-finding"),),
        checkpoints=(
            ResearchCheckpointEvidence(
                node_id="screen-methods",
                checkpoint_sha256=SHA,
                artifact_refs=(),
            ),
        ),
        research_evidence=(
            ResearchFileEvidence(
                relative_path="artifacts/design-review-findings.json",
                kind="file",
                sha256=SHA,
                size_bytes=1,
                mode=0o600,
            ),
        ),
    )


def compete_prepare(
    private_root: str,
    exclusions: RootExclusionSet,
    protocol_ref: ArtifactRef,
    case_ref: ArtifactRef,
    inspection_ref: ArtifactRef,
    inspection: ResearchStopInspection,
    inventory_ref: ArtifactRef,
    system_ref: ArtifactRef,
    gate: Any,
    results: Any,
) -> None:
    gate.wait()
    try:
        with PersonalValidationStore.create(Path(private_root), exclusions) as store:
            service = PersonalValidationService(
                store=store,
                factory_service=None,
                session_nonce="persisted-session-nonce",
                system_snapshot_ref=system_ref,
                attempt_inventory_ref=inventory_ref,
            )
            prepared = service.prepare_correct_stop(
                protocol_ref, case_ref, inspection_ref, inspection
            )
        results.put(
            (
                "ok",
                prepared.session_ref,
                prepared.attempt_ref,
                prepared.completion_event_id,
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        results.put(
            ("error", type(error).__name__, getattr(error, "finding_kind", None))
        )


def run_competing_prepares(
    context: PreparedContext, systems: tuple[ArtifactRef, ArtifactRef]
) -> list[tuple[object, ...]]:
    process_context = multiprocessing.get_context("spawn")
    gate = process_context.Event()
    results = process_context.Queue()
    common = (
        str(context.authority.private_root),
        context.authority.exclusions,
        context.protocol_ref,
        context.case_ref,
        context.inspection_ref,
        context.inspection,
        context.inventory_ref,
    )
    processes = [
        process_context.Process(
            target=compete_prepare, args=(*common, system, gate, results)
        )
        for system in systems
    ]
    for process in processes:
        process.start()
    gate.set()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    return [results.get(timeout=2) for _ in processes]


def session_ref(context: PreparedContext) -> ArtifactRef:
    protocol = context.store.load(context.protocol_ref, PersonalValidationProtocol)
    payload: dict[str, object] = {
        "schema_version": "personal.validation-session.v1",
        "session_nonce": "persisted-session-nonce",
        "protocol_ref": context.protocol_ref,
        "cases": protocol.cases,
    }
    payload["session_id"] = materialize_id("personal-session-", payload)
    session = PersonalValidationSession.model_validate(payload)
    return ref(
        session.session_id,
        hashlib.sha256(session.model_dump_json().encode()).hexdigest(),
    )


def alternate_system_snapshot(context: PreparedContext) -> ArtifactRef:
    current = context.store.load(context.service.system_snapshot_ref, SystemSnapshot)
    payload = current.model_dump(exclude={"snapshot_id"})
    payload["git_commit"] = "alternate-fixture-commit"
    payload["snapshot_id"] = materialize_id("personal-system-snapshot-", payload)
    alternate = SystemSnapshot.model_validate(payload)
    return context.store.publish(alternate.snapshot_id, alternate)


def _case(kind: str, input_ref: ArtifactRef) -> PersonalCanonicalCase:
    payload: dict[str, object] = {
        "schema_version": "personal.canonical-case.v1",
        "kind": kind,
        "input_snapshot_ref": input_ref,
        "expected_behavior_ref": ref(f"expected-{kind}"),
        "reviewer_contract_ref": ref(f"reviewer-{kind}"),
        "required_logical_roots": tuple(sorted(PERSONAL_ATTEMPT_ROOTS_V1)),
        "intended_use": "Exercise the exact advisory preparation boundary.",
        "data_boundary": "synthetic",
        "required_factory_stages": (),
        "expected_terminal_kind": (
            "correct-stop"
            if kind in {"correct-stop", "data-method-incompatibility"}
            else "factory-run"
        ),
        "prohibited_outcomes": ("fabricated-result",),
    }
    payload["case_id"] = materialize_id("personal-case-", payload)
    return PersonalCanonicalCase.model_validate(payload)


def _protocol(refs: dict[str, ArtifactRef]) -> PersonalValidationProtocol:
    bindings = tuple(
        PersonalCanonicalCaseBinding(case_ref=refs[kind], kind=kind)
        for kind in CASE_KINDS
    )
    payload: dict[str, object] = {
        "schema_version": "personal.validation-protocol.v1",
        "protocol_version": "1",
        "cases": bindings,
        "scientific_policy_sha256": SHA,
        "evidence_policy_sha256": SHA,
        "synthesis_policy_sha256": SHA,
        "rubric_sha256": SHA,
        "report_schema_sha256": SHA,
        "external_access_policy_sha256": SHA,
        "scope": "personal-advisory-only",
        "blocks": (),
        "hidden_evaluation_status": "not-run",
        "product_release_status": "scientific_release_pending",
    }
    payload["protocol_id"] = materialize_id("personal-protocol-", payload)
    return PersonalValidationProtocol.model_validate(payload)


def _system_snapshot(protocol_ref: ArtifactRef) -> SystemSnapshot:
    payload: dict[str, object] = {
        "schema_version": "personal.system-snapshot.v1",
        "git_commit": "fixture-commit",
        "execution_tree_sha256": SHA,
        "uv_lock_sha256": SHA,
        "capability_manifest_sha256": SHA,
        "method_profile_sha256": SHA,
        "protocol_ref": protocol_ref,
        "runtime_versions": (("python", "3.13"),),
        "clean_worktree": True,
    }
    payload["snapshot_id"] = materialize_id("personal-system-snapshot-", payload)
    return SystemSnapshot.model_validate(payload)


__all__ = [
    "AuthorityCase",
    "InjectedCrash",
    "PreparedContext",
    "alternate_system_snapshot",
    "authority_case",
    "compete_prepare",
    "fresh_service",
    "prepared_context",
    "ref",
    "run_competing_prepares",
    "session_ref",
    "tree_state",
]
