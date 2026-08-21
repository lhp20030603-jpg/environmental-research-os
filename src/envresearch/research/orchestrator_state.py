"""Small read-only helpers for orchestrator policy state."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from envresearch.models.evidence import DataFeasibilityPayload
from envresearch.research.workflow import data_risk_reasons

if TYPE_CHECKING:
    from envresearch.research.orchestrator import ResearchOrchestrator


def current_data_risk_reasons(
    orchestrator: ResearchOrchestrator,
) -> tuple[str, ...]:
    """Return risk reasons from the current validated feasibility artifact."""
    path = Path("artifacts/data-feasibility.yaml")
    if not (orchestrator.workspace / path).exists():
        return ()
    feasibility = orchestrator.lifecycle.read_payload(path, DataFeasibilityPayload)
    return data_risk_reasons(feasibility, orchestrator.config.acquisition_budget)
