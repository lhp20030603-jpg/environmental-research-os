from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from envresearch.storage.secure_journal import SecureJournal
from envresearch.storage.secure_journal_records import (
    JournalHead,
    canonical,
    seal_head,
    verify_head,
)
from envresearch.workers.filesystem import PinnedRoot


@dataclass(frozen=True)
class JournalCase:
    path: Path
    storage_pin: PinnedRoot
    control_pin: PinnedRoot
    journal_id: str

    @property
    def head_path(self) -> Path:
        return self.control_pin.path / "journal-heads" / f"{self.journal_id}.json"

    def remove(self, missing: str) -> None:
        paths = {
            "key": self.control_pin.path / "queue.key",
            "head": self.head_path,
            "lock": self.control_pin.path
            / "journal-locks"
            / f"{self.journal_id}.filelock",
            "anchor": self.control_pin.path
            / "journal-lock-anchors"
            / f"{self.journal_id}.json",
        }
        paths[missing].unlink()

    def rewrite_predecessor_head(self, field: str) -> None:
        key = (self.control_pin.path / "queue.key").read_bytes()
        head = verify_head(self.head_path.read_bytes(), key)
        values = head.model_dump(exclude={"mac"})
        values[field] = "f" * 64 if field == "journal_id" else head.size_bytes - 1
        replacement = seal_head(key, values)
        self.head_path.write_bytes(canonical(replacement.model_dump()))

    def tree_state(self) -> tuple[tuple[str, str, int, bytes], ...]:
        entries: list[tuple[str, str, int, bytes]] = []
        for label, root in (
            ("storage", self.storage_pin.path),
            ("control", self.control_pin.path),
        ):
            for path in sorted((root, *root.rglob("*"))):
                metadata = path.lstat()
                kind = "directory" if stat.S_ISDIR(metadata.st_mode) else "file"
                data = b"" if kind == "directory" else path.read_bytes()
                entries.append(
                    (
                        f"{label}/{path.relative_to(root)}",
                        kind,
                        stat.S_IMODE(metadata.st_mode),
                        data,
                    )
                )
        return tuple(entries)

    def append_record_without_head(self, payload: dict[str, Any]) -> None:
        journal = SecureJournal(
            self.path,
            storage_root=self.storage_pin.path,
            control_root=self.control_pin.path,
        )

        def fail_before_head(head: JournalHead) -> None:
            if head.record_count == 2:
                raise RuntimeError("simulated crash before head")

        journal._write_head = fail_before_head  # type: ignore[method-assign]
        try:
            with pytest.raises(RuntimeError, match="simulated crash"):
                journal.append(payload)
        finally:
            journal.close()


@pytest.fixture
def journal_case(tmp_path: Path) -> JournalCase:
    storage = tmp_path / "storage"
    control = tmp_path / "control"
    path = storage / "personal.jsonl"
    journal = SecureJournal(path, storage_root=storage, control_root=control)
    journal.ensure()
    journal.append({"event_id": "session.00000001"})
    journal_id = journal._journal_id
    journal.close()
    storage_pin = PinnedRoot(storage, create=False)
    control_pin = PinnedRoot(control, private=True, create=False)
    try:
        yield JournalCase(path, storage_pin, control_pin, journal_id)
    finally:
        storage_pin.close()
        control_pin.close()


@pytest.mark.parametrize("missing", ["key", "head", "lock", "anchor"])
def test_open_existing_read_all_never_recreates_control_state(
    journal_case: JournalCase, missing: str
) -> None:
    journal_case.remove(missing)
    before = journal_case.tree_state()
    with (
        pytest.raises((FileNotFoundError, ValueError)),
        SecureJournal.open_existing(
            journal_case.path,
            storage_root=journal_case.storage_pin,
            control_root=journal_case.control_pin,
            reconcile=False,
        ) as journal,
    ):
        journal.read_all()
    assert journal_case.tree_state() == before


def test_lagging_head_is_reported_without_reconciliation(
    journal_case: JournalCase,
) -> None:
    journal_case.append_record_without_head({"event_id": "session.00000002"})
    before = journal_case.tree_state()
    with (
        SecureJournal.open_existing(
            journal_case.path,
            storage_root=journal_case.storage_pin,
            control_root=journal_case.control_pin,
            reconcile=False,
        ) as journal,
        pytest.raises(ValueError, match="recovery required"),
    ):
        journal.read_all()
    assert journal_case.tree_state() == before


def test_create_from_pinned_borrows_caller_roots(tmp_path: Path) -> None:
    storage = PinnedRoot(tmp_path / "storage")
    control = PinnedRoot(tmp_path / "control", private=True)
    path = storage.path / "personal.jsonl"
    try:
        with SecureJournal.create_from_pinned(
            path,
            storage_root=storage,
            control_root=control,
        ) as journal:
            journal.ensure()
            journal.append({"event_id": "session.00000001"})

        storage.require_attached()
        control.require_attached()
        assert storage.read_file(Path("personal.jsonl"), description="borrowed journal")
    finally:
        control.close()
        storage.close()


def test_open_for_recovery_repairs_only_the_lagging_head(
    journal_case: JournalCase,
) -> None:
    journal_case.append_record_without_head({"event_id": "session.00000002"})
    storage_before = journal_case.path.read_bytes()

    with SecureJournal.open_for_recovery(
        journal_case.path,
        storage_root=journal_case.storage_pin,
        control_root=journal_case.control_pin,
    ) as journal:
        assert journal.read_all() == [
            {"event_id": "session.00000001"},
            {"event_id": "session.00000002"},
        ]

    assert journal_case.path.read_bytes() == storage_before
    with SecureJournal.open_existing(
        journal_case.path,
        storage_root=journal_case.storage_pin,
        control_root=journal_case.control_pin,
        reconcile=False,
    ) as journal:
        assert len(journal.read_all()) == 2


def test_recovery_refuses_more_than_one_lagging_record(
    journal_case: JournalCase,
) -> None:
    original_head = journal_case.head_path.read_bytes()
    writer = SecureJournal(
        journal_case.path,
        storage_root=journal_case.storage_pin.path,
        control_root=journal_case.control_pin.path,
    )
    writer.append({"event_id": "session.00000002"})
    writer.append({"event_id": "session.00000003"})
    writer.close()
    journal_case.head_path.write_bytes(original_head)
    before = journal_case.tree_state()

    with (
        pytest.raises(ValueError, match="lags by more than one"),
        SecureJournal.open_for_recovery(
            journal_case.path,
            storage_root=journal_case.storage_pin,
            control_root=journal_case.control_pin,
        ) as journal,
    ):
        journal.read_all()
    assert journal_case.tree_state() == before


@pytest.mark.parametrize("field", ["journal_id", "size_bytes"])
def test_recovery_rejects_inconsistent_authenticated_predecessor_head(
    journal_case: JournalCase, field: str
) -> None:
    journal_case.append_record_without_head({"event_id": "session.00000002"})
    journal_case.rewrite_predecessor_head(field)
    before = journal_case.tree_state()

    with (
        pytest.raises(ValueError, match="authenticated head"),
        SecureJournal.open_for_recovery(
            journal_case.path,
            storage_root=journal_case.storage_pin,
            control_root=journal_case.control_pin,
        ) as journal,
    ):
        journal.read_all()
    assert journal_case.tree_state() == before


@pytest.mark.parametrize("missing", ["key", "head", "lock", "anchor"])
def test_recovery_never_recreates_missing_control_state(
    journal_case: JournalCase, missing: str
) -> None:
    journal_case.remove(missing)
    before = journal_case.tree_state()
    with (
        pytest.raises((FileNotFoundError, ValueError)),
        SecureJournal.open_for_recovery(
            journal_case.path,
            storage_root=journal_case.storage_pin,
            control_root=journal_case.control_pin,
        ) as journal,
    ):
        journal.read_all()
    assert journal_case.tree_state() == before


def test_read_only_capability_rejects_every_mutation_before_validation(
    journal_case: JournalCase,
) -> None:
    before = journal_case.tree_state()
    with SecureJournal.open_existing(
        journal_case.path,
        storage_root=journal_case.storage_pin,
        control_root=journal_case.control_pin,
        reconcile=False,
    ) as journal:
        operations = (
            lambda: journal.ensure(),
            lambda: journal.append({"_journal": "reserved"}),
            lambda: journal.append_unique({}, identity_fields=()),
        )
        for operation in operations:
            with pytest.raises(RuntimeError, match="read-only"):
                operation()
    assert journal_case.tree_state() == before


def test_open_existing_rejects_reconciliation_request_without_writes(
    journal_case: JournalCase,
) -> None:
    before = journal_case.tree_state()
    with pytest.raises(ValueError, match="explicitly false"):
        SecureJournal.open_existing(
            journal_case.path,
            storage_root=journal_case.storage_pin,
            control_root=journal_case.control_pin,
            reconcile=True,  # type: ignore[arg-type]
        )
    assert journal_case.tree_state() == before


def test_repeated_existing_contexts_have_bounded_descriptors(
    journal_case: JournalCase,
) -> None:
    before = _descriptor_count()
    for _ in range(25):
        with SecureJournal.open_existing(
            journal_case.path,
            storage_root=journal_case.storage_pin,
            control_root=journal_case.control_pin,
            reconcile=False,
        ) as journal:
            assert len(journal.read_all()) == 1
        journal.close()
    assert _descriptor_count() == before


def test_failed_existing_construction_does_not_close_or_leak_borrowed_roots(
    journal_case: JournalCase,
) -> None:
    journal_case.remove("anchor")
    before = _descriptor_count()
    for _ in range(10):
        with pytest.raises(FileNotFoundError):
            SecureJournal.open_existing(
                journal_case.path,
                storage_root=journal_case.storage_pin,
                control_root=journal_case.control_pin,
                reconcile=False,
            )
    assert _descriptor_count() == before
    journal_case.storage_pin.require_attached()
    journal_case.control_pin.require_attached()


def test_unsafe_journal_validation_has_bounded_descriptors(
    journal_case: JournalCase,
) -> None:
    journal_case.path.chmod(0o644)
    before = _descriptor_count()
    for _ in range(10):
        with pytest.raises(ValueError, match="owner-only"):
            SecureJournal.open_existing(
                journal_case.path,
                storage_root=journal_case.storage_pin,
                control_root=journal_case.control_pin,
                reconcile=False,
            )
    assert _descriptor_count() == before


def test_parent_replacement_validation_has_bounded_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage_path, control_path = tmp_path / "storage", tmp_path / "control"
    path = storage_path / "logs/personal.jsonl"
    with SecureJournal(
        path, storage_root=storage_path, control_root=control_path
    ) as writer:
        writer.ensure()
    storage = PinnedRoot(storage_path, create=False)
    control = PinnedRoot(control_path, private=True, create=False)
    real_open = storage.open_directory
    armed = False
    moved = storage.path / "moved"

    def replace_parent(relative: Path, *, create: bool = False) -> int:
        nonlocal armed
        descriptor = real_open(relative, create=create)
        if armed and relative == Path("logs"):
            armed = False
            (storage.path / "logs").rename(moved)
            (storage.path / "logs").mkdir(mode=0o700)
        return descriptor

    monkeypatch.setattr(storage, "open_directory", replace_parent)
    try:
        before = _descriptor_count()
        for _ in range(10):
            armed = True
            with pytest.raises(ValueError, match="parent directory changed"):
                SecureJournal.open_existing(
                    path,
                    storage_root=storage,
                    control_root=control,
                    reconcile=False,
                )
            (storage.path / "logs").rmdir()
            moved.rename(storage.path / "logs")
        assert _descriptor_count() == before
    finally:
        control.close()
        storage.close()


def _descriptor_count() -> int:
    fd_root = "/dev/fd"
    return len(os.listdir(fd_root if Path(fd_root).is_dir() else "/proc/self/fd"))
