"""Durable atomic file replacement."""

import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically replace *path* with *data*, cleaning temporary files on errors."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(data)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
        _sync_parent_directory(path.parent)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _sync_parent_directory(directory: Path) -> None:
    """Make a replaced directory entry durable where the platform supports it.

    POSIX directory sync failures are surfaced. Python does not expose a
    portable Windows directory-flush primitive, so Windows keeps the atomic
    replacement and treats directory syncing as best effort.
    """
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError:
        if os.name == "nt":
            return
        raise
    try:
        os.fsync(descriptor)
    except OSError:
        if os.name != "nt":
            raise
    finally:
        os.close(descriptor)
