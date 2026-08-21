"""Serialized orchestrator entry point for research revisions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from envresearch.models.principal import PrincipalKind
from envresearch.research.node_inputs import adopt_literature_coverage_state

if TYPE_CHECKING:
    from envresearch.research.orchestrator import ResearchOrchestrator
    from envresearch.research.revision_models import RevisionIntent


def request_revision(
    orchestrator: ResearchOrchestrator,
    node_id: str,
    reason: str,
    actor: str,
    principal_capability: str,
) -> RevisionIntent:
    """Authenticate and serialize one target-plus-live-descendant revision."""
    with orchestrator.queue.control.transaction_lock("mutation"):
        principal = orchestrator.principals.require_capability(
            PrincipalKind.REVISION, principal_capability
        )
        adopt_literature_coverage_state(orchestrator)
        intent = orchestrator.revisions.request(
            node_id,
            reason=reason,
            actor=actor,
            principal=principal,
        )
        orchestrator._issue_ready()
        orchestrator.audit.sync()
        return intent
