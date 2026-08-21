"""Research-level principal separation checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from envresearch.kernel.gates import GateRequest

if TYPE_CHECKING:
    from envresearch.research.orchestrator import ResearchOrchestrator


def require_gate_principal(
    orchestrator: ResearchOrchestrator, base_gate_id: str, gate: GateRequest
) -> None:
    """Authenticate the gate decision against every current worker principal."""
    if gate.decision is None:
        raise ValueError("gate decision lacks the authenticated gate principal")
    context = orchestrator.bound_gates.active_context(base_gate_id)
    if context is None:
        raise ValueError("gate decision lacks an authenticated gate context")
    versions = {
        (ref.artifact_id, ref.artifact_version) for ref in context.artifact_refs
    }
    reviewed = tuple(
        artifact.envelope.producer
        for node in orchestrator.graph.nodes
        for path in node.output_paths[:1]
        for ref in context.artifact_refs
        if path.stem == ref.artifact_id
        for artifact in (
            orchestrator.lifecycle.read_history(path, ref.artifact_version),
        )
        if (ref.artifact_id, ref.artifact_version) in versions
        and node.worker_role is not None
    )
    orchestrator.principals.require_gate_actor(gate.decision.decided_by, reviewed)
    orchestrator.principals.require_gate_decision(gate)
