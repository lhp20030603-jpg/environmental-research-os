"""Adversarial filesystem tests for mandatory research audit journals."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from envresearch.kernel.decision_log import DecisionLog, DecisionLogEntry
from envresearch.storage import secure_journal as secure_journal_module
from envresearch.storage.secure_journal_records import JournalHead


def _entry(identity: str = "audit-1") -> DecisionLogEntry:
    return DecisionLogEntry(
        event_id=identity,
        timestamp=datetime(2026, 8, 6, 8, 0, tzinfo=UTC),
        actor="human-owner",
        decision_kind="gate_decision",
        status="approved",
        subject="gate-1",
        reason="Approved exact current artifacts.",
    )


@pytest.mark.parametrize("alias", ("symlink", "hardlink"))
def test_decision_log_never_appends_through_external_alias(
    tmp_path: Path, alias: str
) -> None:
    """Replacing a ledger with an alias must not modify its external target."""
    run = tmp_path / "run"
    run.mkdir()
    victim = tmp_path / "victim.jsonl"
    victim.write_bytes(b"")
    ledger = run / "decision-log.jsonl"
    if alias == "symlink":
        ledger.symlink_to(victim)
    else:
        os.link(victim, ledger)

    with pytest.raises((OSError, ValueError), match="symlink|regular|link count"):
        DecisionLog(ledger).append(_entry())

    assert victim.read_bytes() == b""


def test_decision_log_fails_closed_after_parent_directory_swap(
    tmp_path: Path,
) -> None:
    """A pinned run root cannot silently continue through a replacement path."""
    run = tmp_path / "run"
    run.mkdir()
    log = DecisionLog(run / "decision-log.jsonl")
    displaced = tmp_path / "displaced"
    run.rename(displaced)
    run.mkdir()

    with pytest.raises(ValueError, match="root.*changed|attached"):
        log.append(_entry())

    assert not (run / "decision-log.jsonl").exists()
    assert not (displaced / "decision-log.jsonl").exists()


def test_decision_log_detects_authenticated_history_truncation(tmp_path: Path) -> None:
    """Deleting a durable record cannot turn an audited history into an empty one."""
    path = tmp_path / "decision-log.jsonl"
    log = DecisionLog(path)
    log.append(_entry())
    path.write_bytes(b"")

    with pytest.raises(ValueError, match="head|truncat|authentication"):
        DecisionLog(path).read_all()


def test_decision_log_requires_expected_owner_for_protected_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A protected key owned by another principal is never accepted."""
    path = tmp_path / "decision-log.jsonl"
    log = DecisionLog(path)
    log.append(_entry())
    log.close()
    owner = os.geteuid()
    monkeypatch.setattr(secure_journal_module.os, "geteuid", lambda: owner + 1)

    with pytest.raises(ValueError, match="ownership"):
        DecisionLog(path)


@pytest.mark.parametrize("alias", ("symlink", "hardlink", "fifo"))
def test_decision_log_refuses_aliased_private_lock(tmp_path: Path, alias: str) -> None:
    """An aliased coordination lock cannot redirect or authorize journal writes."""
    path = tmp_path / "run" / "decision-log.jsonl"
    log = DecisionLog(path)
    journal = log._journal
    lock = journal.control.path / "journal-locks" / f"{journal._journal_id}.filelock"
    victim = tmp_path / "lock-victim"
    victim.write_bytes(b"")
    victim.chmod(0o600)
    if alias == "symlink":
        lock.symlink_to(victim)
    elif alias == "hardlink":
        os.link(victim, lock)
    else:
        os.mkfifo(lock, 0o600)

    with pytest.raises((OSError, ValueError), match="lock|regular|symlink"):
        log.append(_entry())

    assert victim.read_bytes() == b""
    assert not path.exists()


def test_decision_log_detects_lock_name_replacement_while_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replacing a held lock name cannot create a second critical section."""
    path = tmp_path / "run" / "decision-log.jsonl"
    log = DecisionLog(path)
    journal = log._journal
    lock = journal.control.path / "journal-locks" / f"{journal._journal_id}.filelock"
    displaced = lock.with_suffix(".displaced")
    real_attached = journal._require_attached
    control_checks = 0

    def replace_during_lock(root: object) -> None:
        nonlocal control_checks
        real_attached(root)  # type: ignore[arg-type]
        if root is journal.control:
            control_checks += 1
            if control_checks == 2:
                lock.rename(displaced)
                lock.write_bytes(b"")
                lock.chmod(0o600)

    monkeypatch.setattr(journal, "_require_attached", replace_during_lock)
    with pytest.raises(ValueError, match="lock.*replacement|lock.*changed"):
        log.append(_entry())

    assert displaced.exists()
    assert lock.exists()


def test_lock_replacement_cannot_open_two_writer_critical_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replacement lock cannot commit while the pinned control root is locked."""
    path = tmp_path / "run" / "decision-log.jsonl"
    first = DecisionLog(path)
    second = DecisionLog(path)
    journal = first._journal
    entered = threading.Event()
    release = threading.Event()
    second_finished = threading.Event()
    errors: list[Exception] = []
    real_append = journal._append_to_descriptor

    def pause_first(*args: object, **kwargs: object) -> None:
        entered.set()
        if not release.wait(timeout=5):
            raise TimeoutError("first writer was not released")
        real_append(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(journal, "_append_to_descriptor", pause_first)

    def append(
        log: DecisionLog, entry: DecisionLogEntry, done: threading.Event
    ) -> None:
        try:
            log.append(entry)
        except (OSError, RuntimeError, ValueError) as error:
            errors.append(error)
        finally:
            done.set()

    first_done = threading.Event()
    first_thread = threading.Thread(
        target=append, args=(first, _entry("first"), first_done)
    )
    first_thread.start()
    assert entered.wait(timeout=5)
    lock = journal.control.path / "journal-locks" / f"{journal._journal_id}.filelock"
    lock.rename(lock.with_suffix(".displaced"))
    lock.write_bytes(b"")
    lock.chmod(0o600)
    second_thread = threading.Thread(
        target=append, args=(second, _entry("second"), second_finished)
    )
    second_thread.start()
    assert not second_finished.wait(timeout=0.1)
    release.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert first_done.is_set() and second_finished.is_set()
    assert len(errors) == 2 and all(isinstance(error, ValueError) for error in errors)
    physical = [json.loads(line) for line in path.read_text().splitlines()]
    assert [item["event_id"] for item in physical] == ["first"]

    lock.unlink()
    lock.with_suffix(".displaced").rename(lock)
    second.append(_entry("second"))
    assert [entry.event_id for entry in second.read_all()] == ["first", "second"]


def test_decision_log_detects_replacement_between_open_and_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name swap cannot make a retained journal descriptor write a victim."""
    path = tmp_path / "run" / "decision-log.jsonl"
    log = DecisionLog(path)
    log.ensure()
    displaced = tmp_path / "displaced.jsonl"
    victim = tmp_path / "replacement-victim.jsonl"
    victim.write_bytes(b"")
    real_write = secure_journal_module.write_all

    def swap_then_write(descriptor: int, data: bytes) -> None:
        path.rename(displaced)
        path.symlink_to(victim)
        real_write(descriptor, data)

    monkeypatch.setattr(secure_journal_module, "write_all", swap_then_write)
    with pytest.raises(ValueError, match="replacement|regular|single-link"):
        log.append(_entry())

    assert victim.read_bytes() == b""


def test_authenticated_journal_recovers_crash_after_record_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One authenticated record ahead of its head is reconciled exactly once."""
    path = tmp_path / "decision-log.jsonl"
    log = DecisionLog(path)
    log.ensure()
    real_head = log._journal._write_head

    def crash_before_head(head: JournalHead) -> None:
        if head.record_count == 1:
            raise RuntimeError("crash before head")
        real_head(head)

    monkeypatch.setattr(log._journal, "_write_head", crash_before_head)
    with pytest.raises(RuntimeError, match="before head"):
        log.append(_entry())
    log.close()
    monkeypatch.undo()

    recovered = DecisionLog(path)
    assert recovered.read_all() == [_entry()]
    assert recovered.read_all() == [_entry()]
