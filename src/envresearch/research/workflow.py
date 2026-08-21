"""Declarative, execution-free Discover and Design research graph."""

from __future__ import annotations

import re
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from envresearch.kernel.artifact_graph import ArtifactGraph, ArtifactNode
from envresearch.models.evidence import AcquisitionBudget, DataFeasibilityPayload
from envresearch.models.intake import ResearchIntakeMode
from envresearch.research.final_binding import FINAL_INPUT_PATHS
from envresearch.research.ranking import CharterRankingPolicy
from envresearch.workers.contracts import WorkerRole

__all__ = [
    "ARTIFACT_PATHS",
    "ResearchRunConfig",
    "ResearchRunPhase",
    "ResearchRunSummary",
    "build_research_graph",
    "data_risk_reasons",
]

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


ARTIFACT_PATHS: dict[str, tuple[Path, ...]] = {
    "normalize-brief": (Path("artifacts/research-brief.yaml"),),
    "frame-charters": (Path("artifacts/candidate-charters.json"),),
    "approve-charter": (Path("artifacts/research-charter.yaml"),),
    "map-literature": (
        Path("artifacts/literature-map.json"),
        Path("artifacts/evidence-matrix.csv"),
        Path("artifacts/evidence-matrix.meta.json"),
    ),
    "inspect-data": (Path("artifacts/data-feasibility.yaml"),),
    "acquire-public-data": (Path("artifacts/data-provenance.yaml"),),
    "define-estimand": (Path("artifacts/estimand-spec.yaml"),),
    "rank-methods": (Path("artifacts/method-candidates.json"),),
    "draft-identification": (Path("artifacts/identification-memo.md"),),
    "review-design": (Path("artifacts/design-review-findings.json"),),
    "compose-plan": (Path("artifacts/analysis-plan.yaml"),),
    "validate-citations": (Path("artifacts/citation-integrity-report.json"),),
    "final-approval": (),
}


class ResearchRunPhase(StrEnum):
    """Externally visible pause or terminal phases for one research run."""

    WAITING_FOR_AGENT = "waiting_for_agent"
    WAITING_FOR_GATE = "waiting_for_gate"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    COMPLETE = "complete"


class ResearchRunConfig(BaseModel):
    """Immutable, local resource bounds and identity for one V0.2 run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace: Path
    run_id: str
    input_mode: ResearchIntakeMode
    requested_by: str = "research-orchestrator"
    require_claim_verified_citations: StrictBool = False
    citation_catalog_roots: tuple[Path, ...] = ()
    ranking_policy: CharterRankingPolicy = Field(default_factory=CharterRankingPolicy)
    config_sha256: str | None = None
    acquisition_budget: AcquisitionBudget = Field(
        default_factory=lambda: AcquisitionBudget(
            max_download_bytes=50_000_000,
            max_local_storage_bytes=100_000_000,
            max_api_calls=100,
            max_external_cost=Decimal(0),
            max_elapsed_seconds=3_600,
        )
    )

    @field_validator("run_id", "requested_by")
    @classmethod
    def require_safe_identity(cls, value: str) -> str:
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("run identity must be a safe identifier")
        return value

    @field_validator("workspace")
    @classmethod
    def normalize_workspace(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("citation_catalog_roots")
    @classmethod
    def normalize_catalog_roots(cls, value: tuple[Path, ...]) -> tuple[Path, ...]:
        roots = tuple(
            sorted((item.expanduser().resolve(strict=True) for item in value), key=str)
        )
        if len(roots) != len(set(roots)):
            raise ValueError("citation catalog roots must be unique")
        return roots

    @field_validator("config_sha256")
    @classmethod
    def require_config_digest(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256.fullmatch(value):
            raise ValueError("config_sha256 must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def require_strict_catalog_authority(self) -> ResearchRunConfig:
        if self.require_claim_verified_citations and not self.citation_catalog_roots:
            raise ValueError("strict citation policy requires an authorized catalog")
        return self


class ResearchRunSummary(BaseModel):
    """Deterministic status snapshot returned after each advancement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    phase: ResearchRunPhase
    completed_nodes: tuple[str, ...]
    pending_work_order_nodes: tuple[str, ...] = ()
    pending_gate_ids: tuple[str, ...] = ()
    approved_artifact: Path | None = None


def data_risk_reasons(
    feasibility: DataFeasibilityPayload, budget: AcquisitionBudget
) -> tuple[str, ...]:
    """Return every access, license, cost, and budget reason requiring a gate."""
    reasons: list[str] = []
    for item in feasibility.candidates:
        if not item.public_access:
            reasons.append(f"{item.dataset_id}: not public")
        if item.requires_credentials:
            reasons.append(f"{item.dataset_id}: credentials required")
        if not item.clear_license:
            reasons.append(f"{item.dataset_id}: license unclear")
        if item.estimated_external_cost > 0:
            reasons.append(f"{item.dataset_id}: nonzero external cost")
        reasons.extend(
            f"{item.dataset_id}: {reason}" for reason in budget.estimate_reasons(item)
        )
    return tuple(reasons)


def build_research_graph(
    input_mode: ResearchIntakeMode | str,
    *,
    require_claim_verified_citations: bool = False,
) -> ArtifactGraph:
    """Build one of the two fixed entry branches and their shared design DAG."""
    mode = ResearchIntakeMode(input_mode)
    entry = _entry_node(mode)
    entry_output = entry.output_paths[0]
    nodes: tuple[ArtifactNode, ...] = (
        entry,
        _node(
            "approve-charter",
            dependencies=(entry.node_id,),
            inputs=(entry_output,),
            gate="gate-1",
        ),
        _node(
            "map-literature",
            role=WorkerRole.LITERATURE_CARTOGRAPHER,
            dependencies=("approve-charter",),
            inputs=(Path("artifacts/research-charter.yaml"),),
        ),
        _node(
            "inspect-data",
            role=WorkerRole.DATA_SCOUT,
            dependencies=("approve-charter",),
            inputs=(Path("artifacts/research-charter.yaml"),),
        ),
        _node(
            "define-estimand",
            role=WorkerRole.ESTIMAND_DESIGNER,
            dependencies=("map-literature", "inspect-data"),
            inputs=(
                Path("artifacts/literature-map.json"),
                Path("artifacts/evidence-matrix.csv"),
                Path("artifacts/data-feasibility.yaml"),
            ),
            gate="data-clearance",
        ),
        _node(
            "rank-methods",
            role=WorkerRole.METHOD_STRATEGIST,
            dependencies=("define-estimand",),
            inputs=(Path("artifacts/estimand-spec.yaml"),),
        ),
        _node(
            "draft-identification",
            role=WorkerRole.METHOD_STRATEGIST,
            dependencies=("rank-methods",),
            inputs=(
                Path("artifacts/estimand-spec.yaml"),
                Path("artifacts/method-candidates.json"),
            ),
        ),
        _node(
            "review-design",
            role=WorkerRole.DESIGN_CRITIC,
            dependencies=("draft-identification",),
            inputs=(
                Path("artifacts/research-charter.yaml"),
                Path("artifacts/literature-map.json"),
                Path("artifacts/evidence-matrix.csv"),
                Path("artifacts/data-feasibility.yaml"),
                Path("artifacts/estimand-spec.yaml"),
                Path("artifacts/method-candidates.json"),
                Path("artifacts/identification-memo.md"),
            ),
        ),
        _node(
            "compose-plan",
            role=WorkerRole.PLAN_COMPOSER,
            dependencies=("review-design",),
            inputs=(
                Path("artifacts/research-charter.yaml"),
                Path("artifacts/literature-map.json"),
                Path("artifacts/evidence-matrix.csv"),
                Path("artifacts/data-feasibility.yaml"),
                Path("artifacts/estimand-spec.yaml"),
                Path("artifacts/method-candidates.json"),
                Path("artifacts/identification-memo.md"),
                Path("artifacts/design-review-findings.json"),
            ),
        ),
    )
    if require_claim_verified_citations:
        nodes += (
            _node(
                "validate-citations",
                dependencies=("compose-plan",),
                inputs=(Path("artifacts/analysis-plan.yaml"),),
            ),
        )
    predecessor = (
        "validate-citations" if require_claim_verified_citations else "compose-plan"
    )
    final_inputs = FINAL_INPUT_PATHS + (
        (Path("artifacts/citation-integrity-report.json"),)
        if require_claim_verified_citations
        else ()
    )
    return ArtifactGraph(
        nodes
        + (
            _node(
                "final-approval",
                dependencies=(predecessor,),
                inputs=final_inputs,
                gate="final-gate",
            ),
        )
    )


def _entry_node(mode: ResearchIntakeMode) -> ArtifactNode:
    if mode is ResearchIntakeMode.BROAD_TOPIC:
        return _node(
            "frame-charters",
            role=WorkerRole.RESEARCH_FRAMER,
            inputs=(Path("artifacts/research-brief.yaml"),),
        )
    return _node(
        "normalize-brief",
        role=WorkerRole.RESEARCH_FRAMER,
        inputs=(Path("artifacts/intake-brief.yaml"),),
    )


def _node(
    node_id: str,
    *,
    role: WorkerRole | None = None,
    dependencies: tuple[str, ...] = (),
    inputs: tuple[Path, ...] = (),
    gate: str | None = None,
) -> ArtifactNode:
    return ArtifactNode(
        node_id=node_id,
        worker_role=None if role is None else role.value,
        dependencies=dependencies,
        input_paths=inputs,
        output_paths=ARTIFACT_PATHS[node_id],
        required_gate=gate,
        version="1",
    )
