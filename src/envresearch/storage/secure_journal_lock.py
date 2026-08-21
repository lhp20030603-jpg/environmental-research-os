"""Authenticated non-replaceable coordination for secure journals."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from envresearch.storage.secure_journal_files import identity, require_safe_file
from envresearch.workers.filesystem import (
    PinnedRoot,
    entry_exists_at,
    write_file_noreplace_at,
)
from envresearch.workers.native import locked_directory_at, locked_regular_at

_HASH = hashlib.sha256


class _LockAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: str = "1.0"
    journal_id: str
    device: int
    inode: int
    mac: str


@contextmanager
def secured_journal_lock(
    control: PinnedRoot,
    journal_id: str,
    key: bytes,
    verify_roots: Callable[[], None],
    *,
    create_control: bool = True,
) -> Iterator[None]:
    """Serialize on the pinned control directory and authenticate lock identity."""
    name = f"{journal_id}.filelock"
    with locked_directory_at(control.fd):
        verify_roots()
        with control.directory(Path("journal-locks")) as parent_fd:
            if not entry_exists_at(parent_fd, name):
                if not create_control:
                    raise FileNotFoundError(name)
                try:
                    write_file_noreplace_at(parent_fd, name, b"", mode=0o600)
                except FileExistsError:
                    pass
            with locked_regular_at(parent_fd, name) as lock_fd:
                _require_anchor(
                    control,
                    journal_id,
                    key,
                    lock_fd,
                    create_control=create_control,
                )
                verify_roots()
                _require_named_lock(control, parent_fd, name, lock_fd)
                yield
                _require_named_lock(control, parent_fd, name, lock_fd)
                verify_roots()


def _require_anchor(
    control: PinnedRoot,
    journal_id: str,
    key: bytes,
    descriptor: int,
    *,
    create_control: bool,
) -> None:
    metadata = os.fstat(descriptor)
    unsigned = {
        "schema_version": "1.0",
        "journal_id": journal_id,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
    }
    anchor = _LockAnchor(
        schema_version="1.0",
        journal_id=journal_id,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mac=hmac.new(key, _canonical(unsigned), _HASH).hexdigest(),
    )
    relative = Path("journal-lock-anchors") / f"{journal_id}.json"
    encoded = _canonical(anchor.model_dump())
    if control.exists(relative):
        data = control.read_file(
            relative,
            description="journal lock anchor",
            required_mode=0o600,
            required_owner=os.geteuid(),
        )
        actual = _LockAnchor.model_validate_json(data)
        actual_unsigned = actual.model_dump(exclude={"mac"})
        if (
            data != _canonical(actual.model_dump())
            or not hmac.compare_digest(
                actual.mac,
                hmac.new(key, _canonical(actual_unsigned), _HASH).hexdigest(),
            )
            or actual != anchor
        ):
            raise ValueError("journal lock replacement detected")
        return
    if not create_control:
        raise FileNotFoundError(relative)
    try:
        control.write_file_noreplace(relative, encoded, mode=0o600)
    except FileExistsError:
        _require_anchor(
            control,
            journal_id,
            key,
            descriptor,
            create_control=create_control,
        )


def _require_named_lock(
    control: PinnedRoot, parent_fd: int, name: str, descriptor: int
) -> None:
    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    require_safe_file(current, "journal lock")
    if identity(current) != identity(os.fstat(descriptor)):
        raise ValueError("journal lock replacement detected")
    with control.directory(Path("journal-locks")) as current_parent:
        if identity(os.fstat(current_parent)) != identity(os.fstat(parent_fd)):
            raise ValueError("journal lock parent changed")


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
