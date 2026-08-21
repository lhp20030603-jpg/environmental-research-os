"""Neutral public result model for design fixture replay."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from envresearch.benchmarks.blind_scoring_contracts import CaseEvaluation
from envresearch.benchmarks.design_scoring import QualityThresholdResult
from envresearch.connectors.contracts import ConnectorCoverage
from envresearch.models.design import ResearchQualityScores
from envresearch.research.workflow import ResearchRunPhase


class DesignFixtureReplay(BaseModel):
    """Recovered phase and exact actual-vs-expected authority inventory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    benchmark_id: str
    expected_phase: ResearchRunPhase
    actual_phase: ResearchRunPhase
    completed_nodes: tuple[str, ...]
    actual_authoritative_files: tuple[Path, ...]
    missing_authoritative_files: tuple[Path, ...]
    unexpected_authoritative_files: tuple[Path, ...]
    replayed_operations: int
    connector_coverage: ConnectorCoverage | None = None
    connector_coverage_bound: bool = False
    quality_scores: ResearchQualityScores
    threshold_results: tuple[QualityThresholdResult, ...]
    open_blockers: tuple[str, ...]
    overall_pass: bool
    blind_case_evaluation: CaseEvaluation | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
