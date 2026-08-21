"""Read-only validation of a completed Final Gate transition."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import TypeAdapter

from envresearch.kernel.artifact_graph import ArtifactNode
from envresearch.kernel.events import EventRecord
from envresearch.kernel.gates import GateRequest
from envresearch.kernel.node_checkpoint_schema import NodeCheckpoint
from envresearch.kernel.node_checkpoints import NodeCheckpointStore
from envresearch.models.artifact import ArtifactRef, ResearchArtifact, verify_artifact
from envresearch.models.design_plan import AnalysisPlanPayload
from envresearch.models.enums import ArtifactLifecycle, GateStatus, WorkflowStatus
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.artifact_lifecycle_support import artifact_ref
from envresearch.research.audit_state import ResearchAuditState
from envresearch.research.final_binding import terminal_refs
from envresearch.research.gate_context import BoundGateContext, BoundGateManager
from envresearch.research.semantic_validation import SemanticSubmissionValidator
from envresearch.workers.filesystem import PinnedRoot


@dataclass(frozen=True)
class FinalApprovalState:
    """Read-only reconstruction of one exact, already-approved Final Gate."""

    plan_ref: ArtifactRef
    plan: AnalysisPlanPayload
    context_ref: ArtifactRef
    context: BoundGateContext
    gate: GateRequest
    checkpoint: NodeCheckpoint
    terminal_inputs: tuple[ArtifactRef, ...]


def reopen_complete_final_exact(
    *,
    lifecycle: ResearchArtifactLifecycle,
    gates: BoundGateManager,
    checkpoints: NodeCheckpointStore,
    nodes: Mapping[str, ArtifactNode],
    semantics: SemanticSubmissionValidator,
    plan_ref: ArtifactRef,
    context_ref: ArtifactRef,
    audit: ResearchAuditState | None = None,
) -> FinalApprovalState:
    """Reconstruct a supplied Final Gate identity without promoting or publishing."""
    try:
        supplied_plan = ArtifactRef.model_validate(plan_ref.model_dump(mode="json"))
        supplied_context = ArtifactRef.model_validate(
            context_ref.model_dump(mode="json")
        )
        if audit is not None:
            audit.verify_method_profiles(semantics.registry)
        semantics.validate_final()
        plan_artifact = _read_artifact(lifecycle.workspace, Path("artifacts/analysis-plan.yaml"))
        current_plan_ref = artifact_ref(plan_artifact.envelope)
        if current_plan_ref != supplied_plan:
            raise ValueError("approved plan reference is not current")
        if plan_artifact.envelope.validation_status is not ArtifactLifecycle.APPROVED:
            raise ValueError("analysis plan is not approved")
        plan = AnalysisPlanPayload.model_validate(plan_artifact.payload)
        context = _read_context(lifecycle.workspace, supplied_context)
        if context.context_hash is None:
            raise ValueError("final gate context is incomplete")
        expected_context_ref = ArtifactRef(
            artifact_id="final-gate-context",
            artifact_version=context.revision,
            content_hash=context.context_hash,
        )
        if supplied_context != expected_context_ref:
            raise ValueError("supplied final context reference is not current")
        refs = _gate_refs(lifecycle.workspace, context, nodes)
        if context.artifact_refs != refs:
            raise ValueError("final gate approval does not match its exact context")
        gate = GateRequest.model_validate_json(
            _read_file(lifecycle.workspace, Path("gates") / f"{context.gate_id}.json")
        )
        if (
            gate.id != context.gate_id
            or gate.status is not GateStatus.APPROVED
            or gate.decision is None
            or gate.decision.conditions.get("gate_context")
            != context.model_dump(mode="json")
        ):
            raise ValueError("final gate request is not an exact approval")
        _require_approval_events(lifecycle.workspace, gate)
        reviewed_ref = context.artifact_refs[1]
        terminal_inputs = terminal_refs(
            supplied_plan,
            reviewed_ref,
            context.context_hash,
            context.revision,
            (
                artifact_ref(
                    _read_artifact(
                        lifecycle.workspace,
                        Path("artifacts/citation-integrity-report.json"),
                    ).envelope
                )
                if "validate-citations" in nodes
                else None
            ),
        )
        if not checkpoints.verify(nodes["final-approval"], terminal_inputs):
            raise ValueError("terminal checkpoint binding mismatch")
        checkpoint = NodeCheckpoint.model_validate_json(
            _read_file(lifecycle.workspace, Path("node-checkpoints/final-approval.json")),
            strict=True,
        )
        return FinalApprovalState(
            plan_ref=supplied_plan,
            plan=plan,
            context_ref=supplied_context,
            context=context,
            gate=gate,
            checkpoint=checkpoint,
            terminal_inputs=terminal_inputs,
        )
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ValueError("completed final approval is stale or corrupt") from error


def _read_file(workspace: Path, relative: Path) -> bytes:
    root = PinnedRoot(workspace, create=False)
    try:
        return root.read_file(relative, description="final approval source")
    finally:
        root.close()


def _read_artifact(workspace: Path, relative: Path) -> ResearchArtifact[object]:
    data = _read_file(workspace, relative)
    value = yaml.safe_load(data) if relative.suffix == ".yaml" else json.loads(data)
    canonical = (
        str(yaml.safe_dump(value, sort_keys=True, allow_unicode=True)).encode("utf-8")
        if relative.suffix == ".yaml"
        else json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
    )
    if data != canonical:
        raise ValueError("final approval artifact bytes are not canonical")
    artifact = TypeAdapter(ResearchArtifact[object]).validate_python(value)
    verify_artifact(artifact)
    return artifact


def _read_context(workspace: Path, reference: ArtifactRef) -> BoundGateContext:
    if reference.artifact_id != "final-gate-context":
        raise ValueError("supplied reference is not a final gate context")
    path = Path("gate-contexts/final-gate") / f"{reference.artifact_version:04d}.json"
    context = BoundGateContext.model_validate_json(_read_file(workspace, path))
    if context.context_hash != reference.content_hash:
        raise ValueError("supplied final context reference is not current")
    if _exists(
        workspace,
        Path("gate-contexts/final-gate/superseded") / f"{context.gate_id}.json",
    ):
        raise ValueError("supplied final context has been superseded")
    return context


def _exists(workspace: Path, relative: Path) -> bool:
    root = PinnedRoot(workspace, create=False)
    try:
        return root.exists(relative)
    finally:
        root.close()


def _gate_refs(
    workspace: Path, context: BoundGateContext, nodes: Mapping[str, ArtifactNode]
) -> tuple[ArtifactRef, ...]:
    reviewed = context.artifact_refs[1]
    refs = (
        artifact_ref(_read_artifact(workspace, Path("artifacts/design-review-findings.json")).envelope),
        artifact_ref(
            _read_artifact(
                workspace,
                Path("artifacts/.versions/analysis-plan.yaml")
                / f"{reviewed.artifact_version:04d}.json",
            ).envelope
        ),
    )
    if "validate-citations" not in nodes:
        return refs
    citation = _read_artifact(
        workspace, Path("artifacts/citation-integrity-report.json")
    )
    return (*refs, artifact_ref(citation.envelope))


def _require_approval_events(workspace: Path, gate: GateRequest) -> None:
    if gate.decision is None or gate.decision.decided_by == gate.requested_by:
        raise ValueError("final gate lacks an independent approval")
    expected = (
        EventRecord(
            event_id=f"{gate.id}.requested",
            run_id=gate.id,
            event_type="gate.requested",
            actor=gate.requested_by,
            timestamp=gate.requested_at,
            from_status=WorkflowStatus.PENDING,
            to_status=WorkflowStatus.PENDING,
            payload={"gate_id": gate.id, "name": gate.name},
        ),
        EventRecord(
            event_id=f"{gate.id}.{gate.decision.status}",
            run_id=gate.id,
            event_type=f"gate.{gate.decision.status}",
            actor=gate.decision.decided_by,
            timestamp=gate.decision.decided_at,
            from_status=WorkflowStatus.PENDING,
            to_status=WorkflowStatus(gate.decision.status),
            payload={
                "gate_id": gate.id,
                "rationale": gate.decision.rationale,
                **(
                    {"conditions": gate.decision.conditions}
                    if gate.decision.conditions
                    else {}
                ),
            },
        ),
    )
    events = tuple(
        EventRecord.model_validate(json.loads(line))
        for line in _read_file(workspace, Path("events.jsonl")).splitlines()
    )
    for event in expected:
        if tuple(item for item in events if item.event_id == event.event_id) != (event,):
            raise ValueError("final gate event history is missing or inconsistent")


def require_complete_final(
    *,
    lifecycle: ResearchArtifactLifecycle,
    gates: BoundGateManager,
    checkpoints: NodeCheckpointStore,
    nodes: Mapping[str, ArtifactNode],
    semantics: SemanticSubmissionValidator,
    audit: ResearchAuditState | None = None,
) -> None:
    try:
        if audit is not None:
            audit.verify_method_profiles(semantics.registry)
        semantics.validate_final()
        refs = gates.artifact_refs("final-gate", lifecycle, nodes)
        context = gates.require_approved("final-gate", "Research design", refs)
        if context is None or context.context_hash is None:
            raise ValueError("final gate is not currently approved")
        compose = nodes["compose-plan"]
        lifecycle.promote_status(
            Path("artifacts/analysis-plan.yaml"),
            ArtifactLifecycle.APPROVED,
            "human-final-gate",
            predecessor_ref=context.artifact_refs[1],
            predecessor_component=lifecycle.read_history(
                Path("artifacts/analysis-plan.yaml"),
                context.artifact_refs[1].artifact_version,
            ).envelope.producer,
            expected_inputs=lifecycle.input_refs(compose),
            gate_context_hash=context.context_hash,
        )
        inputs = terminal_refs(
            lifecycle.artifact_ref(Path("artifacts/analysis-plan.yaml")),
            context.artifact_refs[1],
            context.context_hash,
            context.revision,
            (
                lifecycle.artifact_ref(Path("artifacts/citation-integrity-report.json"))
                if "validate-citations" in nodes
                else None
            ),
        )
        if not checkpoints.verify(nodes["final-approval"], inputs):
            raise ValueError("terminal checkpoint binding mismatch")
    except (OSError, ValueError, FileExistsError) as error:
        raise ValueError("completed final approval is stale or corrupt") from error
