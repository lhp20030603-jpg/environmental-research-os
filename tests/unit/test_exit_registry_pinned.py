"""Descriptor-pinned exit-registry behavior."""

import os
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.storage.secure_journal import SecureJournal
from envresearch.storage.secure_journal_records import JournalHead
from envresearch.workers import filesystem
from envresearch.workers.filesystem import PinnedRoot


class Example(BaseModel):
    """Minimal canonical registry payload."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    value: str


def test_pinned_registry_does_not_follow_replaced_lexical_root(
    tmp_path: Path,
) -> None:
    lexical = tmp_path / "objects"
    pinned = PinnedRoot(lexical, private=True)
    registry = ExitRegistry.from_pinned(pinned, create=True)
    ref = registry.publish("example", Example(value="before"))
    lexical.rename(tmp_path / "original")
    lexical.mkdir(mode=0o700)

    with pytest.raises(ValueError, match="root identity changed"):
        registry.load(ref, Example)


def test_pinned_registry_lock_never_reopens_lexical_root(tmp_path: Path) -> None:
    pinned = PinnedRoot(tmp_path / "objects", private=True)
    registry = ExitRegistry.from_pinned(pinned, create=True)
    with registry.lock("personal-session"):
        (tmp_path / "objects").rename(tmp_path / "moved")
        (tmp_path / "objects").mkdir(mode=0o700)
    with pytest.raises(ValueError, match="root identity changed"):
        pinned.require_attached()


def test_pinned_root_context_close_is_idempotent(tmp_path: Path) -> None:
    with PinnedRoot(tmp_path / "objects", private=True) as pinned:
        descriptor = pinned.fd

    with pytest.raises(OSError):
        os.fstat(descriptor)
    pinned.close()


def test_returned_child_pins_reject_relocation_beneath_replacement_top(
    tmp_path: Path,
) -> None:
    top = PinnedRoot(tmp_path / "top", private=True)
    storage = top.open_child_root(Path("journals"), private=True, create=True)
    control = top.open_child_root(Path("control"), private=True, create=True)
    moved = tmp_path / "moved"
    top.path.rename(moved)
    top.path.mkdir(mode=0o700)
    (moved / "journals").rename(top.path / "journals")
    (moved / "control").rename(top.path / "control")
    try:
        with pytest.raises(ValueError, match="root identity changed"):
            SecureJournal.create_from_pinned(
                storage.path / "personal.jsonl",
                storage_root=storage,
                control_root=control,
            )
        assert tuple(storage.path.iterdir()) == ()
        assert tuple(control.path.iterdir()) == ()
    finally:
        control.close()
        storage.close()
        top.close()


def test_failed_child_construction_closes_descriptor_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    top = PinnedRoot(tmp_path / "top", private=True)
    opened: list[int] = []
    closed: list[int] = []
    real_open = top.open_directory
    real_close = os.close

    def track_open(relative: Path, *, create: bool = False) -> int:
        descriptor = real_open(relative, create=create)
        opened.append(descriptor)
        return descriptor

    def fail_private_root(descriptor: int, *, create: bool) -> None:
        raise ValueError("injected child validation failure")

    def track_close(descriptor: int) -> None:
        if opened and descriptor == opened[-1]:
            closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(top, "open_directory", track_open)
    monkeypatch.setattr(filesystem, "_require_private_root", fail_private_root)
    monkeypatch.setattr(filesystem.os, "close", track_close)
    try:
        with pytest.raises(ValueError, match="injected child validation"):
            top.open_child_root(Path("journals"), private=True, create=True)
        assert len(closed) == 1
    finally:
        top.close()


def test_child_pin_fails_closed_when_top_root_is_replaced_mid_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    top = PinnedRoot(tmp_path / "top", private=True)
    real_open = top.open_directory

    def replace_after_open(relative: Path, *, create: bool = False) -> int:
        descriptor = real_open(relative, create=create)
        top.path.rename(tmp_path / "moved")
        top.path.mkdir(mode=0o700)
        return descriptor

    monkeypatch.setattr(top, "open_directory", replace_after_open)
    try:
        with pytest.raises(ValueError, match="root identity changed"):
            top.open_child_root(Path("journals"), private=True, create=True)
        assert tuple(top.path.iterdir()) == ()
    finally:
        top.close()


def test_child_pin_rejects_relocation_into_replacement_top(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    top = PinnedRoot(tmp_path / "top", private=True)
    real_require = top.require_attached
    checks = 0

    def replace_after_parent_validation() -> None:
        nonlocal checks
        real_require()
        checks += 1
        if checks == 2:
            top.path.rename(tmp_path / "moved")
            top.path.mkdir(mode=0o700)
            (tmp_path / "moved/journals").rename(top.path / "journals")

    monkeypatch.setattr(top, "require_attached", replace_after_parent_validation)
    try:
        with pytest.raises((FileNotFoundError, ValueError), match="parent|identity"):
            top.open_child_root(Path("journals"), private=True, create=True)
    finally:
        top.close()


def test_pinned_journal_never_rebinds_children_after_top_replacement(
    tmp_path: Path,
) -> None:
    top = PinnedRoot(tmp_path / "top", private=True)
    storage = top.open_child_root(Path("journals"), private=True, create=True)
    control = top.open_child_root(Path("control"), private=True, create=True)
    top.path.rename(tmp_path / "moved")
    top.path.mkdir(mode=0o700)
    (top.path / "journals").mkdir(mode=0o700)
    (top.path / "control").mkdir(mode=0o700)
    try:
        with pytest.raises(ValueError, match="root identity changed"):
            SecureJournal.create_from_pinned(
                top.path / "journals/personal.jsonl",
                storage_root=storage,
                control_root=control,
            )
        assert tuple((top.path / "journals").iterdir()) == ()
        assert tuple((top.path / "control").iterdir()) == ()
    finally:
        control.close()
        storage.close()
        top.close()


def test_owned_journal_close_uses_reverse_order_after_control_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = SecureJournal(tmp_path / "personal.jsonl")
    storage_fd = journal.storage.fd
    control_fd = journal.control.fd
    real_control_close = journal.control.close
    order: list[str] = []

    def fail_after_control_close() -> None:
        order.append("control")
        real_control_close()
        raise OSError("injected control close failure")

    def close_storage() -> None:
        order.append("storage")
        PinnedRoot.close(journal.storage)

    monkeypatch.setattr(journal.control, "close", fail_after_control_close)
    monkeypatch.setattr(journal.storage, "close", close_storage)
    with pytest.raises(OSError, match="injected control"):
        journal.close()
    journal.close()
    assert order == ["control", "storage"]
    with pytest.raises(OSError):
        os.fstat(control_fd)
    with pytest.raises(OSError):
        os.fstat(storage_fd)


def test_pinned_journal_accepts_canonical_path_beneath_lexical_alias(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    alias_parent = tmp_path / "alias"
    alias_parent.symlink_to(real_parent, target_is_directory=True)
    storage = PinnedRoot(alias_parent / "storage")
    control = PinnedRoot(alias_parent / "control", private=True)
    path = storage.path / "personal.jsonl"
    try:
        with SecureJournal.create_from_pinned(
            path, storage_root=storage, control_root=control
        ) as writer:
            writer.ensure()
            writer.append({"event_id": "session.00000001"})
        with SecureJournal.open_existing(
            path, storage_root=storage, control_root=control, reconcile=False
        ) as reader:
            assert reader.read_all() == [{"event_id": "session.00000001"}]
    finally:
        control.close()
        storage.close()


def test_pinned_writer_rejects_relocation_immediately_before_first_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    top = PinnedRoot(tmp_path / "top", private=True)
    storage = top.open_child_root(Path("journals"), private=True, create=True)
    control = top.open_child_root(Path("control"), private=True, create=True)
    real_ensure = control.ensure_directory
    relocated = False

    def relocate_then_ensure(relative: Path) -> None:
        nonlocal relocated
        if not relocated:
            relocated = True
            _relocate_children_beneath_replacement(top, "journals", "control")
        real_ensure(relative)

    monkeypatch.setattr(control, "ensure_directory", relocate_then_ensure)
    try:
        with pytest.raises(ValueError, match="root identity changed"):
            SecureJournal.create_from_pinned(
                storage.path / "personal.jsonl",
                storage_root=storage,
                control_root=control,
            )
        assert tuple(storage.path.iterdir()) == ()
        assert tuple(control.path.iterdir()) == ()
    finally:
        control.close()
        storage.close()
        top.close()


def test_recovery_rejects_relocation_immediately_before_head_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    top = PinnedRoot(tmp_path / "top", private=True)
    storage = top.open_child_root(Path("journals"), private=True, create=True)
    control = top.open_child_root(Path("control"), private=True, create=True)
    path = storage.path / "personal.jsonl"
    writer = SecureJournal.create_from_pinned(
        path, storage_root=storage, control_root=control
    )
    try:
        writer.ensure()
        writer.append({"event_id": "session.00000001"})
        real_writer_head = writer._write_head

        def crash_before_second_head(head: JournalHead) -> None:
            if head.record_count == 2:
                raise RuntimeError("simulated crash before head")
            real_writer_head(head)

        monkeypatch.setattr(writer, "_write_head", crash_before_second_head)
        with pytest.raises(RuntimeError, match="simulated crash"):
            writer.append({"event_id": "session.00000002"})
    finally:
        writer.close()

    recovery = SecureJournal.open_for_recovery(
        path, storage_root=storage, control_root=control
    )
    before = _tree_bytes(storage.path), _tree_bytes(control.path)
    real_write = recovery._write_head

    def relocate_then_write(head: JournalHead) -> None:
        _relocate_children_beneath_replacement(top, "journals", "control")
        real_write(head)

    monkeypatch.setattr(recovery, "_write_head", relocate_then_write)
    try:
        with pytest.raises(ValueError, match="root identity changed"):
            recovery.read_all()
        assert (_tree_bytes(storage.path), _tree_bytes(control.path)) == before
    finally:
        recovery.close()
        control.close()
        storage.close()
        top.close()


def _relocate_children_beneath_replacement(top: PinnedRoot, *names: str) -> None:
    moved = top.path.with_name("moved")
    top.path.rename(moved)
    top.path.mkdir(mode=0o700)
    for name in names:
        (moved / name).rename(top.path / name)


def _tree_bytes(root: Path) -> tuple[tuple[str, bytes], ...]:
    return tuple(
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )
