"""Capability-specific construction for descriptor-pinned secure journals."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeVar

from envresearch.storage.secure_journal_records import HASH
from envresearch.workers.filesystem import PinnedRoot, directories_overlap

if TYPE_CHECKING:
    from envresearch.storage.secure_journal import SecureJournal

JournalT = TypeVar("JournalT", bound="SecureJournal")


def create_pinned_journal(
    journal_type: type[JournalT],
    *,
    path: Path,
    storage_root: PinnedRoot,
    control_root: PinnedRoot,
) -> JournalT:
    """Create a borrowed writer without transferring root ownership."""
    journal = _borrowed_journal(
        journal_type,
        path=path,
        storage_root=storage_root,
        control_root=control_root,
        writable=True,
        can_create_control=True,
        can_reconcile_head=True,
    )
    try:
        for relative in (
            Path("journal-locks"),
            Path("journal-lock-anchors"),
            Path("journal-heads"),
        ):
            control_root.ensure_directory(relative)
        journal._key = load_or_create_journal_key(control_root)
        return journal
    except BaseException:
        journal.close()
        raise


def open_existing_journal(
    journal_type: type[JournalT],
    *,
    path: Path,
    storage_root: PinnedRoot,
    control_root: PinnedRoot,
    reconcile: Literal[False] = False,
) -> JournalT:
    """Open existing control state with a strict zero-write capability."""
    if reconcile is not False:
        raise ValueError("existing journal reconciliation must be explicitly false")
    journal = _borrowed_journal(
        journal_type,
        path=path,
        storage_root=storage_root,
        control_root=control_root,
        writable=False,
        can_create_control=False,
        can_reconcile_head=False,
    )
    return _load_existing_control(journal)


def open_recovery_journal(
    journal_type: type[JournalT],
    *,
    path: Path,
    storage_root: PinnedRoot,
    control_root: PinnedRoot,
) -> JournalT:
    """Open only the capability needed for an exact lagging-head repair."""
    journal = _borrowed_journal(
        journal_type,
        path=path,
        storage_root=storage_root,
        control_root=control_root,
        writable=False,
        can_create_control=False,
        can_reconcile_head=True,
    )
    return _load_existing_control(journal)


def _borrowed_journal(
    journal_type: type[JournalT],
    *,
    path: Path,
    storage_root: PinnedRoot,
    control_root: PinnedRoot,
    writable: bool,
    can_create_control: bool,
    can_reconcile_head: bool,
) -> JournalT:
    storage_root.require_attached()
    control_root.require_attached()
    if directories_overlap(storage_root.fd, control_root.fd):
        raise ValueError("journal storage and control roots must be separate")
    absolute = Path(os.path.abspath(path))
    relative = _relative_to_captured_root(absolute, storage_root)
    if not relative.parts or relative == Path("."):
        raise ValueError("journal path must name a file beneath its storage root")
    journal = journal_type.__new__(journal_type)
    journal.path = absolute
    journal.storage = storage_root
    journal.control = control_root
    journal._closed = False
    journal._owns_roots = False
    journal._writable = writable
    journal._can_create_control = can_create_control
    journal._can_reconcile_head = can_reconcile_head
    journal._relative = relative
    journal._journal_id = HASH(
        f"{storage_root.path.name}/{relative.as_posix()}".encode()
    ).hexdigest()
    return journal


def _relative_to_captured_root(absolute: Path, root: PinnedRoot) -> Path:
    for captured in (root.path, root.lexical_path):
        try:
            return absolute.relative_to(captured)
        except ValueError:
            pass
    raise ValueError("journal path must remain beneath its storage root")


def _load_existing_control(journal: JournalT) -> JournalT:
    try:
        journal._key = load_existing_journal_key(journal.control)
        with journal._locked(create_control=False):
            journal._read_head()
            parent_fd, descriptor = journal._open_journal(create=False)
            os.close(descriptor)
            os.close(parent_fd)
        return journal
    except BaseException:
        journal.close()
        raise


def load_or_create_journal_key(control: PinnedRoot) -> bytes:
    """Load one existing key or publish it for a writer-only constructor."""
    try:
        return load_existing_journal_key(control)
    except FileNotFoundError:
        try:
            control.write_file_noreplace(
                Path("queue.key"), secrets.token_bytes(32), mode=0o600
            )
        except FileExistsError:
            pass
        return load_existing_journal_key(control)


def load_existing_journal_key(control: PinnedRoot) -> bytes:
    """Load an existing owner-only single-link key without creating state."""
    key = control.read_file(
        Path("queue.key"),
        description="journal key",
        required_mode=0o600,
        required_owner=os.geteuid(),
    )
    if len(key) != 32:
        raise ValueError("journal key is invalid")
    return key
