from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from operator import attrgetter
from pathlib import Path
from typing import Self

from envresearch.workers.native import rename_noreplace_at
from envresearch.workers.tempfiles import temporary_name_for

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | _NOFOLLOW
_FILE_READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
_FILE_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | _NOFOLLOW
_rename_directory_noreplace = rename_noreplace_at
_REGULAR_FILE = " must be a regular non-symlink file"


class PinnedRoot:
    def __init__(
        self, path: Path, *, private: bool = False, create: bool = True
    ) -> None:
        lexical = Path(os.path.abspath(path))
        if not lexical.name:
            raise ValueError("queue root must not be the filesystem root")
        if create:
            lexical.parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = lexical.parent.resolve(strict=True)
        parent_fd = _pin_existing_directory(resolved_parent)
        try:
            descriptor = _open_directory_component(
                parent_fd, lexical.name, create, 0o700 if private else 0o755
            )
        finally:
            os.close(parent_fd)
        try:
            if private:
                _require_private_root(descriptor, create=create)
        except BaseException:
            os.close(descriptor)
            raise
        self.lexical_path, self.path = lexical, resolved_parent / lexical.name
        self.fd, self.private = descriptor, private
        self._parent_binding: tuple[PinnedRoot, Path] | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        descriptor, self.fd = self.fd, -1
        if descriptor >= 0:
            os.close(descriptor)

    def require_attached(self) -> None:
        try:
            current = os.stat(self.lexical_path, follow_symlinks=False)
            opened = os.fstat(self.fd)
        except FileNotFoundError as error:
            raise ValueError("pinned root identity changed") from error
        if not stat.S_ISDIR(current.st_mode) or _identity(current) != _identity(opened):
            raise ValueError("pinned root identity changed")
        if binding := self._parent_binding:
            parent, relative = binding
            parent.require_attached()
            self.require_parent(parent.fd, relative)

    def open_child_root(self, relative: Path, *, private: bool, create: bool) -> Self:
        parts = _validated_parts(relative)
        if not parts:
            raise ValueError("child root path must not be empty")
        self.require_attached()
        relative_path = Path(*parts)
        descriptor = self.open_directory(relative_path, create=create)
        child = self.__class__.__new__(self.__class__)
        child.fd = descriptor
        try:
            self.require_attached()
            child.lexical_path = self.lexical_path.joinpath(*parts)
            child.path = self.path.joinpath(*parts)
            child.private, child._parent_binding = private, (self, relative_path)
            if private:
                _require_private_root(descriptor, create=create)
            child.require_attached()
            return child
        except BaseException:
            child.close()
            raise

    def __del__(self) -> None:
        with suppress(OSError, AttributeError):
            self.close()

    @contextmanager
    def directory(self, relative: Path, *, create: bool = False) -> Iterator[int]:
        descriptor = self.open_directory(relative, create=create)
        try:
            yield descriptor
        finally:
            os.close(descriptor)

    def open_directory(self, relative: Path, *, create: bool = False) -> int:
        if (binding := self._parent_binding) is not None:
            self.require_attached()
            parent, bound = binding
            return parent.open_directory(bound / relative, create=create)
        return _open_relative_directory(self.fd, relative, create, self.private)

    def require_parent(self, parent_fd: int, relative: Path) -> None:
        try:
            descriptor = _open_relative_directory(parent_fd, relative, False, False)
        except FileNotFoundError as error:
            raise ValueError("pinned child parent identity changed") from error
        try:
            if _identity(os.fstat(descriptor)) != _identity(os.fstat(self.fd)):
                raise ValueError("pinned child parent identity changed")
        finally:
            os.close(descriptor)

    def ensure_directory(self, relative: Path) -> None:
        os.close(self.open_directory(relative, create=True))

    def read_file(
        self,
        relative: Path,
        *,
        description: str,
        required_mode: int | None = None,
        required_owner: int | None = None,
    ) -> bytes:
        parent, name = _split_file(relative)
        with self.directory(parent) as parent_fd:
            return read_regular_at(
                parent_fd,
                name,
                description=description,
                required_mode=required_mode,
                required_owner=required_owner,
            )

    def write_file_noreplace(self, relative: Path, data: bytes, *, mode: int) -> None:
        parent, name = _split_file(relative)
        with self.directory(parent, create=True) as parent_fd:
            write_file_noreplace_at(parent_fd, name, data, mode=mode)

    def exists(self, relative: Path) -> bool:
        parent, name = _split_file(relative)
        try:
            with self.directory(parent) as parent_fd:
                os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True

    def list_directory(self, relative: Path) -> tuple[str, ...]:
        with self.directory(relative) as descriptor:
            return tuple(sorted(os.listdir(descriptor)))

    def require_name(self, name: str) -> None:
        _require_entry_name(name)
        if len(os.fsencode(name)) > os.fpathconf(self.fd, "PC_NAME_MAX"):
            raise ValueError("derived name exceeds the filesystem byte limit")


def directories_overlap(first_fd: int, second_fd: int) -> bool:
    contains = _directory_contains
    return contains(first_fd, second_fd) or contains(second_fd, first_fd)


def read_regular_at(
    parent_fd: int,
    name: str,
    *,
    description: str,
    required_mode: int | None = None,
    required_owner: int | None = None,
) -> bytes:
    return read_regular_with_identity_at(
        parent_fd,
        name,
        description=description,
        required_mode=required_mode,
        required_owner=required_owner,
    )[0]


def read_regular_with_identity_at(
    parent_fd: int,
    name: str,
    *,
    description: str,
    required_mode: int | None = None,
    required_owner: int | None = None,
) -> tuple[bytes, tuple[int, int]]:
    _require_entry_name(name)
    try:
        descriptor = os.open(name, _FILE_READ_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError(description + _REGULAR_FILE) from error
        raise
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(description + _REGULAR_FILE)
        if metadata.st_nlink != 1:
            raise ValueError(f"{description} link count must be exactly one")
        if (
            required_mode is not None
            and stat.S_IMODE(metadata.st_mode) != required_mode
        ):
            raise ValueError(f"{description} has unsafe permissions")
        if required_owner is not None and metadata.st_uid != required_owner:
            raise ValueError(f"{description} has unsafe ownership")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks), _identity(metadata)
    finally:
        os.close(descriptor)


def write_file_noreplace_at(
    parent_fd: int, name: str, data: bytes, *, mode: int
) -> None:
    _require_entry_name(name)
    temporary = temporary_name_for(name)
    if len(os.fsencode(temporary)) > os.fpathconf(parent_fd, "PC_NAME_MAX"):
        raise ValueError("temporary name exceeds the filesystem byte limit")
    descriptor = os.open(temporary, _FILE_WRITE_FLAGS, mode, dir_fd=parent_fd)
    try:
        os.fchmod(descriptor, mode)
        _write_all(descriptor, data)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _rename_directory_noreplace(parent_fd, temporary, parent_fd, name)
        os.fsync(parent_fd)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        raise


def create_directory_at(parent_fd: int, name: str, *, mode: int = 0o700) -> int:
    _require_entry_name(name)
    os.mkdir(name, mode, dir_fd=parent_fd)
    os.fsync(parent_fd)
    descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    os.fchmod(descriptor, mode)
    return descriptor


def open_directory_at(parent_fd: int, name: str) -> int:
    _require_entry_name(name)
    return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)


def entry_exists_at(parent_fd: int, name: str) -> bool:
    _require_entry_name(name)
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def list_names_at(directory_fd: int) -> tuple[str, ...]:
    return tuple(sorted(os.listdir(directory_fd)))


def remove_tree_at(parent_fd: int, name: str) -> None:
    _require_entry_name(name)
    descriptor = open_directory_at(parent_fd, name)
    try:
        for child in os.listdir(descriptor):
            metadata = os.stat(child, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                remove_tree_at(descriptor, child)
            else:
                os.unlink(child, dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.rmdir(name, dir_fd=parent_fd)
    os.fsync(parent_fd)


def _open_directory_component(parent: int, name: str, create: bool, mode: int) -> int:
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(name, mode, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            pass
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("queue component must not be a symlink") from error
        raise
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise ValueError("queue directory must be a regular non-symlink directory")
    return descriptor


def _validated_parts(relative: Path) -> tuple[str, ...]:
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("path must be safe and relative to the pinned root")
    parts = tuple(relative.parts)
    for part in parts:
        _require_entry_name(part)
    return parts


def _split_file(relative: Path) -> tuple[Path, str]:
    parts = _validated_parts(relative)
    if not parts:
        raise ValueError("file path must not be empty")
    return Path(*parts[:-1]), parts[-1]


def _require_entry_name(name: str) -> None:
    if not name or name in {".", ".."} or "/" in name or "\0" in name:
        raise ValueError("filesystem entry name must be one safe segment")


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(descriptor, view) :]


_identity = attrgetter("st_dev", "st_ino")


def _require_private_root(descriptor: int, *, create: bool) -> None:
    if create:
        os.fchmod(descriptor, 0o700)
        return
    metadata = os.fstat(descriptor)
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise ValueError("protected queue root has unsafe metadata")


def _open_relative_directory(fd: int, path: Path, create: bool, private: bool) -> int:
    descriptor = os.dup(fd)
    try:
        for component in _validated_parts(path):
            child = _open_directory_component(
                descriptor, component, create, 0o700 if private else 0o755
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _pin_existing_directory(path: Path) -> int:
    before = os.stat(path, follow_symlinks=False)
    descriptor = os.open(path, _DIRECTORY_FLAGS)
    if _identity(before) != _identity(os.fstat(descriptor)):
        os.close(descriptor)
        raise ValueError("queue root changed while being pinned")
    return descriptor


def _directory_contains(ancestor_fd: int, descendant_fd: int) -> bool:
    ancestor_identity = _identity(os.fstat(ancestor_fd))
    current = os.dup(descendant_fd)
    try:
        while True:
            current_identity = _identity(os.fstat(current))
            if current_identity == ancestor_identity:
                return True
            parent = os.open("..", _DIRECTORY_FLAGS, dir_fd=current)
            parent_identity = _identity(os.fstat(parent))
            os.close(current)
            current = parent
            if parent_identity == current_identity:
                return False
    finally:
        os.close(current)
