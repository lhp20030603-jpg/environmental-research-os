"""Research-owned immutable evidence for reconstructing a correct stop."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._audit_lineage import (
    artifact_ref_key,
)
from envresearch.paper._audit_lineage import (
    strict_model_input as _model_input,
)

STRICT = ConfigDict(
    extra="forbid", frozen=True, strict=True, revalidate_instances="always"
)


def strict_model_input(value: object) -> object:
    return _model_input(value)


def strict_artifact_input(value: object) -> object:
    if isinstance(value, ArtifactRef):
        value = {
            "artifact_id": value.artifact_id,
            "artifact_version": value.artifact_version,
            "content_hash": value.content_hash,
        }
    fields = ("artifact_id", "artifact_version", "content_hash")
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("strict artifact ref input is malformed")
    if any(
        type(value[field]) is not expected
        for field, expected in zip(fields, (str, int, str), strict=True)
    ):
        raise ValueError("strict artifact ref fields cannot be coerced")
    return value


StrictArtifactRef = Annotated[ArtifactRef, BeforeValidator(strict_artifact_input)]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _require_sha256(value: str) -> str:
    if not _SHA256.fullmatch(value):
        raise ValueError("research evidence digest must be a full lowercase SHA-256")
    return value


ResearchSha256 = Annotated[str, AfterValidator(_require_sha256)]


class ResearchFileEvidence(BaseModel):
    """One descriptor-relative research-owned file or directory observation."""

    model_config = STRICT
    relative_path: str
    kind: Literal["file", "directory", "symlink"]
    sha256: ResearchSha256 | None
    size_bytes: int = Field(ge=0)
    mode: int = Field(ge=0)
    symlink_target: str | None = None

    @model_validator(mode="after")
    def require_kind_evidence(self) -> ResearchFileEvidence:
        if (
            not self.relative_path
            or self.relative_path.startswith("/")
            or ".." in self.relative_path.split("/")
        ):
            raise ValueError("research evidence path must be canonical and relative")
        if self.kind == "file" and self.sha256 is None:
            raise ValueError("research file evidence requires a digest")
        if self.kind != "file" and self.sha256 is not None:
            raise ValueError("non-file research evidence cannot claim a byte digest")
        if (self.kind == "symlink") != (self.symlink_target is not None):
            raise ValueError("research symlink evidence is incomplete")
        return self


class ResearchCheckpointEvidence(BaseModel):
    """Exact completed-node checkpoint evidence reconstructed read-only."""

    model_config = STRICT
    node_id: str
    checkpoint_sha256: ResearchSha256
    artifact_refs: tuple[StrictArtifactRef, ...]

    @field_validator("artifact_refs")
    @classmethod
    def require_canonical_refs(
        cls, value: tuple[ArtifactRef, ...]
    ) -> tuple[ArtifactRef, ...]:
        if value != tuple(sorted(value, key=artifact_ref_key)) or len(value) != len(
            set(value)
        ):
            raise ValueError("checkpoint artifact refs must be unique and canonical")
        return value


class ResearchStopInspection(BaseModel):
    """Read-only reconstruction of one exact blocked research run."""

    model_config = STRICT
    schema_version: Literal["research.stop-inspection.v1"]
    run_id: str
    phase: Literal["blocked"]
    stop_code: Literal["RESEARCH_RUN_BLOCKED"]
    blocking_gate_ref: StrictArtifactRef | None = None
    blocking_gate_context_sha256: ResearchSha256 | None = None
    findings: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    review_ref: StrictArtifactRef | None = None
    checkpoints: tuple[
        Annotated[ResearchCheckpointEvidence, BeforeValidator(strict_model_input)], ...
    ]
    research_evidence: tuple[
        Annotated[ResearchFileEvidence, BeforeValidator(strict_model_input)], ...
    ] = Field(min_length=1)

    @model_validator(mode="after")
    def require_complete_canonical_evidence(self) -> ResearchStopInspection:
        if (self.blocking_gate_ref is None) != (
            self.blocking_gate_context_sha256 is None
        ):
            raise ValueError(
                "blocking gate and context evidence must be bound together"
            )
        if self.findings != tuple(sorted(self.findings, key=artifact_ref_key)) or len(
            self.findings
        ) != len(set(self.findings)):
            raise ValueError("stop findings must be unique and canonical")
        checkpoint_keys = tuple(item.node_id for item in self.checkpoints)
        if checkpoint_keys != tuple(sorted(checkpoint_keys)) or len(
            checkpoint_keys
        ) != len(set(checkpoint_keys)):
            raise ValueError("checkpoint evidence must be unique and canonical")
        evidence_keys = tuple(item.relative_path for item in self.research_evidence)
        if evidence_keys != tuple(sorted(evidence_keys)) or len(evidence_keys) != len(
            set(evidence_keys)
        ):
            raise ValueError("research evidence must be unique and canonical")
        return self


__all__ = [
    "ResearchCheckpointEvidence",
    "ResearchFileEvidence",
    "ResearchStopInspection",
]
