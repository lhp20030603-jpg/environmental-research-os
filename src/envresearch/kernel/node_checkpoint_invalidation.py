"""Globally exclusive invalidation orchestration and crash recovery."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import cast

from envresearch.kernel.artifact_graph import ArtifactGraph
from envresearch.kernel.events import EventLogCorruptionError, EventRecord
from envresearch.kernel.node_checkpoint_archive import (
    InvalidationArchive,
    NamespaceSnapshot,
    PendingInvalidation,
)
from envresearch.kernel.node_checkpoint_events import (
    INVALIDATED,
    PinnedNodeEventLog,
    invalidated_event,
    replay_generations,
)
from envresearch.kernel.node_checkpoint_schema import NodeCheckpoint, canonical_bytes
from envresearch.kernel.task_identity import payload_hash

CheckpointRecord = tuple[NodeCheckpoint, bytes]


def invalidate_locked(
    graph: ArtifactGraph,
    node_id: str,
    reason: str,
    events_log: PinnedNodeEventLog,
    archive: InvalidationArchive,
    verify_outputs: Callable[[Mapping[str, str]], bool],
    move_targets: Callable[
        [int, str, frozenset[str], Mapping[str, CheckpointRecord]], None
    ],
) -> frozenset[str]:
    """Run or resume one invalidation while the caller holds the store lock."""
    prefix, suffix = events_log.read_prefix()
    snapshot = archive.preflight(invalidation_ids(prefix))
    pending = snapshot.pending
    if pending is not None:
        core = _pending_core(pending, graph, node_id, reason, snapshot)
        expected = _event_for_core(core, snapshot)
        events = events_log.read_for_expected(expected)
        snapshot = archive.preflight(invalidation_ids(events))
        pending = snapshot.pending
        if pending is None:
            matches = [
                event for event in events if event.event_id == expected.event_id
            ]
            if matches != [expected]:
                raise ValueError("completed invalidation event changed during retry")
            replay_generations(events, snapshot.active, snapshot.archives)
            targets = core.get("targets")
            if not isinstance(targets, list) or any(
                not isinstance(target, str) for target in targets
            ):
                raise TypeError("pending invalidation targets are malformed")
            return frozenset(targets)
    elif suffix:
        raise EventLogCorruptionError(
            "event log torn suffix has no recoverable invalidation transaction"
        )
    else:
        events = prefix

    pending_hashes = pending_source_hashes(pending)
    state = replay_generations(
        events,
        snapshot.active,
        snapshot.archives,
        pending_hashes=pending_hashes,
    )
    active = _historical_active(
        graph,
        node_id,
        snapshot,
        state,
        pending_hashes,
        verify_outputs,
    )
    archived = pending.records if pending is not None else {}
    if pending is None and node_id not in active:
        repeated = _completed_invalidation(events, node_id, reason)
        if repeated is None:
            raise FileNotFoundError("node has no active checkpoint to invalidate")
        return repeated
    completed = frozenset(set(active).union(archived))
    targets = graph.invalidate(node_id, completed)
    records = {key: value for key, value in active.items() if key in targets}
    records.update({key: value for key, value in archived.items() if key in targets})
    if set(records) != set(targets):
        raise ValueError("invalidation checkpoint set is incomplete")
    core = invalidation_core(node_id, reason, targets, records)
    event = _event_for_core(core, snapshot)
    intent_data = canonical_bytes(core)
    if pending is not None and pending.event_id != event.event_id:
        raise ValueError(
            f"pending invalidation {pending.event_id} must be recovered first"
        )
    if (
        pending is not None
        and pending.intent is not None
        and canonical_bytes(pending.intent) != intent_data
    ):
        raise ValueError("partial invalidation intent changed")
    archive_fd = (
        archive.prepare(event.event_id, intent_data)
        if pending is None or pending.staged
        else archive.open_archive(event.event_id)
    )
    try:
        move_targets(archive_fd, node_id, targets, records)
        archive.remove_intent(archive_fd, intent_data)
    finally:
        os.close(archive_fd)
    events_log.append_expected(event)
    return targets


def invalidation_ids(events: list[EventRecord]) -> set[str]:
    return {event.event_id for event in events if event.event_type == INVALIDATED}


def pending_source_hashes(pending: PendingInvalidation | None) -> dict[str, str]:
    if pending is None:
        return {}
    if pending.intent is not None:
        hashes = pending.intent.get("source_checkpoint_hashes")
        if not isinstance(hashes, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in hashes.items()
        ):
            raise ValueError("pending invalidation source hashes are malformed")
        return cast(dict[str, str], hashes)
    return {key: value[0].checkpoint_hash for key, value in pending.records.items()}


def invalidation_core(
    node_id: str,
    reason: str,
    targets: frozenset[str],
    records: Mapping[str, CheckpointRecord],
) -> dict[str, object]:
    return {
        "node_id": node_id,
        "reason": reason,
        "source_checkpoint_hashes": {
            key: records[key][0].checkpoint_hash for key in sorted(records)
        },
        "targets": sorted(targets),
    }


def _historical_active(
    graph: ArtifactGraph,
    source_node_id: str,
    snapshot: NamespaceSnapshot,
    state: Mapping[str, str],
    pending_hashes: Mapping[str, str],
    verify_outputs: Callable[[Mapping[str, str]], bool],
) -> dict[str, CheckpointRecord]:
    active: dict[str, CheckpointRecord] = {}
    for node in graph.nodes:
        record = snapshot.active.get(node.node_id)
        if record is None:
            continue
        checkpoint = record[0]
        explained = state.get(node.node_id) == checkpoint.checkpoint_hash
        transitional = pending_hashes.get(node.node_id) == checkpoint.checkpoint_hash
        if not (explained or transitional):
            continue
        if node.node_id != source_node_id and not verify_outputs(
            checkpoint.output_hashes
        ):
            continue
        active[node.node_id] = record
    return active


def _pending_core(
    pending: PendingInvalidation,
    graph: ArtifactGraph,
    node_id: str,
    reason: str,
    snapshot: NamespaceSnapshot,
) -> dict[str, object]:
    if pending.intent is not None:
        source = pending.intent.get("node_id")
        if source != node_id:
            raise ValueError(
                f"pending invalidation {pending.event_id} for {source} "
                "must be recovered first"
            )
        if pending.intent.get("reason") != reason:
            raise ValueError("pending invalidation reason changed")
        return pending.intent
    if not pending.records:
        raise FileExistsError("superseded event directory already exists")
    completed = frozenset(set(snapshot.active).union(pending.records))
    targets = graph.invalidate(node_id, completed)
    records = {key: value for key, value in snapshot.active.items() if key in targets}
    records.update(
        {key: value for key, value in pending.records.items() if key in targets}
    )
    core = invalidation_core(node_id, reason, targets, records)
    if f"research.node.invalidated.{payload_hash(core)}" != pending.event_id:
        raise ValueError(
            f"pending invalidation {pending.event_id} must be recovered first"
        )
    return core


def _event_for_core(
    core: Mapping[str, object], snapshot: NamespaceSnapshot
) -> EventRecord:
    hashes = core.get("source_checkpoint_hashes")
    if not isinstance(hashes, dict):
        raise TypeError("invalidation source hashes are malformed")
    by_hash = {
        checkpoint.checkpoint_hash: checkpoint
        for records in (snapshot.active, *snapshot.archives.values())
        for checkpoint, _ in records.values()
    }
    checkpoints = [by_hash.get(digest) for digest in hashes.values()]
    if not checkpoints or any(checkpoint is None for checkpoint in checkpoints):
        raise ValueError("invalidation checkpoint generation is missing")
    timestamp = max(
        checkpoint.completed_at for checkpoint in checkpoints if checkpoint is not None
    )
    return invalidated_event(core, timestamp)


def _completed_invalidation(
    events: list[EventRecord], node_id: str, reason: str
) -> frozenset[str] | None:
    matches = [
        event
        for event in events
        if event.event_type == INVALIDATED
        and event.payload.get("node_id") == node_id
        and event.payload.get("reason") == reason
    ]
    if not matches:
        return None
    targets = matches[-1].payload.get("targets")
    if not isinstance(targets, list):
        raise TypeError("recorded invalidation targets are malformed")
    return frozenset(targets)
