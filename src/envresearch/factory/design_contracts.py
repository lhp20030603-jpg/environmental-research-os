"""Strict, timestamp-free contracts for a retrospective V0.2 design handoff."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from envresearch.kernel.gates import GateRequest
from envresearch.kernel.node_checkpoint_schema import NodeCheckpoint
from envresearch.kernel.task_identity import payload_hash
from envresearch.models.artifact import ArtifactRef
from envresearch.models.design_plan import AnalysisPlanPayload
from envresearch.models.enums import GateStatus
from envresearch.research.audit_state import ResearchRunManifest
from envresearch.research.final_binding import terminal_refs
from envresearch.research.gate_context import BoundGateContext

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STRICT = ConfigDict(
    extra="forbid", frozen=True, strict=True, revalidate_instances="always"
)


def _strict_model(model: type[BaseModel]) -> Any:
    """Dump and strictly rebuild nested instances before trusting their fields."""

    def revalidate(value: object) -> BaseModel:
        if isinstance(value, model):
            value = value.model_dump(mode="python")
            return model.model_validate(value, strict=True)
        return model.model_validate(value, strict=False)

    return BeforeValidator(revalidate)


StrictArtifactRef = Annotated[ArtifactRef, _strict_model(ArtifactRef)]
StrictManifest = Annotated[ResearchRunManifest, _strict_model(ResearchRunManifest)]
StrictPlan = Annotated[AnalysisPlanPayload, _strict_model(AnalysisPlanPayload)]
StrictContext = Annotated[BoundGateContext, _strict_model(BoundGateContext)]
StrictGate = Annotated[GateRequest, _strict_model(GateRequest)]
StrictCheckpoint = Annotated[NodeCheckpoint, _strict_model(NodeCheckpoint)]


class ResearchFileEvidence(BaseModel):
    """Authenticated, root-relative evidence for an existing research file."""

    model_config = _STRICT

    relative_path: str
    sha256: str
    size_bytes: StrictInt = Field(ge=0)

    @field_validator("relative_path")
    @classmethod
    def require_safe_relative_path(cls, value: str) -> str:
        path = Path(value)
        if not value or path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("evidence path must be safe relative")
        if path.as_posix() != value:
            raise ValueError("evidence path must use canonical POSIX spelling")
        return value

    @field_validator("sha256")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("evidence digest must be a lowercase SHA-256")
        return value


def approved_design_id(plan_ref: ArtifactRef, context_ref: ArtifactRef) -> str:
    """Derive a stable ID from full exact plan and final-context identities."""
    plan = ArtifactRef.model_validate(plan_ref.model_dump(mode="json"), strict=True)
    context = ArtifactRef.model_validate(
        context_ref.model_dump(mode="json"), strict=True
    )
    return payload_hash(
        {
            "plan_ref": plan.model_dump(mode="json"),
            "final_context_ref": context.model_dump(mode="json"),
        }
    )


class ApprovedDesignHandoff(BaseModel):
    """A fully reconstructable, retrospective V0.2 approved design."""

    model_config = _STRICT

    schema_version: Literal["factory.approved-design.v1"]
    design_id: str
    producer: Literal["research-factory-design-adapter-v1"]
    manifest: StrictManifest
    manifest_evidence: ResearchFileEvidence
    plan_ref: StrictArtifactRef
    plan: StrictPlan
    final_context_ref: StrictArtifactRef
    final_context: StrictContext
    final_gate: StrictGate
    terminal_checkpoint: StrictCheckpoint
    decision_log_evidence: ResearchFileEvidence
    method_profile_sha256: dict[str, str]

    @field_validator("design_id")
    @classmethod
    def require_design_id(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("design ID must be a lowercase SHA-256")
        return value

    @field_validator("method_profile_sha256")
    @classmethod
    def require_method_profile_digests(cls, value: dict[str, str]) -> dict[str, str]:
        if tuple(value) != tuple(sorted(value)):
            raise ValueError("method profile digests must use deterministic key order")
        if not value:
            raise ValueError("method profile digests must not be empty")
        for profile_id, digest in value.items():
            if not profile_id or not _SHA256.fullmatch(digest):
                raise ValueError("method profile digest must be a lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def require_exact_final_binding(self) -> ApprovedDesignHandoff:
        if self.design_id != approved_design_id(self.plan_ref, self.final_context_ref):
            raise ValueError("design ID does not match full exact references")
        context_hash = self.final_context.context_hash
        expected_context_ref = ArtifactRef(
            artifact_id="final-gate-context",
            artifact_version=self.final_context.revision,
            content_hash=context_hash or "",
        )
        if self.final_context_ref != expected_context_ref:
            raise ValueError("final context reference does not match approved context")
        if self.final_context.base_gate_id != "final-gate":
            raise ValueError("handoff requires the final gate context")
        if (
            self.final_gate.id != self.final_context.gate_id
            or self.final_gate.status is not GateStatus.APPROVED
            or self.final_gate.decision is None
            or self.final_gate.decision.conditions.get("gate_context")
            != self.final_context.model_dump(mode="json")
        ):
            raise ValueError("final gate is not paired with its approved context")
        context_refs = self.final_context.artifact_refs
        if len(context_refs) not in (2, 3):
            raise ValueError("final context lacks the reviewed plan reference")
        citation_report = context_refs[2] if len(context_refs) == 3 else None
        if (
            citation_report is not None
            and citation_report.artifact_id != "citation-integrity-report"
        ):
            raise ValueError("final context has an unexpected terminal reference")
        expected_inputs = terminal_refs(
            self.plan_ref,
            context_refs[1],
            context_hash or "",
            self.final_context.revision,
            citation_report,
        )
        expected_hashes = dict(
            sorted(
                (
                    f"{item.artifact_id}@{item.artifact_version}",
                    item.content_hash,
                )
                for item in expected_inputs
            )
        )
        if (
            self.terminal_checkpoint.node_id != "final-approval"
            or self.terminal_checkpoint.input_hashes != expected_hashes
        ):
            raise ValueError(
                "terminal checkpoint inputs do not exactly bind Final Gate"
            )
        if self.method_profile_sha256 != self.manifest.method_profile_sha256:
            raise ValueError("method profile digests differ from the run manifest")
        return self
