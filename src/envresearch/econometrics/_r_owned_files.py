"""Descriptor-relative owned files for trusted local R execution."""

from __future__ import annotations

import hashlib
import os
import stat
import uuid
from pathlib import Path


class RRuntimeInvalid(ValueError):
    """The reviewed R runtime or generated script is not trustworthy."""


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


def publish_owned_file(
    workspace: Path,
    directory: str,
    filename: str,
    data: bytes,
    mode: int,
) -> Path:
    """Atomically publish bytes below one no-follow workspace directory."""
    root_fd = _open_directory(workspace)
    try:
        child_fd = _open_or_create_directory(root_fd, directory)
        try:
            temporary = f".{filename}.{uuid.uuid4().hex}.tmp"
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
                mode,
                dir_fd=child_fd,
            )
            try:
                _write_all(descriptor, data)
                os.fchmod(descriptor, mode)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(
                temporary,
                filename,
                src_dir_fd=child_fd,
                dst_dir_fd=child_fd,
            )
            os.fsync(child_fd)
        except OSError as error:
            try:
                os.unlink(temporary, dir_fd=child_fd)
            except (OSError, UnboundLocalError):
                pass
            raise RRuntimeInvalid(
                "owned runtime hierarchy is not trustworthy"
            ) from error
        finally:
            os.close(child_fd)
    finally:
        os.close(root_fd)
    return workspace / directory / filename


def ensure_owned_directory(workspace: Path, directory: str) -> Path:
    """Create or authenticate one direct no-follow workspace directory."""
    root_fd = _open_directory(workspace)
    try:
        child_fd = _open_or_create_directory(root_fd, directory)
        os.close(child_fd)
    finally:
        os.close(root_fd)
    return workspace / directory


def open_owned_file(
    workspace: Path,
    directory: str,
    filename: str,
    expected_sha256: str,
    max_bytes: int,
) -> tuple[int, os.stat_result]:
    """Open and authenticate one owned regular file, retaining its descriptor."""
    root_fd = _open_directory(workspace)
    try:
        child_fd = _open_directory(directory, dir_fd=root_fd)
        try:
            descriptor = os.open(
                filename,
                os.O_RDONLY | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
                dir_fd=child_fd,
            )
        finally:
            os.close(child_fd)
    except OSError as error:
        os.close(root_fd)
        raise RRuntimeInvalid("owned runtime hierarchy is not trustworthy") from error
    os.close(root_fd)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
            raise RRuntimeInvalid("owned runtime file is not a bounded regular file")
        digest = hashlib.sha256(_read_all(descriptor, max_bytes)).hexdigest()
        if digest != expected_sha256:
            raise RRuntimeInvalid("owned runtime file identity changed")
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor, metadata
    except Exception:
        os.close(descriptor)
        raise


def _open_directory(path: str | Path, *, dir_fd: int | None = None) -> int:
    """Open one directory without following its final component."""
    try:
        descriptor = os.open(path, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=dir_fd)
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("not a directory")
        return descriptor
    except OSError as error:
        raise RRuntimeInvalid("owned runtime hierarchy is not trustworthy") from error


def _open_or_create_directory(root_fd: int, name: str) -> int:
    """Open or create one direct owned child directory."""
    try:
        os.mkdir(name, mode=0o700, dir_fd=root_fd)
    except FileExistsError:
        pass
    return _open_directory(name, dir_fd=root_fd)


def _write_all(descriptor: int, data: bytes) -> None:
    """Write exact bytes to one newly created file."""
    view = memoryview(data)
    while view:
        view = view[os.write(descriptor, view) :]


def _read_all(descriptor: int, max_bytes: int) -> bytes:
    """Read a file beneath one exact byte ceiling."""
    data = bytearray()
    while chunk := os.read(descriptor, min(1024 * 1024, max_bytes - len(data) + 1)):
        data.extend(chunk)
        if len(data) > max_bytes:
            raise RRuntimeInvalid("owned runtime file exceeds its size limit")
    return bytes(data)
