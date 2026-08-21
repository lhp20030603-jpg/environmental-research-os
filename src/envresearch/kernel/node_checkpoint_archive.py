"""Protected namespace preflight and invalidation archive transactions."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from envresearch.kernel.node_checkpoint_schema import (
    NodeCheckpoint,
    canonical_bytes,
    checkpoint_name,
    load_json_object,
    read_checkpoint_at,
    require_safe_id,
)
from envresearch.kernel.task_identity import payload_hash
from envresearch.workers.filesystem import (
    create_directory_at,
    entry_exists_at,
    list_names_at,
    open_directory_at,
    read_regular_at,
    read_regular_with_identity_at,
)
from envresearch.workers.native import rename_noreplace_at
from envresearch.workers.tempfiles import target_name_digest, temporary_target_digest

LOCK_NAME = ".node-checkpoints.filelock"
SUPERSEDED = "superseded"
INTENT = ".invalidation-intent.json"
_EVENT_ID = re.compile(r"^research\.node\.invalidated\.([0-9a-f]{64})$")
_STAGE = re.compile(r"^\.(research\.node\.invalidated\.[0-9a-f]{64})\.stage$")
_MAX_ACTIVE_ENTRIES = 256
_MAX_ARCHIVES = 256
_MAX_ARCHIVE_RECORDS = 256

CheckpointRecord = tuple[NodeCheckpoint, bytes]


@dataclass(frozen=True)
class PendingInvalidation:
    """One globally exclusive incomplete archive transaction."""

    event_id: str
    intent: dict[str, object] | None
    records: dict[str, CheckpointRecord]
    staged: bool = False


@dataclass(frozen=True)
class NamespaceSnapshot:
    """Validated active and superseded checkpoint namespace."""

    active: dict[str, CheckpointRecord]
    archives: dict[str, dict[str, CheckpointRecord]]
    pending: PendingInvalidation | None


@dataclass(frozen=True)
class _DisposableStage:
    """Pinned empty or exact-helper stage eligible for bounded cleanup."""

    name: str
    fd: int
    identity: tuple[int, int]
    temporary: str | None = None
    temporary_identity: tuple[int, int] | None = None


class InvalidationArchive:
    """Manage exact no-replace supersession transactions under one pinned root."""

    def __init__(
        self,
        checkpoints_fd: int,
        *,
        write_once: Callable[[int, str, bytes], None],
        move_once: Callable[[int, str, int, str], None],
    ) -> None:
        self._fd = checkpoints_fd
        self._write_once = write_once
        self._move_once = move_once

    def preflight(self, event_ids: set[str]) -> NamespaceSnapshot:
        """Validate the whole protected namespace before any cleanup or mutation."""
        active = self._scan_active()
        archives: dict[str, dict[str, CheckpointRecord]] = {}
        pending: list[PendingInvalidation] = []
        cleanup_stages: list[_DisposableStage] = []
        try:
            superseded_fd = open_directory_at(self._fd, SUPERSEDED)
        except FileNotFoundError:
            return NamespaceSnapshot(active, archives, None)
        try:
            _require_directory(superseded_fd, "superseded directory")
            names = list_names_at(superseded_fd)
            if len(names) > _MAX_ARCHIVES:
                raise ValueError("superseded namespace contains too many entries")
            stage_count = sum(_STAGE.fullmatch(name) is not None for name in names)
            if stage_count > 1:
                raise ValueError("multiple staging transactions exist")
            for name in names:
                stage_match = _STAGE.fullmatch(name)
                if stage_match:
                    item = self._scan_stage(superseded_fd, name, stage_match.group(1))
                    if isinstance(item, _DisposableStage):
                        cleanup_stages.append(item)
                    else:
                        pending.append(item)
                    continue
                if not _EVENT_ID.fullmatch(name):
                    raise ValueError("invalid superseded directory identity")
                archive_fd = _open_checked_directory(
                    superseded_fd, name, "superseded event directory"
                )
                try:
                    records = self.read_archive(archive_fd)
                    intent = self.read_intent(archive_fd, name)
                finally:
                    os.close(archive_fd)
                archives[name] = records
                if name in event_ids:
                    if intent is not None:
                        raise ValueError("completed invalidation retained its intent")
                else:
                    pending.append(PendingInvalidation(name, intent, records))
            if len(pending) > 1:
                raise ValueError("multiple pending invalidation transactions exist")
            if cleanup_stages and pending:
                raise ValueError("staging residue overlaps a pending invalidation")
            for stage in cleanup_stages:
                self._remove_disposable_stage(superseded_fd, stage)
        finally:
            for stage in cleanup_stages:
                os.close(stage.fd)
            os.close(superseded_fd)
        return NamespaceSnapshot(active, archives, pending[0] if pending else None)

    def prepare(self, event_id: str, intent_data: bytes) -> int:
        """Publish an exact durable intent directory, resuming its exact stage."""
        superseded_fd = _open_or_create(self._fd, SUPERSEDED)
        stage = f".{event_id}.stage"
        try:
            if entry_exists_at(superseded_fd, event_id):
                raise FileExistsError("superseded event directory already exists")
            if entry_exists_at(superseded_fd, stage):
                stage_fd = _open_checked_directory(
                    superseded_fd, stage, "invalidation stage"
                )
                try:
                    existing = self.read_intent(stage_fd, event_id)
                    if existing is None or canonical_bytes(existing) != intent_data:
                        raise ValueError("staged invalidation intent changed")
                finally:
                    os.close(stage_fd)
            else:
                stage_fd = create_directory_at(superseded_fd, stage, mode=0o700)
                try:
                    self._write_once(stage_fd, INTENT, intent_data)
                finally:
                    os.close(stage_fd)
            rename_noreplace_at(superseded_fd, stage, superseded_fd, event_id)
            os.fsync(superseded_fd)
            return _open_checked_directory(
                superseded_fd, event_id, "superseded event directory"
            )
        finally:
            os.close(superseded_fd)

    def open_archive(self, event_id: str) -> int:
        superseded_fd = open_directory_at(self._fd, SUPERSEDED)
        try:
            return _open_checked_directory(
                superseded_fd, event_id, "superseded event directory"
            )
        finally:
            os.close(superseded_fd)

    def move_targets(
        self,
        archive_fd: int,
        source_node_id: str,
        targets: frozenset[str],
        records: Mapping[str, CheckpointRecord],
    ) -> None:
        """Resume byte-preserving moves in a stable source-first order."""
        archived = set(self.read_archive(archive_fd))
        if not archived.issubset(targets):
            raise ValueError("superseded checkpoint set contains unexpected nodes")
        for node_id in (source_node_id, *sorted(targets - {source_node_id})):
            name = checkpoint_name(node_id)
            active_exists = entry_exists_at(self._fd, name)
            archived_exists = entry_exists_at(archive_fd, name)
            if active_exists and archived_exists:
                raise FileExistsError("active and superseded checkpoint collision")
            if archived_exists:
                _, data = read_checkpoint_at(archive_fd, name, node_id)
                if data != records[node_id][1]:
                    raise ValueError("superseded checkpoint bytes changed")
                continue
            if not active_exists:
                raise FileNotFoundError("invalidation source checkpoint disappeared")
            self._move_once(self._fd, name, archive_fd, name)

    def remove_intent(self, archive_fd: int, expected: bytes) -> None:
        if not entry_exists_at(archive_fd, INTENT):
            return
        actual = read_regular_at(
            archive_fd,
            INTENT,
            description="invalidation intent",
            required_mode=0o600,
            required_owner=os.geteuid(),
        )
        if actual != expected:
            raise ValueError("invalidation intent changed before completion")
        os.unlink(INTENT, dir_fd=archive_fd)
        os.fsync(archive_fd)

    def read_archive(self, archive_fd: int) -> dict[str, CheckpointRecord]:
        records: dict[str, CheckpointRecord] = {}
        names = list_names_at(archive_fd)
        if len(names) > _MAX_ARCHIVE_RECORDS + 1:
            raise ValueError("superseded archive contains too many entries")
        for filename in names:
            if filename == INTENT:
                continue
            if not filename.endswith(".json"):
                raise ValueError("invalid superseded checkpoint filename")
            node_id = filename.removesuffix(".json")
            require_safe_id(node_id, "archived node ID")
            _require_regular_entry(archive_fd, filename, "archived checkpoint")
            records[node_id] = read_checkpoint_at(archive_fd, filename, node_id)
        return records

    @staticmethod
    def read_intent(archive_fd: int, event_id: str) -> dict[str, object] | None:
        if not entry_exists_at(archive_fd, INTENT):
            return None
        data = read_regular_at(
            archive_fd,
            INTENT,
            description="invalidation intent",
            required_mode=0o600,
            required_owner=os.geteuid(),
        )
        value = load_json_object(data)
        if data != canonical_bytes(value):
            raise ValueError("invalidation intent bytes are not canonical")
        if event_id != f"research.node.invalidated.{payload_hash(value)}":
            raise ValueError("invalidation intent identity changed")
        return value

    def _scan_active(self) -> dict[str, CheckpointRecord]:
        names = list_names_at(self._fd)
        if len(names) > _MAX_ACTIVE_ENTRIES:
            raise ValueError("node checkpoint namespace contains too many entries")
        active: dict[str, CheckpointRecord] = {}
        for name in names:
            if name == LOCK_NAME:
                _require_regular_entry(self._fd, name, "checkpoint lock")
                continue
            if name == SUPERSEDED:
                continue
            if name.startswith(".tmp-"):
                raise ValueError(
                    "stale active checkpoint temporary in protected namespace"
                )
            if not name.endswith(".json"):
                raise ValueError("invalid node checkpoint namespace entry")
            node_id = name.removesuffix(".json")
            require_safe_id(node_id, "checkpoint node ID")
            _require_regular_entry(self._fd, name, "node checkpoint")
            active[node_id] = read_checkpoint_at(self._fd, name, node_id)
        return active

    def _scan_stage(
        self, superseded_fd: int, stage: str, event_id: str
    ) -> PendingInvalidation | _DisposableStage:
        stage_fd = _open_checked_directory(superseded_fd, stage, "invalidation stage")
        try:
            names = list_names_at(stage_fd)
            if not names:
                return _DisposableStage(
                    stage, stage_fd, _identity(os.fstat(stage_fd))
                )
            if names == (INTENT,):
                intent = self.read_intent(stage_fd, event_id)
                os.close(stage_fd)
                return PendingInvalidation(event_id, intent, {}, staged=True)
            if len(names) != 1 or not names[0].startswith(".tmp-"):
                raise ValueError("invalidation stage has multiple or foreign entries")
            temporary = names[0]
            if temporary_target_digest(temporary) != target_name_digest(INTENT):
                raise ValueError("staged invalidation temporary targets another file")
            _, temporary_identity = read_regular_with_identity_at(
                stage_fd,
                temporary,
                description="staged invalidation temporary",
                required_mode=0o600,
                required_owner=os.geteuid(),
            )
            return _DisposableStage(
                stage,
                stage_fd,
                _identity(os.fstat(stage_fd)),
                temporary,
                temporary_identity,
            )
        except BaseException:
            os.close(stage_fd)
            raise

    @staticmethod
    def _remove_disposable_stage(
        superseded_fd: int, stage: _DisposableStage
    ) -> None:
        _require_stage_identity(superseded_fd, stage)
        expected = () if stage.temporary is None else (stage.temporary,)
        if list_names_at(stage.fd) != expected:
            raise ValueError("invalidation stage changed after validation")
        _require_stage_identity(superseded_fd, stage)
        if stage.temporary is not None:
            _require_regular_entry(stage.fd, stage.temporary, "staged temporary")
            metadata = os.stat(
                stage.temporary, dir_fd=stage.fd, follow_symlinks=False
            )
            if _identity(metadata) != stage.temporary_identity:
                raise ValueError("staged temporary identity changed")
            os.unlink(stage.temporary, dir_fd=stage.fd)
            os.fsync(stage.fd)
        _require_stage_identity(superseded_fd, stage)
        os.rmdir(stage.name, dir_fd=superseded_fd)
        os.fsync(superseded_fd)


def _open_or_create(parent_fd: int, name: str) -> int:
    try:
        return open_directory_at(parent_fd, name)
    except FileNotFoundError:
        try:
            return create_directory_at(parent_fd, name, mode=0o700)
        except FileExistsError:
            return open_directory_at(parent_fd, name)


def _open_checked_directory(parent_fd: int, name: str, description: str) -> int:
    try:
        descriptor = open_directory_at(parent_fd, name)
    except OSError as error:
        raise ValueError(f"{description} must be a non-symlink directory") from error
    try:
        _require_directory(descriptor, description)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _require_directory(descriptor: int, description: str) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError(f"{description} has invalid owner or mode")


def _require_regular_entry(parent_fd: int, name: str, description: str) -> None:
    metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise ValueError(f"{description} has invalid type, owner, mode, or link count")


def _require_stage_identity(parent_fd: int, stage: _DisposableStage) -> None:
    metadata = os.stat(stage.name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode) or _identity(metadata) != stage.identity:
        raise ValueError("invalidation stage identity changed")


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino
