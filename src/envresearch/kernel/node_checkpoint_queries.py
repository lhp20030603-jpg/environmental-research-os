"""Exact historical queries over validated checkpoint generations."""

from __future__ import annotations

from envresearch.kernel.events import EventRecord
from envresearch.kernel.node_checkpoint_archive import NamespaceSnapshot
from envresearch.kernel.node_checkpoint_events import (
    INVALIDATED,
    replay_generations,
    validate_invalidation_event,
)
from envresearch.kernel.node_checkpoint_invalidation import pending_source_hashes


def require_completed_invalidation(
    events: list[EventRecord],
    snapshot: NamespaceSnapshot,
    *,
    node_id: str,
    reason: str,
    affected_nodes: frozenset[str],
) -> dict[str, str]:
    """Return archive hashes only for one exact, fully replayed invalidation."""
    replay_generations(
        events,
        snapshot.active,
        snapshot.archives,
        pending_hashes=pending_source_hashes(snapshot.pending),
    )
    matches = [
        event
        for event in events
        if event.event_type == INVALIDATED
        and event.payload.get("node_id") == node_id
        and event.payload.get("reason") == reason
    ]
    if len(matches) != 1:
        raise ValueError("exact completed checkpoint invalidation is missing")
    hashes = validate_invalidation_event(matches[0], snapshot.archives)
    if frozenset(hashes) != affected_nodes:
        raise ValueError("checkpoint invalidation affected set changed")
    return hashes
