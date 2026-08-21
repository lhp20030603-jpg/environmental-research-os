"""Private-root authority and exact Personal attempt DAG integration tests."""

from __future__ import annotations

import itertools
import os
import shutil
from contextlib import suppress
from pathlib import Path

import pytest
from personal_validation_fixtures import (
    alternate_system_snapshot,
    authority_case,
    fresh_service,
    prepared_context,
    run_competing_prepares,
    tree_state,
)
from test_factory_run import connected_factory

import envresearch.personal_validation.private_store as private_store_module
from envresearch.personal_validation.contracts import PersonalValidationAttempt
from envresearch.personal_validation.errors import (
    PersonalValidationAuthorityInvalid,
    PersonalValidationIntegrityInvalid,
)
from envresearch.personal_validation.events import ZERO_PREDECESSOR
from envresearch.personal_validation.private_store import PersonalValidationStore
from envresearch.personal_validation.roots import PersonalRootAuthorityManifest
from envresearch.workers.filesystem import directories_overlap


def test_private_store_uses_explicit_nested_storage_and_control_roots(
    tmp_path: Path,
) -> None:
    case = authority_case(tmp_path)
    with PersonalValidationStore.create(case.private_root, case.exclusions) as store:
        assert store.objects.lexical_path == case.private_root / "objects"
        assert store.journals.lexical_path == case.private_root / "journals"
        assert store.control.lexical_path == case.private_root / "control/journal"
        assert not (
            case.private_root.parent / ".journals.worker-queue-control"
        ).exists()
        assert all(
            root.is_exact_child_of(store.root)
            for root in (store.objects, store.journals)
        )
        assert store.control.is_exact_descendant_of(store.root, Path("control/journal"))
        for left, right in itertools.combinations(
            (store.objects, store.journals, store.control), 2
        ):
            assert not directories_overlap(left.fd, right.fd)
        assert store.manifest.private_root.device == os.fstat(store.root.fd).st_dev
        assert isinstance(store.manifest, PersonalRootAuthorityManifest)


def test_private_root_overlap_is_rejected_without_mutation(tmp_path: Path) -> None:
    case = authority_case(tmp_path)
    unsafe = case.repository / "private"
    unsafe.mkdir(mode=0o700)
    (unsafe / "owner.txt").write_text("unchanged")
    before = tree_state(case.repository)
    with pytest.raises(PersonalValidationAuthorityInvalid) as captured:
        PersonalValidationStore.create(unsafe, case.exclusions)
    assert captured.value.finding_kind == "private-root-overlap"
    assert tree_state(case.repository) == before


def test_reopen_rejects_private_root_moved_beneath_exclusion_without_write(
    tmp_path: Path,
) -> None:
    case = authority_case(tmp_path)
    with PersonalValidationStore.create(case.private_root, case.exclusions):
        pass
    moved = case.repository / "moved-private"
    case.private_root.rename(moved)
    before = tree_state(case.repository)

    with pytest.raises(PersonalValidationAuthorityInvalid):
        PersonalValidationStore.open_existing(moved, case.exclusions)

    assert tree_state(case.repository) == before


def test_attempt_event_dag_has_no_forward_reference(tmp_path: Path) -> None:
    context = prepared_context(tmp_path)
    try:
        prepared = context.service.prepare_correct_stop(
            context.protocol_ref,
            context.case_ref,
            context.inspection_ref,
            context.inspection,
        )
        attempt = context.store.load(prepared.attempt_ref, PersonalValidationAttempt)
        completed = context.store.require_event(prepared.completion_event_id)
        events = context.store.read_events()

        assert completed.object_ref == prepared.attempt_ref
        assert events[0].object_ref == prepared.session_ref
        assert events[0].predecessor_sha256 == ZERO_PREDECESSOR
        assert events[1].predecessor_sha256 == events[0].event_sha256()
        assert [event.sequence for event in events] == [1, 2]
        assert "bundle_ref" not in attempt.model_dump()
        assert "completion_event_ref" not in attempt.model_dump()
    finally:
        context.store.close()


def test_existing_factory_run_is_reopened_into_exact_attempt(tmp_path: Path) -> None:
    factory = connected_factory(tmp_path / "factory-stack")
    context = None
    try:
        run_ref = factory.service.assemble(factory.design_ref, factory.release_ref)
        context = prepared_context(
            tmp_path / "personal-stack",
            selected_kind="successful-end-to-end",
            factory_service=factory.service,
        )
        prepared = context.service.prepare_existing_run(
            context.protocol_ref, context.case_ref, run_ref
        )
        attempt = context.store.load(prepared.attempt_ref, PersonalValidationAttempt)
        completed = context.store.require_event(prepared.completion_event_id)

        assert attempt.target.target_type == "completed-factory-run"
        assert attempt.target.run_ref == run_ref
        assert completed.object_ref == prepared.attempt_ref
    finally:
        if context is not None:
            context.store.close()
        factory.close()


def test_status_reopens_exact_completed_attempt_without_writes(tmp_path: Path) -> None:
    context = prepared_context(tmp_path)
    prepared = context.service.prepare_correct_stop(
        context.protocol_ref,
        context.case_ref,
        context.inspection_ref,
        context.inspection,
    )
    context.store.close()
    store, service = fresh_service(context, writable=False)
    try:
        before = tree_state(context.authority.private_root)
        status = service.status(prepared.session_ref)
        assert status.session_ref == prepared.session_ref
        assert status.attempt_refs == (prepared.attempt_ref,)
        assert tree_state(context.authority.private_root) == before
    finally:
        store.close()


def test_successor_attempt_extends_exact_per_case_predecessor_chain(
    tmp_path: Path,
) -> None:
    context = prepared_context(tmp_path)
    try:
        first = context.service.prepare_correct_stop(
            context.protocol_ref,
            context.case_ref,
            context.inspection_ref,
            context.inspection,
        )
        successor = context.service.prepare_correct_stop(
            context.protocol_ref,
            context.case_ref,
            context.inspection_ref,
            context.inspection,
            predecessor_attempt_ref=first.attempt_ref,
        )
        attempt = context.store.load(successor.attempt_ref, PersonalValidationAttempt)
        before = tree_state(context.authority.private_root)

        assert successor.attempt_ref != first.attempt_ref
        assert attempt.predecessor_attempt_ref == first.attempt_ref
        assert [event.sequence for event in context.store.read_events()] == [1, 2, 3]
        assert (
            context.service.prepare_correct_stop(
                context.protocol_ref,
                context.case_ref,
                context.inspection_ref,
                context.inspection,
                predecessor_attempt_ref=first.attempt_ref,
            )
            == successor
        )
        assert context.service.status(first.session_ref).attempt_refs == (
            first.attempt_ref,
            successor.attempt_ref,
        )
        assert tree_state(context.authority.private_root) == before
    finally:
        context.store.close()


def test_divergent_retry_is_typed_and_byte_identical(tmp_path: Path) -> None:
    context = prepared_context(tmp_path)
    prepared = context.service.prepare_correct_stop(
        context.protocol_ref,
        context.case_ref,
        context.inspection_ref,
        context.inspection,
    )
    forged_system = alternate_system_snapshot(context)
    _, divergent = fresh_service(
        context, writable=True, system_snapshot_ref=forged_system
    )
    before = tree_state(context.authority.private_root)
    try:
        with pytest.raises(PersonalValidationIntegrityInvalid) as captured:
            divergent.prepare_correct_stop(
                context.protocol_ref,
                context.case_ref,
                context.inspection_ref,
                context.inspection,
            )
        assert captured.value.finding_kind == "attempt-retry-divergent"
        assert tree_state(context.authority.private_root) == before
        assert divergent.status(prepared.session_ref).attempt_refs == (
            prepared.attempt_ref,
        )
    finally:
        divergent.store.close()
        context.store.close()


@pytest.mark.parametrize("divergent", [False, True])
def test_two_process_prepare_has_one_canonical_attempt_without_residue(
    tmp_path: Path, divergent: bool
) -> None:
    context = prepared_context(tmp_path)
    alternate = alternate_system_snapshot(context)
    systems = (
        context.service.system_snapshot_ref,
        alternate if divergent else context.service.system_snapshot_ref,
    )
    context.store.close()
    outcomes = run_competing_prepares(context, systems)
    successes = [outcome for outcome in outcomes if outcome[0] == "ok"]
    if divergent:
        assert len(successes) == 1
        assert sorted(outcome[0] for outcome in outcomes) == ["error", "ok"]
        assert next(outcome for outcome in outcomes if outcome[0] == "error")[1:] == (
            "PersonalValidationIntegrityInvalid",
            "attempt-retry-divergent",
        )
    else:
        assert len(successes) == 2
        assert successes[0][1:] == successes[1][1:]
    with PersonalValidationStore.open_existing(
        context.authority.private_root, context.authority.exclusions
    ) as reopened:
        assert len(reopened.read_events()) == 2
        assert len(reopened.attempt_objects()) == 1
    private_root = context.authority.private_root
    assert not any(".tmp" in path.name for path in private_root.rglob("*"))
    assert not tuple((private_root / "objects/exit/current").iterdir())


def test_partial_store_open_closes_every_pin_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = authority_case(tmp_path)
    descriptors = len(os.listdir("/dev/fd"))

    def fail(*_: object, **__: object) -> object:
        raise RuntimeError("injected journal construction failure")

    monkeypatch.setattr(
        "envresearch.personal_validation.private_store.SecureJournal.create_from_pinned",
        fail,
    )
    with pytest.raises(RuntimeError, match="injected journal"):
        PersonalValidationStore.create(case.private_root, case.exclusions)
    assert len(os.listdir("/dev/fd")) == descriptors


@pytest.mark.parametrize("boundary", ["objects", "journals", "control", "journal"])
def test_top_root_swap_during_composition_is_typed_zero_product_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    case = authority_case(tmp_path)
    excluded_before = tree_state(case.repository, case.git_common, case.vault)
    descriptors = len(os.listdir("/dev/fd"))
    displaced = tmp_path / f"displaced-{boundary}"
    attacker_state: list[tuple[tuple[object, ...], ...]] = []

    def swap_root() -> None:
        case.private_root.rename(displaced)
        case.private_root.mkdir(mode=0o700)
        (case.private_root / "attacker.txt").write_bytes(b"unchanged")
        attacker_state.append(tree_state(case.private_root))

    real_open = private_store_module._open_child

    def attacked_open(*args: object, **kwargs: object) -> object:
        child = real_open(*args, **kwargs)  # type: ignore[arg-type]
        relative = args[1]
        attacked_relative = (
            Path("control/journal") if boundary == "control" else Path(boundary)
        )
        if relative == attacked_relative:
            swap_root()
        return child

    if boundary == "journal":
        real_journal = private_store_module.SecureJournal.create_from_pinned

        def attacked_journal(*args: object, **kwargs: object) -> object:
            swap_root()
            return real_journal(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(
            private_store_module.SecureJournal,
            "create_from_pinned",
            attacked_journal,
        )
    else:
        monkeypatch.setattr(private_store_module, "_open_child", attacked_open)

    with pytest.raises(PersonalValidationAuthorityInvalid):
        PersonalValidationStore.create(case.private_root, case.exclusions)

    assert tree_state(case.private_root) == attacker_state[0]
    assert tree_state(case.repository, case.git_common, case.vault) == excluded_before
    assert len(os.listdir("/dev/fd")) == descriptors


def test_store_context_and_close_are_idempotent(tmp_path: Path) -> None:
    case = authority_case(tmp_path)
    store = PersonalValidationStore.create(case.private_root, case.exclusions)
    descriptors = (store.root.fd, store.objects.fd, store.journals.fd, store.control.fd)
    store.close()
    store.close()
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize("missing", ["objects", "objects/exit/locks"])
def test_writer_reopen_never_recreates_missing_child_root(
    tmp_path: Path, missing: str
) -> None:
    case = authority_case(tmp_path)
    with PersonalValidationStore.create(case.private_root, case.exclusions):
        pass
    shutil.rmtree(case.private_root / missing)
    before = tree_state(case.private_root)

    with pytest.raises(PersonalValidationAuthorityInvalid):
        PersonalValidationStore.create(case.private_root, case.exclusions)

    assert tree_state(case.private_root) == before


@pytest.mark.parametrize("damage", ["journal-control", "session-lock"])
def test_live_writer_rejects_deleted_control_identity_without_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    context = prepared_context(tmp_path)
    real_publish = context.store.publish
    attacked: list[tuple[tuple[object, ...], ...]] = []

    def delete_control(artifact_id: str, payload: object) -> object:
        if isinstance(payload, PersonalValidationAttempt) and not attacked:
            root = context.authority.private_root
            identity = (
                context.store.journal._journal_id
                if damage == "journal-control"
                else "personal-validation-session"
            )
            base = root / (
                "control/journal" if damage == "journal-control" else "objects"
            )
            (base / f"journal-locks/{identity}.filelock").unlink()
            (base / f"journal-lock-anchors/{identity}.json").unlink()
            attacked.append(tree_state(root))
        return real_publish(artifact_id, payload)  # type: ignore[arg-type]

    monkeypatch.setattr(context.store, "publish", delete_control)
    try:
        with pytest.raises(PersonalValidationAuthorityInvalid) as captured:
            context.service.prepare_correct_stop(
                context.protocol_ref,
                context.case_ref,
                context.inspection_ref,
                context.inspection,
            )
        assert captured.value.finding_kind in {
            "journal-control-invalid",
            "session-lock-invalid",
        }
        assert tree_state(context.authority.private_root) == attacked[0]
    finally:
        with suppress(PersonalValidationAuthorityInvalid):
            context.store.close()
