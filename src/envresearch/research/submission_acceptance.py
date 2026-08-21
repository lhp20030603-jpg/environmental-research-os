"""Trusted acceptance transaction inputs for authenticated worker submissions."""

from __future__ import annotations

from pathlib import Path

from envresearch.kernel.artifact_graph import ArtifactNode
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.producer_identity import (
    authenticated_producer,
    require_independent_critic,
)
from envresearch.research.ranking import CharterRankingPolicy
from envresearch.research.semantic_validation import SemanticSubmissionValidator
from envresearch.research.submission_policy import apply_submission_policy
from envresearch.workers.queue import FilesystemWorkerQueue


def promote_authenticated_submission(
    *,
    node: ArtifactNode,
    queue: FilesystemWorkerQueue,
    lifecycle: ResearchArtifactLifecycle,
    semantics: SemanticSubmissionValidator,
    ranking_policy: CharterRankingPolicy,
) -> None:
    """Validate one queue-authenticated candidate and preserve receipt identity."""
    submissions = queue.collect(node.node_id)
    if len(submissions) != 1 or len(submissions[0].candidate_relative_paths) != 1:
        raise ValueError("work order requires exactly one authenticated candidate")
    order = queue.read_order(node.node_id)
    inputs = lifecycle.input_refs(node)
    if order.input_artifacts != inputs:
        raise ValueError("sealed work order does not match current DAG inputs")
    candidate = queue.exchange.read_file(
        submissions[0].candidate_relative_paths[0], description="worker candidate"
    )
    payload = lifecycle.validate_candidate(node.node_id, candidate)
    payload = apply_submission_policy(node.node_id, payload, ranking_policy)
    semantics.validate(node.node_id, payload, inputs)
    if submissions[0].principal_assignment != order.principal_assignment:
        raise ValueError("submission principal assignment mismatch")
    producer = authenticated_producer(submissions[0].producer)
    if node.node_id == "review-design":
        require_independent_critic(
            producer,
            tuple(
                lifecycle.artifact_producer(path)
                for path in node.input_paths
                if path != Path("artifacts/research-charter.yaml")
            ),
        )
    lifecycle.promote_submission(node, payload, inputs, producer)
