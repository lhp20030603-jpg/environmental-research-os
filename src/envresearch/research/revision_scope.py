"""Live durable-state traversal for ancestor revisions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from envresearch.models.enums import ArtifactLifecycle

if TYPE_CHECKING:
    from envresearch.research.revisions import RevisionTransaction


def live_revision_scope(
    transaction: RevisionTransaction,
    node_id: str,
    completed: frozenset[str],
) -> tuple[str, ...]:
    """Include every descendant with a live order, checkpoint, or artifact."""
    candidates = {node_id, *transaction.graph.descendants(node_id)}
    affected: list[str] = []
    for candidate in sorted(candidates):
        node = transaction.nodes[candidate]
        has_artifact = any(
            (transaction.workspace / path).exists()
            and transaction.lifecycle.current_envelope(path).validation_status
            is not ArtifactLifecycle.SUPERSEDED
            for path in node.output_paths
            if path.suffix in {".json", ".yaml", ".csv", ".md"}
            and not path.name.endswith(".meta.json")
        )
        has_order = node.worker_role is not None and transaction.queue.has_generation(
            candidate
        )
        if candidate == node_id or candidate in completed or has_artifact or has_order:
            affected.append(candidate)
    return tuple(affected)
