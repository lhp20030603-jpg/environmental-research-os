"""Shared non-creating authentication helpers for protected worker state."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from envresearch.workers.filesystem import PinnedRoot


def prepare_directories(
    storage: PinnedRoot, names: tuple[str, ...], *, create: bool
) -> None:
    """Create requested directories or prove every directory already exists."""
    for name in names:
        if create:
            storage.ensure_directory(Path(name))
        else:
            if storage.private:
                _require_private_directory(storage, Path(name))
            else:
                with storage.directory(Path(name)):
                    pass


def load_existing_key(storage: PinnedRoot) -> bytes:
    """Read an existing private queue key without minting replacement state."""
    key = storage.read_file(
        Path("queue.key"),
        description="queue key",
        required_mode=0o600,
        required_owner=os.geteuid(),
    )
    if len(key) != 32:
        raise ValueError("queue key is invalid")
    return key


def _require_private_directory(storage: PinnedRoot, relative: Path) -> None:
    with storage.directory(relative) as descriptor:
        metadata = os.fstat(descriptor)
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise ValueError("protected queue directory has unsafe metadata")
