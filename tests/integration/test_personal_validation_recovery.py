"""Exact retry and strict zero-write recovery tests for Personal attempts."""

from __future__ import annotations

from pathlib import Path

import pytest
from personal_validation_fixtures import (
    InjectedCrash,
    alternate_system_snapshot,
    fresh_service,
    prepared_context,
    session_ref,
    tree_state,
)

from envresearch.personal_validation.contracts import (
    PersonalValidationAttempt,
    PersonalValidationProtocol,
)
from envresearch.personal_validation.errors import PersonalValidationError
from envresearch.personal_validation.service import PersonalValidationService
from envresearch.storage.secure_journal_records import JournalHead


@pytest.mark.parametrize("boundary", ["attempt-object", "completion-event"])
def test_prepare_recovers_exact_crash_boundary(tmp_path: Path, boundary: str) -> None:
    context = prepared_context(tmp_path, failure_boundary=boundary)
    with pytest.raises(InjectedCrash, match=boundary):
        context.service.prepare_correct_stop(
            context.protocol_ref,
            context.case_ref,
            context.inspection_ref,
            context.inspection,
        )
    context.store.close()
    retry_store, retry = fresh_service(context, writable=True)
    try:
        recovered = retry.prepare_correct_stop(
            context.protocol_ref,
            context.case_ref,
            context.inspection_ref,
            context.inspection,
        )
        assert retry.status(recovered.session_ref).attempt_refs == (
            recovered.attempt_ref,
        )
        assert len(retry.store.read_events()) == 2
    finally:
        retry_store.close()


def test_other_case_cannot_consume_an_orphan_attempt_event_slot(tmp_path: Path) -> None:
    context = prepared_context(tmp_path, failure_boundary="attempt-object")
    with pytest.raises(InjectedCrash):
        context.service.prepare_correct_stop(
            context.protocol_ref,
            context.case_ref,
            context.inspection_ref,
            context.inspection,
        )
    protocol = context.store.load(context.protocol_ref, PersonalValidationProtocol)
    other_case_ref = next(
        binding.case_ref
        for binding in protocol.cases
        if binding.kind == "data-method-incompatibility"
    )
    retry = PersonalValidationService(
        store=context.store,
        factory_service=None,
        session_nonce="persisted-session-nonce",
        system_snapshot_ref=context.service.system_snapshot_ref,
        attempt_inventory_ref=context.inventory_ref,
    )
    before = tree_state(context.authority.private_root)
    try:
        with pytest.raises(PersonalValidationError) as captured:
            retry.prepare_correct_stop(
                context.protocol_ref,
                other_case_ref,
                context.inspection_ref,
                context.inspection,
            )
        assert captured.value.finding_kind == "attempt-retry-divergent"
        assert tree_state(context.authority.private_root) == before
        recovered = retry.prepare_correct_stop(
            context.protocol_ref,
            context.case_ref,
            context.inspection_ref,
            context.inspection,
        )
        assert retry.status(recovered.session_ref).attempt_refs == (
            recovered.attempt_ref,
        )
    finally:
        context.store.close()


def test_retry_after_completed_event_is_exactly_zero_write(tmp_path: Path) -> None:
    context = prepared_context(tmp_path)
    first = context.service.prepare_correct_stop(
        context.protocol_ref,
        context.case_ref,
        context.inspection_ref,
        context.inspection,
    )
    before = tree_state(context.authority.private_root)

    assert (
        context.service.prepare_correct_stop(
            context.protocol_ref,
            context.case_ref,
            context.inspection_ref,
            context.inspection,
        )
        == first
    )
    assert tree_state(context.authority.private_root) == before
    context.store.close()


def test_status_rejects_orphan_attempt_without_repair_or_write(
    tmp_path: Path,
) -> None:
    context = prepared_context(tmp_path, failure_boundary="attempt-object")
    with pytest.raises(InjectedCrash):
        context.service.prepare_correct_stop(
            context.protocol_ref,
            context.case_ref,
            context.inspection_ref,
            context.inspection,
        )
    expected_session_ref = session_ref(context)
    context.store.close()
    before = tree_state(context.authority.private_root)
    store, service = fresh_service(context, writable=False)
    try:
        with pytest.raises(PersonalValidationError) as captured:
            service.status(expected_session_ref)
        assert captured.value.finding_kind == "attempt-event-incomplete"
        assert tree_state(context.authority.private_root) == before
    finally:
        store.close()


@pytest.mark.parametrize(
    "damage",
    [
        "missing-head",
        "missing-key",
        "missing-lock",
        "missing-anchor",
        "truncation",
    ],
)
def test_status_is_zero_write_under_corrupt_control_state(
    tmp_path: Path, damage: str
) -> None:
    context = prepared_context(tmp_path)
    prepared = context.service.prepare_correct_stop(
        context.protocol_ref,
        context.case_ref,
        context.inspection_ref,
        context.inspection,
    )
    context.store.close()
    control = context.authority.private_root / "control/journal"
    if damage == "missing-key":
        (control / "queue.key").unlink()
    elif damage == "missing-head":
        next((control / "journal-heads").iterdir()).unlink()
    elif damage == "missing-lock":
        next((control / "journal-locks").iterdir()).unlink()
    elif damage == "missing-anchor":
        next((control / "journal-lock-anchors").iterdir()).unlink()
    else:
        journal = context.authority.private_root / "journals/personal-validation.jsonl"
        data = journal.read_bytes()
        journal.write_bytes(data[: max(1, len(data) // 2)])
    before = tree_state(context.authority.private_root)

    with pytest.raises((PersonalValidationError, FileNotFoundError, ValueError)):
        store = context.service.store.open_existing(
            context.authority.private_root, context.authority.exclusions
        )
        try:
            service = context.service.__class__(
                store=store,
                factory_service=None,
                session_nonce="persisted-session-nonce",
                system_snapshot_ref=context.service.system_snapshot_ref,
                attempt_inventory_ref=context.inventory_ref,
            )
            service.status(prepared.session_ref)
        finally:
            store.close()
    assert tree_state(context.authority.private_root) == before


def test_read_only_status_reports_lagging_head_without_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = prepared_context(tmp_path)
    prepared = context.service.prepare_correct_stop(
        context.protocol_ref,
        context.case_ref,
        context.inspection_ref,
        context.inspection,
    )

    def fail_head(_: object) -> None:
        raise OSError("injected crash before head publication")

    monkeypatch.setattr(context.store.journal, "_write_head", fail_head)
    with pytest.raises(OSError, match="injected crash"):
        context.store.journal.append(
            {
                "schema_version": "personal.validation-event.v1",
                "event_id": "unreachable-lag-record",
            }
        )
    context.store.close()
    before = tree_state(context.authority.private_root)

    with pytest.raises((PersonalValidationError, ValueError), match="recovery"):
        store, service = fresh_service(context, writable=False)
        try:
            service.status(prepared.session_ref)
        finally:
            store.close()
    assert tree_state(context.authority.private_root) == before


def test_divergent_retry_does_not_reconcile_lagging_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = prepared_context(tmp_path)
    divergent_system_ref = alternate_system_snapshot(context)
    real_write_head = context.store.journal._write_head

    def crash_before_completion_head(head: JournalHead) -> None:
        if head.record_count == 2:
            raise OSError("injected completion-head crash")
        real_write_head(head)

    monkeypatch.setattr(
        context.store.journal, "_write_head", crash_before_completion_head
    )
    with pytest.raises(PersonalValidationError):
        context.service.prepare_correct_stop(
            context.protocol_ref,
            context.case_ref,
            context.inspection_ref,
            context.inspection,
        )
    context.store.close()
    before = tree_state(context.authority.private_root)

    store, divergent = fresh_service(
        context, writable=True, system_snapshot_ref=divergent_system_ref
    )
    try:
        with pytest.raises(PersonalValidationError) as captured:
            divergent.prepare_correct_stop(
                context.protocol_ref,
                context.case_ref,
                context.inspection_ref,
                context.inspection,
            )
        assert captured.value.finding_kind == "attempt-retry-divergent"
    finally:
        store.close()
    assert tree_state(context.authority.private_root) == before


def test_exact_retry_repairs_only_its_lagging_completion_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = prepared_context(tmp_path)
    real_write_head = context.store.journal._write_head

    def crash_before_completion_head(head: JournalHead) -> None:
        if head.record_count == 2:
            raise OSError("injected completion-head crash")
        real_write_head(head)

    monkeypatch.setattr(
        context.store.journal, "_write_head", crash_before_completion_head
    )
    with pytest.raises(PersonalValidationError):
        context.service.prepare_correct_stop(
            context.protocol_ref,
            context.case_ref,
            context.inspection_ref,
            context.inspection,
        )
    context.store.close()
    before_open = tree_state(context.authority.private_root)
    journal_path = context.authority.private_root / "journals/personal-validation.jsonl"
    journal_bytes = journal_path.read_bytes()
    object_state = tree_state(context.authority.private_root / "objects")

    store, retry = fresh_service(context, writable=True)
    try:
        assert tree_state(context.authority.private_root) == before_open
        recovered = retry.prepare_correct_stop(
            context.protocol_ref,
            context.case_ref,
            context.inspection_ref,
            context.inspection,
        )
        assert retry.status(recovered.session_ref).attempt_refs == (
            recovered.attempt_ref,
        )
    finally:
        store.close()

    assert journal_path.read_bytes() == journal_bytes
    assert tree_state(context.authority.private_root / "objects") == object_state
    assert tree_state(context.authority.private_root) != before_open


def test_completion_append_does_not_reconcile_malformed_lagging_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = prepared_context(tmp_path)
    real_publish = context.store.publish
    real_write_head = context.store.journal._write_head
    lagging: list[tuple[tuple[object, ...], ...]] = []

    def publish_then_inject(artifact_id: str, payload: object) -> object:
        published = real_publish(artifact_id, payload)  # type: ignore[arg-type]
        if not isinstance(payload, PersonalValidationAttempt):
            return published

        def crash_before_malformed_head(head: JournalHead) -> None:
            raise OSError("injected malformed-head crash")

        monkeypatch.setattr(
            context.store.journal, "_write_head", crash_before_malformed_head
        )
        try:
            with pytest.raises(OSError, match="malformed-head"):
                context.store.journal.append({"schema_version": "not-a-personal-event"})
        finally:
            monkeypatch.setattr(context.store.journal, "_write_head", real_write_head)
        lagging.append(tree_state(context.authority.private_root))
        return published

    monkeypatch.setattr(context.store, "publish", publish_then_inject)
    try:
        with pytest.raises(PersonalValidationError) as captured:
            context.service.prepare_correct_stop(
                context.protocol_ref,
                context.case_ref,
                context.inspection_ref,
                context.inspection,
            )
        assert captured.value.finding_kind == "event-history-invalid"
        assert tree_state(context.authority.private_root) == lagging[0]
    finally:
        context.store.close()


def test_exact_retry_recovers_lagging_session_start_before_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = prepared_context(tmp_path)
    real_write_head = context.store.journal._write_head

    def crash_before_start_head(head: JournalHead) -> None:
        if head.record_count == 1:
            raise OSError("injected start-head crash")
        real_write_head(head)

    monkeypatch.setattr(context.store.journal, "_write_head", crash_before_start_head)
    with pytest.raises(PersonalValidationError):
        context.service.prepare_correct_stop(
            context.protocol_ref,
            context.case_ref,
            context.inspection_ref,
            context.inspection,
        )
    context.store.close()

    store, retry = fresh_service(context, writable=True)
    try:
        recovered = retry.prepare_correct_stop(
            context.protocol_ref,
            context.case_ref,
            context.inspection_ref,
            context.inspection,
        )
        assert len(store.read_events()) == 2
        assert retry.status(recovered.session_ref).attempt_refs == (
            recovered.attempt_ref,
        )
    finally:
        store.close()
