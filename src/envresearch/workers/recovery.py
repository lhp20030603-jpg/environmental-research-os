"""Bounded descriptor-relative recovery for protected temporary files."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from envresearch.workers.contracts import require_candidate_filename
from envresearch.workers.filesystem import read_regular_with_identity_at
from envresearch.workers.tempfiles import (
    target_name_digest,
    temporary_target_digest,
)

_MAX_RECOVERABLE_TEMPORARIES = 64
_MAX_RECEIPT_NAMESPACE_ENTRIES = 256
_MetadataToken = tuple[int, int, int, int, int, int, int, int]


@dataclass(frozen=True)
class _ValidatedTemporary:
    name: str
    data: bytes
    identity: tuple[int, int]
    target: str
    metadata: _MetadataToken


def recover_receipt_namespace_at(
    parent_fd: int,
    *,
    owner: int,
    authenticate_target: Callable[[bytes], str],
) -> tuple[str, ...]:
    """Validate the whole receipt namespace before removing stale temporaries."""
    names = _bounded_names_at(parent_fd)
    temporary_names = tuple(name for name in names if name.startswith(".tmp-"))
    if len(temporary_names) > _MAX_RECOVERABLE_TEMPORARIES:
        raise ValueError("too many protected temporary files")
    final_names = _preflight_names(names, temporary_names)
    validated = tuple(
        _validate_temporary(parent_fd, name, owner, authenticate_target)
        for name in temporary_names
    )
    for expected in validated:
        current = _validate_temporary(
            parent_fd, expected.name, owner, authenticate_target
        )
        if current != expected:
            raise ValueError("protected temporary changed during recovery")
        _unlink_validated_at(parent_fd, current, owner)
    return final_names


def _bounded_names_at(parent_fd: int) -> tuple[str, ...]:
    names: list[str] = []
    with os.scandir(parent_fd) as entries:
        for entry in entries:
            if len(names) >= _MAX_RECEIPT_NAMESPACE_ENTRIES:
                raise ValueError("too many protected namespace entries")
            names.append(entry.name)
    return tuple(sorted(names))


def _preflight_names(
    names: Iterable[str], temporary_names: tuple[str, ...]
) -> tuple[str, ...]:
    temporary_targets: set[str] = set()
    for name in temporary_names:
        target = temporary_target_digest(name)
        if target in temporary_targets:
            raise ValueError("multiple protected temporaries target one receipt")
        temporary_targets.add(target)
    final_names: list[str] = []
    for name in names:
        if name.startswith(".tmp-"):
            continue
        if not name.endswith(".json"):
            raise ValueError("submission anchor path mismatch")
        try:
            require_candidate_filename(name.removesuffix(".json"))
        except ValueError as error:
            raise ValueError("submission anchor path mismatch") from error
        final_names.append(name)
    return tuple(final_names)


def _validate_temporary(
    parent_fd: int,
    name: str,
    owner: int,
    authenticate_target: Callable[[bytes], str],
) -> _ValidatedTemporary:
    encoded_target = temporary_target_digest(name)
    data, identity = read_regular_with_identity_at(
        parent_fd,
        name,
        description="protected receipt temporary",
        required_mode=0o600,
        required_owner=owner,
    )
    target = authenticate_target(data)
    if encoded_target != target_name_digest(target):
        raise ValueError("protected temporary target authentication failed")
    metadata = _validated_metadata_at(parent_fd, name, owner)
    if metadata[:2] != identity:
        raise ValueError("protected temporary identity changed during recovery")
    return _ValidatedTemporary(name, data, identity, target, metadata)


def _unlink_validated_at(
    parent_fd: int, temporary: _ValidatedTemporary, owner: int
) -> None:
    metadata = _validated_metadata_at(parent_fd, temporary.name, owner)
    if metadata != temporary.metadata:
        raise ValueError("protected temporary changed during recovery")
    os.unlink(temporary.name, dir_fd=parent_fd)
    os.fsync(parent_fd)


def _validated_metadata_at(
    parent_fd: int, name: str, owner: int
) -> _MetadataToken:
    metadata = os.stat(
        name, dir_fd=parent_fd, follow_symlinks=False
    )
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError("protected temporary identity changed during recovery")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ValueError("protected temporary has unsafe permissions")
    if metadata.st_uid != owner:
        raise ValueError("protected temporary has unsafe ownership")
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )
