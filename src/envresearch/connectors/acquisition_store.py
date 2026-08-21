"""Descriptor-pinned storage and audit records for connector acquisitions."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from envresearch.connectors.contracts import (
    AcquisitionAuditRecord,
    MeasuredAcquisition,
)
from envresearch.workers.filesystem import PinnedRoot
from envresearch.workers.native import rename_noreplace_at

_READ_FLAGS = (
    os.O_RDONLY
    | os.O_CLOEXEC
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)
_LOCK_OPEN_FLAGS = os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
_LOCK_CREATE_FLAGS = _LOCK_OPEN_FLAGS | os.O_CREAT | os.O_EXCL
_TARGET_CREATE_FLAGS = (
    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
)
_SAFE_ID = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
)


class UnsafeAcquisitionOutput(ValueError):
    """An output could not be bound to one stable regular inode."""


@dataclass(frozen=True, slots=True)
class PinnedAcquisitionTarget:
    """A gateway-created staging inode retained through accepted publication."""

    path: Path
    relative_staging: Path
    relative_target: Path
    descriptor: int
    parent_fd: int


class AcquisitionStore:
    """Own a private pinned root for outputs, locks, and immutable audit records."""

    def __init__(self, root: Path) -> None:
        self._root = PinnedRoot(root, private=True)
        self.root = self._root.path
        for relative in (
            Path("outputs"),
            Path("control/locks"),
            Path("control/audit"),
            Path("control/state"),
        ):
            self._root.ensure_directory(relative)

    def close(self) -> None:
        """Release the pinned root descriptor."""
        self._root.close()

    def __del__(self) -> None:
        root = getattr(self, "_root", None)
        if root is not None:
            try:
                root.close()
            except OSError:
                pass

    def target(self, connector_id: str, request_id: str) -> Path:
        """Return only the deterministic connector staging path below the root."""
        relative = self.relative_staging(connector_id, request_id)
        self._root.ensure_directory(relative.parent)
        return self.root / relative

    def relative_target(self, connector_id: str, request_id: str) -> Path:
        """Return the accepted output name without touching its final entry."""
        return (
            Path("outputs") / _safe_id(connector_id) / _safe_id(request_id) / "payload"
        )

    def relative_staging(self, connector_id: str, request_id: str) -> Path:
        """Return the connector-visible staging name below the accepted parent."""
        return self.relative_target(connector_id, request_id).with_name(
            "payload.pending"
        )

    def exists(self, connector_id: str, request_id: str) -> bool:
        """Check accepted or partial output entries without following aliases."""
        return self._root.exists(
            self.relative_target(connector_id, request_id)
        ) or self._root.exists(self.relative_staging(connector_id, request_id))

    @contextmanager
    def prepare(
        self, connector_id: str, request_id: str
    ) -> Iterator[PinnedAcquisitionTarget]:
        """Create and pin the exact staging inode before connector code runs."""
        staging = self.relative_staging(connector_id, request_id)
        accepted = self.relative_target(connector_id, request_id)
        self._root.ensure_directory(staging.parent)
        parent_fd = self._root.open_directory(staging.parent)
        try:
            descriptor = os.open(
                staging.name,
                _TARGET_CREATE_FLAGS,
                0o600,
                dir_fd=parent_fd,
            )
            try:
                os.fchmod(descriptor, 0o600)
                _require_regular_single_link(os.fstat(descriptor))
                yield PinnedAcquisitionTarget(
                    path=self.root / staging,
                    relative_staging=staging,
                    relative_target=accepted,
                    descriptor=descriptor,
                    parent_fd=parent_fd,
                )
            finally:
                os.close(descriptor)
        finally:
            os.close(parent_fd)

    @contextmanager
    def locked(self, connector_id: str, request_id: str) -> Iterator[None]:
        """Serialize one request across gateway instances and processes."""
        key = _request_key(connector_id, request_id)
        with self._root.directory(Path("control/locks")) as parent_fd:
            name = f"{key}.lock"
            try:
                descriptor = os.open(name, _LOCK_CREATE_FLAGS, 0o600, dir_fd=parent_fd)
                created = True
            except FileExistsError:
                descriptor = os.open(name, _LOCK_OPEN_FLAGS, dir_fd=parent_fd)
                created = False
            try:
                metadata = os.fstat(descriptor)
                if created:
                    os.fchmod(descriptor, 0o600)
                    metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                ):
                    raise UnsafeAcquisitionOutput(
                        "acquisition lock is not an owner-only regular single-link file"
                    )
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    def measure(self, connector_id: str, request_id: str) -> MeasuredAcquisition:
        """Measure an already accepted output for idempotent reuse."""
        relative = self.relative_target(connector_id, request_id)
        with self._root.directory(relative.parent) as parent_fd:
            descriptor = _open_output(parent_fd, relative.name)
            try:
                return _measure_descriptor(
                    descriptor, parent_fd, relative.name, relative
                )
            finally:
                os.close(descriptor)

    def measure_pinned(self, target: PinnedAcquisitionTarget) -> MeasuredAcquisition:
        """Measure the pre-created inode and require its staging name to retain it."""
        return _measure_descriptor(
            target.descriptor,
            target.parent_fd,
            target.relative_staging.name,
            target.relative_target,
        )

    def promote(
        self, target: PinnedAcquisitionTarget, measured: MeasuredAcquisition
    ) -> None:
        """Atomically publish the measured staging inode under its accepted name."""
        self.verify_pinned(target, measured, promoted=False)
        rename_noreplace_at(
            target.parent_fd,
            target.relative_staging.name,
            target.parent_fd,
            target.relative_target.name,
        )
        os.fsync(target.parent_fd)
        self.verify_pinned(target, measured, promoted=True)

    def verify_pinned(
        self,
        target: PinnedAcquisitionTarget,
        measured: MeasuredAcquisition,
        *,
        promoted: bool,
    ) -> None:
        """Re-hash the pinned inode at the current staging or accepted name."""
        name = target.relative_target.name if promoted else target.relative_staging.name
        current = _measure_descriptor(
            target.descriptor,
            target.parent_fd,
            name,
            target.relative_target,
        )
        if current != measured:
            raise UnsafeAcquisitionOutput(
                "acquisition output changed during publication"
            )

    def record(self, record: AcquisitionAuditRecord) -> None:
        """Durably append one immutable audit result under its request directory."""
        request_dir = (
            Path("control/audit")
            / _safe_id(record.connector_id)
            / _safe_id(record.request_id)
        )
        payload = _serialize(record)
        self._root.write_file_noreplace(
            request_dir / f"{uuid4().hex}.json", payload, mode=0o600
        )

    def publish_state(
        self,
        record: AcquisitionAuditRecord,
        *,
        pinned: PinnedAcquisitionTarget,
        measured: MeasuredAcquisition,
    ) -> None:
        """Publish the one accepted terminal state without replacement."""
        if record.status != "accepted":
            raise ValueError("only accepted acquisitions may become terminal state")
        self.verify_pinned(pinned, measured, promoted=True)
        final = self._state_path(record.connector_id, record.request_id)
        provisional = final.with_name(f"{final.name}.pending")
        try:
            self._root.write_file_noreplace(provisional, _serialize(record), mode=0o600)
            self.verify_pinned(pinned, measured, promoted=True)
            with self._root.directory(final.parent) as state_fd:
                rename_noreplace_at(state_fd, provisional.name, state_fd, final.name)
                os.fsync(state_fd)
            self.verify_pinned(pinned, measured, promoted=True)
        except BaseException:
            self._publish_tombstone(record.connector_id, record.request_id)
            raise

    def load_state(
        self, connector_id: str, request_id: str
    ) -> AcquisitionAuditRecord | None:
        """Load one accepted terminal record through the pinned root."""
        path = self._state_path(connector_id, request_id)
        if self._root.exists(self._tombstone_path(connector_id, request_id)):
            return None
        if not self._root.exists(path):
            return None
        raw = self._root.read_file(
            path, description="acquisition state", required_mode=0o600
        )
        return AcquisitionAuditRecord.model_validate_json(raw)

    @staticmethod
    def _state_path(connector_id: str, request_id: str) -> Path:
        return Path("control/state") / f"{_request_key(connector_id, request_id)}.json"

    @staticmethod
    def _tombstone_path(connector_id: str, request_id: str) -> Path:
        key = _request_key(connector_id, request_id)
        return Path("control/state") / f"{key}.quarantined"

    def _publish_tombstone(self, connector_id: str, request_id: str) -> None:
        path = self._tombstone_path(connector_id, request_id)
        try:
            self._root.write_file_noreplace(path, b"quarantined\n", mode=0o600)
        except FileExistsError:
            pass


def _safe_id(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError("connector and request IDs must be 1-128 characters")
    if value in {".", ".."} or any(character not in _SAFE_ID for character in value):
        raise ValueError("connector and request IDs contain unsafe characters")
    return value


def _request_key(connector_id: str, request_id: str) -> str:
    identity = f"{_safe_id(connector_id)}\0{_safe_id(request_id)}".encode()
    return hashlib.sha256(identity).hexdigest()


def _open_output(parent_fd: int, name: str) -> int:
    try:
        return os.open(name, _READ_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError as error:
        raise UnsafeAcquisitionOutput("acquisition output is missing") from error
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise UnsafeAcquisitionOutput(
                "acquisition output must not be a symlink"
            ) from error
        raise


def _require_regular_single_link(metadata: os.stat_result) -> os.stat_result:
    if not stat.S_ISREG(metadata.st_mode):
        raise UnsafeAcquisitionOutput("acquisition output must be a regular file")
    if metadata.st_nlink != 1:
        raise UnsafeAcquisitionOutput(
            "acquisition output link count must be exactly one"
        )
    return metadata


def _measure_descriptor(
    descriptor: int,
    parent_fd: int,
    path_name: str,
    relative_target: Path,
) -> MeasuredAcquisition:
    os.lseek(descriptor, 0, os.SEEK_SET)
    before = _require_regular_single_link(os.fstat(descriptor))
    digest = hashlib.sha256()
    count = 0
    while chunk := os.read(descriptor, 1024 * 1024):
        count += len(chunk)
        digest.update(chunk)
    after = _require_regular_single_link(os.fstat(descriptor))
    try:
        current = os.stat(path_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError as error:
        raise UnsafeAcquisitionOutput("acquisition output is missing") from error
    _require_regular_single_link(current)
    _require_stable(before, after, current, count)
    return MeasuredAcquisition(
        relative_target=relative_target.as_posix(),
        bytes=count,
        local_storage_bytes=count,
        sha256=digest.hexdigest(),
        device=after.st_dev,
        inode=after.st_ino,
    )


def _require_stable(
    before: os.stat_result,
    after: os.stat_result,
    current: os.stat_result,
    count: int,
) -> None:
    if _identity(before) != _identity(after) or _identity(after) != _identity(current):
        raise UnsafeAcquisitionOutput("acquisition output changed during verification")
    if _mutation(before) != _mutation(after) or _mutation(after) != _mutation(current):
        raise UnsafeAcquisitionOutput("acquisition output changed during verification")
    if count != after.st_size:
        raise UnsafeAcquisitionOutput(
            "acquisition output size changed during verification"
        )


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _mutation(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_nlink,
    )


def _serialize(record: AcquisitionAuditRecord) -> bytes:
    payload = record.model_dump(mode="json")
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
