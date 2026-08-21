"""Serialized connector-coverage binding entry point."""

from __future__ import annotations

from typing import TYPE_CHECKING

from envresearch.connectors.contracts import ConnectorCoverage
from envresearch.research.node_inputs import (
    adopt_literature_coverage_state,
    bind_literature_coverage,
)

if TYPE_CHECKING:
    from envresearch.research.orchestrator import ResearchOrchestrator


def bind_coverage(
    orchestrator: ResearchOrchestrator, coverage: ConnectorCoverage
) -> None:
    """Bind coverage while holding the complete run mutation lock."""
    with orchestrator.queue.control.transaction_lock("mutation"):
        graph = bind_literature_coverage(
            orchestrator.lifecycle, orchestrator.graph, coverage
        )
        adopt_literature_coverage_state(orchestrator, graph)
