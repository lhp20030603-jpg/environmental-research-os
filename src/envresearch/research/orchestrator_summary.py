"""Read-only phase summary construction for research orchestration."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from envresearch.kernel.artifact_graph import ArtifactGraph
from envresearch.kernel.gates import GateRequest
from envresearch.kernel.node_checkpoints import NodeCheckpointStore
from envresearch.models.enums import ArtifactLifecycle, GateStatus
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.workflow import ResearchRunPhase, ResearchRunSummary


def summarize(
    *,
    run_id: str,
    graph: ArtifactGraph,
    workspace: Path,
    lifecycle: ResearchArtifactLifecycle,
    checkpoints: NodeCheckpointStore,
    gate_lookup: Callable[[str], GateRequest | None],
    has_open_blocker: Callable[[], bool],
    require_complete_final: Callable[[], None],
    gate_order: tuple[str, ...],
) -> ResearchRunSummary:
    completed = checkpoints.completed_nodes(graph)
    pending_gates = tuple(
        gate.id
        for gate_id in gate_order
        if (gate := gate_lookup(gate_id)) is not None
        and gate.status is GateStatus.PENDING
    )
    pending_orders = tuple(
        node.node_id
        for node in graph.nodes
        if node.worker_role is not None
        and node.node_id not in completed
        and (workspace / f"work-orders/{node.node_id}.json").exists()
    )
    plan_path = Path("artifacts/analysis-plan.yaml")
    approved_plan = (workspace / plan_path).exists() and lifecycle.read_artifact(
        plan_path
    ).envelope.validation_status is ArtifactLifecycle.APPROVED
    rejected = any(
        (gate := gate_lookup(gate_id)) is not None
        and gate.status is GateStatus.REJECTED
        for gate_id in gate_order
    )
    if approved_plan and "final-approval" in completed:
        require_complete_final()
        phase = ResearchRunPhase.COMPLETE
    elif rejected or has_open_blocker():
        phase = ResearchRunPhase.BLOCKED
    elif pending_gates:
        phase = ResearchRunPhase.WAITING_FOR_GATE
    elif pending_orders:
        phase = ResearchRunPhase.WAITING_FOR_AGENT
    else:
        phase = ResearchRunPhase.DEGRADED
    return ResearchRunSummary(
        run_id=run_id,
        phase=phase,
        completed_nodes=tuple(
            node.node_id for node in graph.nodes if node.node_id in completed
        ),
        pending_work_order_nodes=pending_orders,
        pending_gate_ids=pending_gates,
        approved_artifact=plan_path if approved_plan else None,
    )
