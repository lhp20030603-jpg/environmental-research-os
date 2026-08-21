"""Darwin/Linux descriptor-relative atomic publication and file locking."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager

if sys.platform == "darwin" or sys.platform.startswith("linux"):
    import fcntl as _fcntl
else:  # pragma: no cover - exercised by platform simulation
    _fcntl = None  # type: ignore[assignment]


def rename_noreplace_at(
    source_fd: int, source_name: str, destination_fd: int, destination: str
) -> None:
    """Atomically rename an entry without replacing the destination."""
    library = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source_name)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        function = library.renameatx_np
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        result = function(
            source_fd, source_bytes, destination_fd, destination_bytes, 0x00000004
        )
    elif sys.platform.startswith("linux") and hasattr(library, "renameat2"):
        function = library.renameat2
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        result = function(source_fd, source_bytes, destination_fd, destination_bytes, 1)
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename unsupported")
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), destination)


def rename_exchange_at(
    first_fd: int, first_name: str, second_fd: int, second_name: str
) -> None:
    """Atomically exchange two directory entries on supported POSIX systems."""
    library = ctypes.CDLL(None, use_errno=True)
    first_bytes = os.fsencode(first_name)
    second_bytes = os.fsencode(second_name)
    if sys.platform == "darwin":
        function = library.renameatx_np
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        result = function(first_fd, first_bytes, second_fd, second_bytes, 0x00000002)
    elif sys.platform.startswith("linux") and hasattr(library, "renameat2"):
        function = library.renameat2
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        result = function(first_fd, first_bytes, second_fd, second_bytes, 2)
    else:
        raise OSError(errno.ENOTSUP, "atomic exchange rename unsupported")
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), second_name)


@contextmanager
def locked_regular_at(
    parent_fd: int, name: str, *, timeout: float = 30
) -> Iterator[int]:
    """Hold an exclusive lock on one verified descriptor-relative file."""
    if _fcntl is None or not (
        sys.platform == "darwin" or sys.platform.startswith("linux")
    ):
        raise OSError(errno.ENOTSUP, "descriptor locking unsupported")
    flags = (
        os.O_RDWR
        | os.O_CLOEXEC
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("lock must be a regular non-symlink file") from error
        raise
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
        ):
            raise ValueError("lock file identity or permissions are unsafe")
        deadline = time.monotonic() + timeout
        while True:
            try:
                _fcntl.flock(descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                break
            except BlockingIOError as error:
                if timeout <= 0 or time.monotonic() >= deadline:
                    raise TimeoutError("order lock acquisition timed out") from error
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        try:
            yield descriptor
        finally:
            _fcntl.flock(descriptor, _fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


@contextmanager
def locked_directory_at(descriptor: int, *, timeout: float = 30) -> Iterator[int]:
    """Hold an exclusive lock on a fresh description of one pinned directory."""
    if _fcntl is None:
        raise OSError(errno.ENOTSUP, "descriptor locking unsupported")
    opened = os.open(
        ".",
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
        dir_fd=descriptor,
    )
    try:
        metadata = os.fstat(opened)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise ValueError("coordination directory identity is unsafe")
        deadline = time.monotonic() + timeout
        while True:
            try:
                _fcntl.flock(opened, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                break
            except BlockingIOError as error:
                if timeout <= 0 or time.monotonic() >= deadline:
                    raise TimeoutError(
                        "directory lock acquisition timed out"
                    ) from error
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        try:
            yield opened
        finally:
            _fcntl.flock(opened, _fcntl.LOCK_UN)
    finally:
        os.close(opened)
