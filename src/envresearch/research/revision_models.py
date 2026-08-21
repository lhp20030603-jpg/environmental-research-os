"""Public immutable contracts for research revision transactions."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, model_validator

from envresearch.models.artifact import ArtifactRef
from envresearch.models.principal import PrincipalAssignment


class RevisionArtifact(BaseModel):
    """One exact authoritative artifact observed before revision mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Path
    ref: ArtifactRef


class RevisionGateTarget(BaseModel):
    """Exact active gate context, or authenticated absence, at intent creation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    base_gate_id: str
    gate_id: str | None
    context_hash: str | None

    @model_validator(mode="after")
    def require_paired_context_identity(self) -> RevisionGateTarget:
        if (self.gate_id is None) != (self.context_hash is None):
            raise ValueError("revision gate target identity is incomplete")
        return self


class RevisionIntent(BaseModel):
    """Immutable reason, actor, target, and current identity for one revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    revision_id: str
    run_id: str
    generation: int
    node_id: str
    actor: str
    principal_assignment: PrincipalAssignment
    reason: str
    target_artifacts: tuple[RevisionArtifact, ...]
    affected_nodes: tuple[str, ...]
    checkpoint_nodes: tuple[str, ...]
    worker_nodes: tuple[str, ...]
    gate_targets: tuple[RevisionGateTarget, ...]
    created_at: datetime

    @model_validator(mode="after")
    def require_unique_gate_targets(self) -> RevisionIntent:
        identities = tuple(target.base_gate_id for target in self.gate_targets)
        if len(identities) != len(set(identities)):
            raise ValueError("revision gate targets must be unique")
        return self
