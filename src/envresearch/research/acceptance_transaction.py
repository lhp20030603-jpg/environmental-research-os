"""Serialized, recoverable acceptance of one authenticated node candidate."""

from __future__ import annotations

from envresearch.kernel.artifact_graph import ArtifactNode
from envresearch.kernel.node_checkpoints import NodeCheckpointStore
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.ranking import CharterRankingPolicy
from envresearch.research.semantic_validation import SemanticSubmissionValidator
from envresearch.research.submission_acceptance import promote_authenticated_submission
from envresearch.workers.queue import FilesystemWorkerQueue


def accept_node_transaction(
    *,
    node: ArtifactNode,
    queue: FilesystemWorkerQueue,
    lifecycle: ResearchArtifactLifecycle,
    semantics: SemanticSubmissionValidator,
    checkpoints: NodeCheckpointStore,
    ranking_policy: CharterRankingPolicy,
) -> None:
    """Hold one process lock across candidate identity, lifecycle, and checkpoint."""
    with queue.control.transaction_lock("accept", node.node_id):
        promote_authenticated_submission(
            node=node,
            queue=queue,
            lifecycle=lifecycle,
            semantics=semantics,
            ranking_policy=ranking_policy,
        )
        inputs = lifecycle.input_refs(node)
        if not checkpoints.verify(node, inputs):
            checkpoints.publish(node, inputs, node.output_paths)
