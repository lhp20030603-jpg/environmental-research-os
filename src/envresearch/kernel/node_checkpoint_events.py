"""Pinned event I/O and chronological checkpoint-generation replay."""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from envresearch.kernel.events import EventLog, EventLogCorruptionError, EventRecord
from envresearch.kernel.node_checkpoint_schema import (
    SHA256,
    NodeCheckpoint,
    load_json_object,
    require_reason,
    require_safe_id,
)
from envresearch.kernel.task_identity import payload_hash
from envresearch.models.enums import WorkflowStatus
from envresearch.workers.filesystem import PinnedRoot

PASSED = "research.node.passed"
INVALIDATED = "research.node.invalidated"
RUN_ID = "research-workflow"
ACTOR = "research-orchestrator"
_FIELDS = {
    "event_id",
    "run_id",
    "event_type",
    "actor",
    "timestamp",
    "from_status",
    "to_status",
    "payload",
}


def passed_event(checkpoint: NodeCheckpoint) -> EventRecord:
    return EventRecord(
        event_id=f"research.node.passed.{checkpoint.checkpoint_hash}",
        run_id=RUN_ID,
        event_type=PASSED,
        actor=ACTOR,
        timestamp=checkpoint.completed_at,
        from_status=WorkflowStatus.RUNNING,
        to_status=WorkflowStatus.RUNNING,
        payload={
            "node_id": checkpoint.node_id,
            "node_version": checkpoint.node_version,
            "definition_hash": checkpoint.definition_hash,
            "input_hashes": dict(checkpoint.input_hashes),
            "output_hashes": dict(checkpoint.output_hashes),
            "checkpoint_hash": checkpoint.checkpoint_hash,
        },
    )


def invalidated_event(
    core: Mapping[str, object], checkpoint_time: datetime
) -> EventRecord:
    return EventRecord(
        event_id=f"research.node.invalidated.{payload_hash(core)}",
        run_id=RUN_ID,
        event_type=INVALIDATED,
        actor=ACTOR,
        timestamp=checkpoint_time,
        from_status=WorkflowStatus.RUNNING,
        to_status=WorkflowStatus.RUNNING,
        payload=dict(core),
    )


class PinnedNodeEventLog(EventLog):
    """Strict node-event view anchored to a store-owned workspace descriptor."""

    def __init__(
        self, path: Path, root: PinnedRoot, ensure_open: Callable[[], None]
    ) -> None:
        super().__init__(path)
        self._root = root
        self._ensure_open = ensure_open

    def validate_file(self) -> None:
        """Validate the final entry without requiring a complete JSONL suffix."""
        self._ensure_open()
        try:
            descriptor = self._open(os.O_RDONLY)
        except FileNotFoundError:
            return
        try:
            _require_regular(descriptor, "event log")
        finally:
            os.close(descriptor)

    def append(self, event: EventRecord) -> None:
        """Append one durable record through the pinned workspace root."""
        self._ensure_open()
        durable = EventRecord.model_validate(dict(event.__dict__))
        descriptor = self._open(os.O_WRONLY | os.O_APPEND | os.O_CREAT, mode=0o600)
        try:
            _require_regular(descriptor, "event log")
            _write_all(descriptor, _record_bytes(durable))
            os.fsync(descriptor)
            os.fsync(self._root.fd)
        finally:
            os.close(descriptor)

    def read_all(self) -> list[EventRecord]:
        """Read complete strict, duplicate-free event objects."""
        self._ensure_open()
        data = self._read_bytes()
        events, suffix = _parse_complete_prefix(data)
        if suffix:
            raise EventLogCorruptionError(
                f"event log corruption at line {len(events) + 1}: "
                "event record is not newline terminated"
            )
        return events

    def read_prefix(self) -> tuple[list[EventRecord], bytes]:
        """Return strict complete records plus an opaque final torn suffix."""
        self._ensure_open()
        return _parse_complete_prefix(self._read_bytes())

    def read_for_expected(self, expected: EventRecord) -> list[EventRecord]:
        """Repair only a suffix that is an exact prefix of ``expected``."""
        self._ensure_open()
        record = _record_bytes(expected)
        descriptor = self._open(os.O_RDWR | os.O_CREAT, mode=0o600)
        try:
            _require_regular(descriptor, "event log")
            data = _read_all(descriptor)
            events, suffix = _parse_complete_prefix(data)
            if suffix:
                if len(suffix) >= len(record) or not record.startswith(suffix):
                    raise EventLogCorruptionError(
                        "event log torn suffix does not match the expected retry event"
                    )
                os.ftruncate(descriptor, len(data) - len(suffix))
                os.fsync(descriptor)
                os.fsync(self._root.fd)
            return events
        finally:
            os.close(descriptor)

    def append_expected(self, expected: EventRecord) -> None:
        """Idempotently repair and append one deterministic expected event."""
        events = self.read_for_expected(expected)
        matches = [event for event in events if event.event_id == expected.event_id]
        if matches:
            if matches != [expected]:
                raise ValueError("event ID is already bound to different content")
            return
        self.append(expected)

    def _read_bytes(self) -> bytes:
        try:
            descriptor = self._open(os.O_RDONLY)
        except FileNotFoundError:
            return b""
        try:
            _require_regular(descriptor, "event log")
            return _read_all(descriptor)
        finally:
            os.close(descriptor)

    def _open(self, flags: int, *, mode: int = 0o600) -> int:
        flags |= (
            os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            return os.open("events.jsonl", flags, mode, dir_fd=self._root.fd)
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ValueError(
                    "event log must be a regular non-symlink file"
                ) from error
            raise


def replay_generations(
    events: list[EventRecord],
    active: Mapping[str, tuple[NodeCheckpoint, bytes]],
    archives: Mapping[str, Mapping[str, tuple[NodeCheckpoint, bytes]]],
    *,
    pending_hashes: Mapping[str, str] | None = None,
    allow_unrecorded: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Validate chronological pass/invalidation state and physical active state."""
    pending_hashes = pending_hashes or {}
    allow_unrecorded = allow_unrecorded or {}
    locations: dict[str, list[NodeCheckpoint]] = {}
    for checkpoint, _ in active.values():
        locations.setdefault(checkpoint.checkpoint_hash, []).append(checkpoint)
    for records in archives.values():
        for checkpoint, _ in records.values():
            locations.setdefault(checkpoint.checkpoint_hash, []).append(checkpoint)
    duplicated = [digest for digest, values in locations.items() if len(values) != 1]
    if duplicated:
        raise ValueError("active/archive checkpoint generation collision")

    state: dict[str, str] = {}
    for event in events:
        if event.event_type == PASSED:
            digest = event.payload.get("checkpoint_hash")
            candidates = locations.get(digest) if isinstance(digest, str) else None
            if not candidates or event != passed_event(candidates[0]):
                raise ValueError("recorded pass event is not bound to a checkpoint")
            node_id = candidates[0].node_id
            if node_id in state:
                raise ValueError("multiple pass events exist in one active generation")
            state[node_id] = candidates[0].checkpoint_hash
        elif event.event_type == INVALIDATED:
            hashes = validate_invalidation_event(event, archives)
            for node_id, digest in hashes.items():
                if state.get(node_id) != digest:
                    raise ValueError(
                        "invalidation does not consume the active generation"
                    )
            for node_id in hashes:
                state.pop(node_id)

    for node_id, digest in pending_hashes.items():
        if state.get(node_id) != digest:
            raise ValueError("pending invalidation generation changed")
        state.pop(node_id)

    for node_id, (checkpoint, _) in active.items():
        digest = checkpoint.checkpoint_hash
        if node_id in pending_hashes and pending_hashes[node_id] == digest:
            continue
        if state.get(node_id) == digest:
            continue
        if allow_unrecorded.get(node_id) == digest and node_id not in state:
            continue
        raise ValueError("active checkpoint is not explained by event generation state")
    for node_id, digest in state.items():
        if node_id not in active or active[node_id][0].checkpoint_hash != digest:
            raise ValueError("event generation has no matching active checkpoint")
    return state


def validate_invalidation_event(
    event: EventRecord,
    archives: Mapping[str, Mapping[str, tuple[NodeCheckpoint, bytes]]],
) -> dict[str, str]:
    """Validate fixed metadata, deterministic identity, and exact archive binding."""
    if (
        event.run_id != RUN_ID
        or event.actor != ACTOR
        or event.from_status != WorkflowStatus.RUNNING
        or event.to_status != WorkflowStatus.RUNNING
        or set(event.payload)
        != {"node_id", "reason", "source_checkpoint_hashes", "targets"}
    ):
        raise ValueError("recorded invalidation event metadata changed")
    node_id = event.payload["node_id"]
    reason = event.payload["reason"]
    hashes = event.payload["source_checkpoint_hashes"]
    targets = event.payload["targets"]
    require_safe_id(node_id, "invalidated node ID")
    require_reason(reason)
    if (
        not isinstance(targets, list)
        or not targets
        or any(not isinstance(target, str) for target in targets)
        or targets != sorted(set(targets))
        or node_id not in targets
        or not isinstance(hashes, dict)
        or tuple(hashes) != tuple(sorted(hashes))
        or set(hashes) != set(targets)
        or any(
            not isinstance(value, str) or not SHA256.fullmatch(value)
            for value in hashes.values()
        )
    ):
        raise ValueError("recorded invalidation event payload changed")
    for target in targets:
        require_safe_id(target, "invalidation target ID")
    if event.event_id != f"research.node.invalidated.{payload_hash(event.payload)}":
        raise ValueError("recorded invalidation event identity changed")
    records = archives.get(event.event_id)
    if records is None:
        raise ValueError("recorded invalidation archive is missing")
    actual = {key: value[0].checkpoint_hash for key, value in records.items()}
    if actual != hashes or event.timestamp != max(
        checkpoint.completed_at for checkpoint, _ in records.values()
    ):
        raise ValueError("recorded invalidation archive changed")
    return cast(dict[str, str], hashes)


def _parse_complete_prefix(data: bytes) -> tuple[list[EventRecord], bytes]:
    boundary = data.rfind(b"\n") + 1
    complete, suffix = data[:boundary], data[boundary:]
    events: list[EventRecord] = []
    event_ids: set[str] = set()
    for line_number, raw_line in enumerate(complete.splitlines(), 1):
        try:
            value = load_json_object(raw_line)
            if set(value) != _FIELDS:
                raise ValueError("event record fields are not exact")
            event = EventRecord.model_validate(value)
            if event.event_id in event_ids:
                raise ValueError("duplicate event ID")
            event_ids.add(event.event_id)
            events.append(event)
        except (UnicodeError, ValueError, TypeError, ValidationError) as error:
            raise EventLogCorruptionError(
                f"event log corruption at line {line_number}: {error}"
            ) from error
    return events, suffix


def _record_bytes(event: EventRecord) -> bytes:
    return (event.model_dump_json() + "\n").encode()


def _require_regular(descriptor: int, description: str) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise ValueError(
            f"{description} must be an owner-only regular non-symlink single-link file"
        )


def _read_all(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while chunk := os.read(descriptor, 1024 * 1024):
        chunks.append(chunk)
    return b"".join(chunks)


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        remaining = remaining[os.write(descriptor, remaining) :]
