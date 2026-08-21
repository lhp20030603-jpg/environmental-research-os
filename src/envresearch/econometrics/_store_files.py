"""Descriptor-relative access to the local econometrics artifact store."""

from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path

from envresearch.workers.filesystem import PinnedRoot


class StoreFiles:
    """Read and publish leaves without following any path component."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._pinned: PinnedRoot | None = None

    @classmethod
    def from_pinned(cls, root: PinnedRoot) -> StoreFiles:
        """Borrow a retained root for descriptor-relative access."""
        instance = cls(root.path)
        instance._pinned = root
        return instance

    def read(self, relative: Path) -> bytes:
        """Read one exact regular leaf beneath the authenticated root."""
        parent, name = self._parent(relative, create=False)
        try:
            lexical = os.stat(name, dir_fd=parent, follow_symlinks=False)
            descriptor = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=parent,
            )
            try:
                opened = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(lexical.st_mode)
                    or not stat.S_ISREG(opened.st_mode)
                    or (lexical.st_dev, lexical.st_ino)
                    != (opened.st_dev, opened.st_ino)
                ):
                    raise OSError("store evidence is not a regular file")
                return _read_descriptor(descriptor, opened.st_size)
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)

    def write(self, relative: Path, data: bytes, mode: int = 0o444) -> None:
        """Atomically replace one leaf inside an authenticated parent directory."""
        parent, name = self._parent(relative, create=True)
        temporary = f".{name}.{uuid.uuid4().hex}.tmp"
        descriptor = -1
        try:
            descriptor = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
                dir_fd=parent,
            )
            _write_descriptor(descriptor, data)
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
            os.close(parent)

    def persist_exact(self, relative: Path, data: bytes) -> None:
        """Publish an immutable leaf or require its existing exact bytes."""
        try:
            existing = self.read(relative)
        except FileNotFoundError:
            self.write(relative, data)
            return
        if existing != data:
            raise ValueError("content-addressed local evidence collision")

    def unlink(self, relative: Path) -> None:
        """Remove one final leaf without following it."""
        parent, name = self._parent(relative, create=False)
        try:
            try:
                os.unlink(name, dir_fd=parent)
            except FileNotFoundError:
                pass
        finally:
            os.close(parent)

    def exists(self, relative: Path) -> bool:
        """Return whether an exact readable regular leaf exists."""
        try:
            self.read(relative)
        except FileNotFoundError:
            return False
        return True

    def ensure_directory(self, relative: Path) -> None:
        """Create and authenticate every directory component beneath root."""
        marker = relative / ".directory-marker"
        parent, _ = self._parent(marker, create=True)
        os.close(parent)

    def open_lock(self, relative: Path, *, create: bool = True) -> int:
        """Open one process-lock leaf through authenticated directory handles."""
        parent, name = self._parent(relative, create=create)
        try:
            flags = (
                os.O_RDWR | (os.O_CREAT if create else 0) | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                descriptor = os.open(name, flags, 0o600, dir_fd=parent)
            except FileNotFoundError:
                if not create:
                    raise
                descriptor = os.open(name, flags, 0o600, dir_fd=parent)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                os.close(descriptor)
                raise OSError("store lock is not a regular file")
            return descriptor
        finally:
            os.close(parent)

    def _parent(self, relative: Path, *, create: bool) -> tuple[int, str]:
        parts = _parts(relative)
        if self._pinned is not None:
            self._pinned.require_attached()
            descriptor = self._pinned.open_directory(Path(*parts[:-1]), create=create)
            try:
                self._pinned.require_attached()
                return descriptor, parts[-1]
            except BaseException:
                os.close(descriptor)
                raise
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        elif not self.root.exists():
            raise FileNotFoundError(self.root)
        descriptor = os.open(
            self.root,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            for component in parts[:-1]:
                try:
                    child = os.open(
                        component,
                        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=descriptor,
                    )
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(component, 0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                    child = os.open(
                        component,
                        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=descriptor,
                    )
                os.close(descriptor)
                descriptor = child
            return descriptor, parts[-1]
        except BaseException:
            os.close(descriptor)
            raise


def _parts(relative: Path) -> tuple[str, ...]:
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("store path must be canonical and relative")
    return relative.parts


def _read_descriptor(descriptor: int, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size + 1
    while remaining:
        chunk = os.read(descriptor, min(remaining, 1024 * 1024))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) != size:
        raise OSError("store evidence size changed while reading")
    return data


def _write_descriptor(descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        offset += os.write(descriptor, data[offset:])
