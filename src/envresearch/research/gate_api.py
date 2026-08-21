"""Owner-control entry point for authenticated human gate decisions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from envresearch.kernel.gates import GateDecision, GateRequest
from envresearch.models.principal import PrincipalKind

if TYPE_CHECKING:
    from envresearch.research.orchestrator import ResearchOrchestrator


def decide_gate(
    orchestrator: ResearchOrchestrator,
    base_gate_id: str,
    decision: GateDecision,
    principal_capability: str,
) -> GateRequest:
    """Persist and owner-authenticate one exact active-context decision."""
    with orchestrator.queue.control.transaction_lock("mutation"):
        orchestrator.principals.require_capability(
            PrincipalKind.GATE, principal_capability
        )
        context = orchestrator.bound_gates.active_context(base_gate_id)
        if context is None:
            raise ValueError("gate decision requires an active context")
        active = orchestrator.bound_gates.active_gate(base_gate_id)
        if active is not None and active.decision is not None:
            if not _same_claim(active.decision, decision):
                raise ValueError("terminal gate differs from the retried decision")
            decided = active
        else:
            decided = orchestrator.gates.decide(context.gate_id, decision)
        orchestrator.principals.record_gate_decision(decided)
        return decided


def _same_claim(first: GateDecision, second: GateDecision) -> bool:
    return (
        first.status == second.status
        and first.decided_by == second.decided_by
        and first.rationale == second.rationale
        and first.conditions == second.conditions
    )
