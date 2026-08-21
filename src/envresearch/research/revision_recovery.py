"""Reconcile authenticated revision events with their durable side effects."""

from __future__ import annotations

from typing import TYPE_CHECKING

from envresearch.models.enums import ArtifactLifecycle

if TYPE_CHECKING:
    from envresearch.kernel.artifact_graph import ArtifactNode
    from envresearch.kernel.node_checkpoints import NodeCheckpointStore
    from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
    from envresearch.research.audit_state import ResearchAuditState
    from envresearch.research.gate_context import BoundGateManager
    from envresearch.research.revision_models import RevisionIntent
    from envresearch.workers.queue import FilesystemWorkerQueue


def require_recorded_side_effects(
    intent: RevisionIntent,
    events: set[str],
    *,
    nodes: dict[str, ArtifactNode],
    queue: FilesystemWorkerQueue,
    lifecycle: ResearchArtifactLifecycle,
    gates: BoundGateManager,
    audit: ResearchAuditState,
    checkpoints: NodeCheckpointStore,
) -> None:
    """Never trust a claimed recovery boundary without its durable effect."""
    if "revision_completed" in events and not any(
        entry.event_id == f"revision-request:{intent.revision_id}"
        for entry in audit.decisions.read_all()
    ):
        raise ValueError("revision journal audit side effect missing")
    if "worker_namespace_archived" in events:
        from envresearch.workers.revision_archive import validate_archive

        for node_id in intent.worker_nodes:
            try:
                validate_archive(
                    queue,
                    node_id,
                    intent.revision_id,
                    allow_cancellation=node_id not in intent.checkpoint_nodes,
                )
            except (OSError, ValueError) as error:
                raise ValueError(
                    "revision journal worker archive side effect missing"
                ) from error
    if "checkpoints_invalidated" in events:
        try:
            checkpoints.require_invalidation(
                intent.node_id,
                f"revision {intent.revision_id}: {intent.reason}",
                frozenset(intent.checkpoint_nodes),
            )
        except (OSError, ValueError) as error:
            raise ValueError(
                "revision journal checkpoint side effect missing"
            ) from error
    if "artifacts_superseded" in events:
        for target in intent.target_artifacts:
            superseded = lifecycle.read_history(
                target.path, target.ref.artifact_version + 1
            )
            envelope = superseded.envelope
            if (
                envelope.validation_status is not ArtifactLifecycle.SUPERSEDED
                or envelope.provenance.get("revision_id") != intent.revision_id
            ):
                raise ValueError("revision journal artifact side effect missing")
    if "gates_superseded" in events and not all(
        gates.revision_effect_is_durable(
            target.base_gate_id,
            target.gate_id,
            target.context_hash,
            intent.revision_id,
            intent.actor,
            intent.reason,
        )
        for target in intent.gate_targets
    ):
        raise ValueError("revision journal gate side effect missing")
