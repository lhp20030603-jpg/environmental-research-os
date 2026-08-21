"""Strict verification and narrowly scoped recovery for secure journals."""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable, Sequence
from pathlib import Path

from envresearch.storage.secure_journal_records import (
    ZERO_HASH,
    JournalHead,
    SecureRecord,
    canonical,
    seal_head,
    verify_head,
)
from envresearch.workers.filesystem import (
    PinnedRoot,
    entry_exists_at,
    read_regular_at,
    write_file_noreplace_at,
)


def build_journal_head(
    *,
    key: bytes,
    journal_id: str,
    descriptor: int,
    record: SecureRecord | None,
    size: int,
    count: int | None = None,
) -> JournalHead:
    """Build the authenticated head for one retained journal descriptor."""
    metadata = os.fstat(descriptor)
    return seal_head(
        key,
        {
            "journal_id": journal_id,
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "record_count": count
            if count is not None
            else (record.sequence if record else 0),
            "size_bytes": size,
            "last_sha256": record.record_sha256 if record else ZERO_HASH,
        },
    )


def read_journal_head(control: PinnedRoot, journal_id: str, key: bytes) -> JournalHead:
    """Read and authenticate one existing protected head."""
    data = control.read_file(
        Path("journal-heads") / f"{journal_id}.json",
        description="journal head",
        required_mode=0o600,
        required_owner=os.geteuid(),
    )
    return verify_head(data, key)


def write_journal_head(control: PinnedRoot, journal_id: str, head: JournalHead) -> None:
    """Atomically replace one authenticated protected head."""
    relative = Path("journal-heads") / f"{journal_id}.json"
    with control.directory(relative.parent) as parent_fd:
        if entry_exists_at(parent_fd, relative.name):
            read_regular_at(
                parent_fd,
                relative.name,
                description="journal head",
                required_mode=0o600,
                required_owner=os.geteuid(),
            )
        temporary = f".{journal_id}.{secrets.token_hex(8)}.tmp"
        write_file_noreplace_at(
            parent_fd, temporary, canonical(head.model_dump()), mode=0o600
        )
        try:
            os.replace(
                temporary,
                relative.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            os.fsync(parent_fd)
        except BaseException:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            raise


def verify_journal_head_strict(
    *,
    expected: JournalHead,
    actual: JournalHead,
    records: Sequence[SecureRecord],
    prefix_size: int,
) -> None:
    """Verify an exact head without performing recovery writes."""
    _require_common_head_state(expected, actual)
    if expected.record_count == actual.record_count:
        if expected != actual:
            raise ValueError("journal authenticated head mismatch")
        return
    _require_exact_lag(expected, actual, records, prefix_size)
    raise ValueError("journal recovery required")


def repair_lagging_head(
    *,
    expected: JournalHead,
    actual: JournalHead,
    records: Sequence[SecureRecord],
    prefix_size: int,
    write_head: Callable[[JournalHead], None],
) -> None:
    """Repair only one authenticated crash record beyond an existing head."""
    _require_common_head_state(expected, actual)
    if expected.record_count == actual.record_count:
        if expected != actual:
            raise ValueError("journal authenticated head mismatch")
        return
    _require_exact_lag(expected, actual, records, prefix_size)
    write_head(actual)


def _require_common_head_state(expected: JournalHead, actual: JournalHead) -> None:
    if expected.journal_id != actual.journal_id:
        raise ValueError("journal authenticated head mismatch")
    if (expected.device, expected.inode) != (actual.device, actual.inode):
        raise ValueError("journal file replacement detected")
    if (
        expected.record_count > actual.record_count
        or expected.size_bytes > actual.size_bytes
    ):
        raise ValueError("journal truncation detected against authenticated head")


def _require_exact_lag(
    expected: JournalHead,
    actual: JournalHead,
    records: Sequence[SecureRecord],
    prefix_size: int,
) -> None:
    if expected.record_count + 1 != actual.record_count:
        raise ValueError("journal head lags by more than one crash record")
    if expected.size_bytes != prefix_size:
        raise ValueError("journal authenticated head has an invalid prefix size")
    prior = records[-2].record_sha256 if len(records) > 1 else ZERO_HASH
    if expected.last_sha256 != prior:
        raise ValueError("journal crash recovery chain mismatch")
