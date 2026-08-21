"""Low-level retained-descriptor I/O for secure journals."""

from __future__ import annotations

import errno
import os
import stat

READ_WRITE_FLAGS = (
    os.O_RDWR
    | os.O_APPEND
    | os.O_CLOEXEC
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)
CREATE_FLAGS = READ_WRITE_FLAGS | os.O_CREAT | os.O_EXCL
READ_ONLY_FLAGS = (
    os.O_RDONLY
    | os.O_CLOEXEC
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)


def open_regular(
    parent_fd: int, name: str, *, create: bool, writable: bool = True
) -> tuple[int, bool]:
    """Open or create one final entry without following an alias."""
    if create:
        if not writable:
            raise ValueError("read-only journal cannot be created")
        try:
            return os.open(name, CREATE_FLAGS, 0o600, dir_fd=parent_fd), True
        except FileExistsError:
            pass
    try:
        flags = READ_WRITE_FLAGS if writable else READ_ONLY_FLAGS
        return os.open(name, flags, dir_fd=parent_fd), False
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENXIO}:
            raise ValueError("journal must be a regular non-symlink file") from error
        raise


def require_safe_file(metadata: os.stat_result, description: str) -> None:
    """Require an owner-only regular inode with exactly one link."""
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise ValueError(
            f"{description} must be an owner-only regular single-link file"
        )


def read_all(descriptor: int) -> bytes:
    """Read all bytes from a retained descriptor at its current offset."""
    chunks: list[bytes] = []
    while chunk := os.read(descriptor, 1024 * 1024):
        chunks.append(chunk)
    return b"".join(chunks)


def write_all(descriptor: int, data: bytes) -> None:
    """Write every byte to a retained descriptor."""
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        view = view[written:]


def identity(metadata: os.stat_result) -> tuple[int, int]:
    """Return the stable device/inode identity of an open or named entry."""
    return metadata.st_dev, metadata.st_ino
