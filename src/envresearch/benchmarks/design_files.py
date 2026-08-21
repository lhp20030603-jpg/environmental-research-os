"""Read-only descriptor-pinned filesystem boundary for design fixtures."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from typing import Self

from envresearch.workers.filesystem import (
    PinnedRoot,
    list_names_at,
    open_directory_at,
    read_regular_at,
    read_regular_with_identity_at,
)

_DIRECTORY_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
)


class PinnedFixtureRoot:
    """Pin an existing catalog root and never follow descendant aliases."""

    def __init__(self, root: Path) -> None:
        lexical = Path(os.path.abspath(root))
        try:
            before = os.stat(lexical, follow_symlinks=False)
            descriptor = os.open(lexical, _DIRECTORY_FLAGS)
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ValueError(
                    "fixture root must be a regular non-symlink directory"
                ) from error
            raise
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or not stat.S_ISDIR(opened.st_mode)
            or _identity(before) != _identity(opened)
        ):
            os.close(descriptor)
            raise ValueError("fixture root must be a regular non-symlink directory")
        self.root = lexical
        self.fd = descriptor

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def discover_manifests(self) -> tuple[Path, ...]:
        """Find benchmark.yaml files without following any directory symlink."""
        found: list[Path] = []
        self._walk(os.dup(self.fd), Path(), found)
        return tuple(sorted(found))

    def read(self, relative: Path, *, description: str) -> bytes:
        """Read one regular, single-link file beneath the pinned root."""
        parts = _safe_parts(relative)
        parent = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                child = _open_directory(parent, part, description)
                os.close(parent)
                parent = child
            return read_regular_at(parent, parts[-1], description=description)
        finally:
            os.close(parent)

    def pin_directory(self, relative: Path) -> PinnedFixtureRoot:
        """Pin one existing descendant directory for a multi-file snapshot."""
        parts = () if relative == Path(".") else _safe_parts(relative)
        descriptor = os.dup(self.fd)
        try:
            for part in parts:
                child = _open_directory(descriptor, part, "fixture directory")
                os.close(descriptor)
                descriptor = child
            pinned = object.__new__(PinnedFixtureRoot)
            pinned.root = self.root / relative
            pinned.fd = descriptor
            return pinned
        except BaseException:
            os.close(descriptor)
            raise

    def snapshot_to(
        self, destination: Path, *, validate_controls: bool = False
    ) -> None:
        """Copy the pinned tree without following or preserving aliases."""
        target = PinnedRoot(destination, private=True)
        try:
            self._snapshot(
                os.dup(self.fd), target, Path(), validate_controls, private=False
            )
        finally:
            target.close()

    def _snapshot(
        self,
        descriptor: int,
        target: PinnedRoot,
        prefix: Path,
        validate_controls: bool,
        *,
        private: bool,
    ) -> None:
        try:
            for name in list_names_at(descriptor):
                metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                relative = prefix / name
                protected = private or (
                    validate_controls and _is_protected_control_root(relative)
                )
                if stat.S_ISLNK(metadata.st_mode):
                    raise ValueError("fixture snapshot must not contain symlinks")
                if stat.S_ISDIR(metadata.st_mode):
                    child = _open_directory(descriptor, name, "fixture directory")
                    opened = os.fstat(child)
                    if _identity(metadata) != _identity(opened):
                        os.close(child)
                        raise ValueError("fixture directory changed during snapshot")
                    if protected:
                        _require_private_metadata(opened, directory=True)
                    target.ensure_directory(relative)
                    self._snapshot(
                        child,
                        target,
                        relative,
                        validate_controls,
                        private=protected,
                    )
                    continue
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise ValueError(
                        "fixture snapshot files must be regular single-link files"
                    )
                data, identity = read_regular_with_identity_at(
                    descriptor,
                    name,
                    description="fixture snapshot file",
                    required_mode=0o600 if protected else None,
                    required_owner=os.geteuid() if protected else None,
                )
                if _identity(metadata) != identity:
                    raise ValueError("fixture file changed during snapshot")
                target.write_file_noreplace(
                    relative, data, mode=stat.S_IMODE(metadata.st_mode)
                )
        finally:
            os.close(descriptor)

    def _walk(self, descriptor: int, prefix: Path, found: list[Path]) -> None:
        try:
            for name in list_names_at(descriptor):
                metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                relative = prefix / name
                if stat.S_ISLNK(metadata.st_mode):
                    if name == "benchmark.yaml":
                        raise ValueError("benchmark manifest must not be a symlink")
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    child = _open_directory(descriptor, name, "fixture directory")
                    self._walk(child, relative, found)
                    continue
                if name == "benchmark.yaml":
                    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                        raise ValueError(
                            "benchmark manifest must be a regular single-link file"
                        )
                    found.append(relative)
        finally:
            os.close(descriptor)


def _open_directory(parent: int, name: str, description: str) -> int:
    try:
        descriptor = open_directory_at(parent, name)
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError(f"{description} must not be a symlink") from error
        raise
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError(f"{description} must be a regular directory")
    return descriptor


def _safe_parts(relative: Path) -> tuple[str, ...]:
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("fixture path must remain beneath the pinned root")
    parts = tuple(relative.parts)
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("fixture path must be a nonempty relative path")
    return parts


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _is_protected_control_root(path: Path) -> bool:
    parts = path.parts
    return len(parts) >= 4 and parts[-4:-2] == ("control", "queues")


def _require_private_metadata(metadata: os.stat_result, *, directory: bool) -> None:
    expected_mode = 0o700 if directory else 0o600
    if (
        metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != expected_mode
    ):
        raise ValueError(
            "authenticated run control has unsafe ownership or permissions"
        )
