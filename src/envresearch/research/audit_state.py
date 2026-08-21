"""Recoverable V0.2 run manifest and artifact-bound research decisions."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from envresearch.kernel.decision_log import DecisionLog, DecisionLogEntry
from envresearch.kernel.gates import GateRequest
from envresearch.methods.registry import MethodProfileRegistry
from envresearch.models.artifact import ArtifactRef
from envresearch.models.design import DesignReviewPayload
from envresearch.models.enums import ArtifactLifecycle, GateStatus
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.gate_context import BoundGateContext
from envresearch.research.run_binding import method_profile_digests
from envresearch.research.workflow import ResearchRunConfig
from envresearch.storage.artifacts import ArtifactStore

if TYPE_CHECKING:
    from envresearch.research.revisions import RevisionIntent

MANIFEST_PATH = Path("research-run-manifest.json")
DECISION_LOG_PATH = Path("decision-log.jsonl")


class ResearchRunManifest(BaseModel):
    """Immutable scientific registry and intake identity for one research run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.1"] = "1.1"
    run_id: str
    input_mode: str
    config_sha256: str | None
    intake_artifact: ArtifactRef
    method_profiles: dict[str, str]
    method_profile_sha256: dict[str, str]


class ResearchAuditState:
    """Create and replay mandatory run-level audit artifacts idempotently."""

    def __init__(
        self,
        workspace: Path,
        lifecycle: ResearchArtifactLifecycle,
    ) -> None:
        self.workspace = workspace
        self.lifecycle = lifecycle
        self.raw = ArtifactStore(workspace)
        self.decisions = DecisionLog(workspace / DECISION_LOG_PATH)

    def initialize(
        self,
        config: ResearchRunConfig,
        intake_ref: ArtifactRef,
        registry: MethodProfileRegistry,
    ) -> ResearchRunManifest:
        """Publish or verify the immutable run manifest and readable ledger."""
        expected = ResearchRunManifest(
            run_id=config.run_id,
            input_mode=config.input_mode.value,
            config_sha256=config.config_sha256,
            intake_artifact=intake_ref,
            method_profiles={
                profile_id: profile.version
                for profile_id, profile in registry.profiles.items()
            },
            method_profile_sha256=method_profile_digests(registry),
        )
        path = self.workspace / MANIFEST_PATH
        if path.exists():
            try:
                actual = ResearchRunManifest.model_validate(
                    self.raw.read_json(MANIFEST_PATH)
                )
            except (OSError, TypeError, ValueError) as error:
                raise ValueError("research run manifest is corrupt") from error
            if actual != expected:
                raise ValueError("research run manifest does not match this run")
        else:
            self.raw.write_json(MANIFEST_PATH, expected.model_dump(mode="json"))
        self._ensure_decision_log()
        return expected

    def verify_method_profiles(self, registry: MethodProfileRegistry) -> None:
        """Reject resumed or final runs whose loaded scientific rules changed."""
        try:
            manifest = ResearchRunManifest.model_validate(
                self.raw.read_json(MANIFEST_PATH)
            )
        except (OSError, TypeError, ValueError) as error:
            raise ValueError("research run manifest is corrupt") from error
        if manifest.method_profile_sha256 != method_profile_digests(registry):
            raise ValueError("research run method profile content changed")

    def close(self) -> None:
        """Release descriptor-pinned audit state."""
        self.decisions.close()

    def sync(self) -> None:
        """Replay every durable gate, disagreement, risk, and terminal decision."""
        self.decisions.read_all()
        self._sync_gates()
        self._sync_review()
        self._sync_terminal()

    def record_revision(self, intent: RevisionIntent) -> None:
        """Append the explicit human/producer revision request exactly once."""
        self.decisions.append(
            DecisionLogEntry(
                event_id=f"revision-request:{intent.revision_id}",
                timestamp=intent.created_at,
                actor=intent.actor,
                decision_kind="revision_request",
                status="requested",
                subject=intent.node_id,
                reason=intent.reason,
                metadata={
                    "revision_id": intent.revision_id,
                    "generation": intent.generation,
                    "affected_nodes": list(intent.affected_nodes),
                    "target_artifacts": [
                        item.model_dump(mode="json") for item in intent.target_artifacts
                    ],
                },
            )
        )

    def _ensure_decision_log(self) -> None:
        self.decisions.ensure()

    def _sync_gates(self) -> None:
        root = self.workspace / "gate-contexts"
        if not root.exists():
            return
        for context_path in sorted(root.glob("*/*.json")):
            context = BoundGateContext.model_validate_json(context_path.read_bytes())
            gate_path = self.workspace / "gates" / f"{context.gate_id}.json"
            if not gate_path.exists():
                continue
            gate = GateRequest.model_validate_json(gate_path.read_bytes())
            metadata: dict[str, JsonValue] = {
                "base_gate_id": context.base_gate_id,
                "context_hash": context.context_hash,
                "revision": context.revision,
                "artifact_refs": [
                    ref.model_dump(mode="json") for ref in context.artifact_refs
                ],
            }
            if context.revision > 1:
                self._append_revision(
                    context=context,
                    timestamp=gate.requested_at,
                    actor=gate.requested_by,
                    status="requested",
                    reason="A superseding artifact context requires renewed review.",
                    metadata=metadata,
                )
            self.decisions.append(
                DecisionLogEntry(
                    event_id=f"gate:{context.gate_id}:requested:{context.context_hash}",
                    timestamp=gate.requested_at,
                    actor=gate.requested_by,
                    decision_kind="gate_request",
                    status="pending",
                    subject=context.gate_id,
                    reason="Human review requested for exact artifact context.",
                    metadata=metadata,
                )
            )
            if gate.decision is None:
                continue
            decision = gate.decision
            self.decisions.append(
                DecisionLogEntry(
                    event_id=(
                        f"gate:{context.gate_id}:{decision.status.value}:"
                        f"{context.context_hash}"
                    ),
                    timestamp=decision.decided_at,
                    actor=decision.decided_by,
                    decision_kind="gate_decision",
                    status=decision.status.value,
                    subject=context.gate_id,
                    reason=decision.rationale,
                    metadata=metadata,
                )
            )
            if decision.status is GateStatus.REJECTED:
                self._append_revision(
                    context=context,
                    timestamp=decision.decided_at,
                    actor=decision.decided_by,
                    status="rejected",
                    reason=decision.rationale,
                    metadata=metadata,
                )
            self._sync_risk_acceptance(context, gate, metadata)

    def _append_revision(
        self,
        *,
        context: BoundGateContext,
        timestamp: datetime,
        actor: str,
        status: str,
        reason: str,
        metadata: Mapping[str, JsonValue],
    ) -> None:
        self.decisions.append(
            DecisionLogEntry(
                event_id=(
                    f"revision:{context.gate_id}:{status}:{context.context_hash}"
                ),
                timestamp=timestamp,
                actor=actor,
                decision_kind="revision_request",
                status=status,
                subject=context.base_gate_id,
                reason=reason,
                metadata=dict(metadata),
            )
        )

    def _sync_risk_acceptance(
        self,
        context: BoundGateContext,
        gate: GateRequest,
        metadata: Mapping[str, JsonValue],
    ) -> None:
        decision = gate.decision
        if decision is None or decision.status is not GateStatus.APPROVED:
            return
        accepted = decision.conditions.get("accepted_major_ids", [])
        risk_ids = accepted if isinstance(accepted, list) else []
        if context.base_gate_id == "data-gate" and not risk_ids:
            risk_ids = ["conditional-data-risk"]
        for risk_id in risk_ids:
            if not isinstance(risk_id, str):
                continue
            self.decisions.append(
                DecisionLogEntry(
                    event_id=f"risk:{context.gate_id}:{risk_id}:{context.context_hash}",
                    timestamp=decision.decided_at,
                    actor=decision.decided_by,
                    decision_kind="risk_acceptance",
                    status="accepted",
                    subject=risk_id,
                    reason=decision.rationale,
                    metadata=dict(metadata),
                )
            )

    def _sync_review(self) -> None:
        path = Path("artifacts/design-review-findings.json")
        if not (self.workspace / path).exists():
            return
        artifact = self.lifecycle.read_artifact(path)
        review = DesignReviewPayload.model_validate(artifact.payload)
        ref = self.lifecycle.artifact_ref(path)
        for finding in review.findings:
            self.decisions.append(
                DecisionLogEntry(
                    event_id=f"finding:{ref.content_hash}:{finding.finding_id}",
                    timestamp=artifact.envelope.created_at,
                    actor=artifact.envelope.producer.component,
                    decision_kind="agent_disagreement",
                    status="resolved" if finding.resolved else "open",
                    subject=finding.finding_id,
                    reason=finding.finding,
                    metadata={
                        "artifact_ref": ref.model_dump(mode="json"),
                        "severity": finding.severity.value,
                        "evidence_refs": list(finding.evidence_refs),
                    },
                )
            )

    def _sync_terminal(self) -> None:
        path = Path("artifacts/analysis-plan.yaml")
        if not (self.workspace / path).exists():
            return
        artifact = self.lifecycle.read_artifact(path)
        if artifact.envelope.validation_status is not ArtifactLifecycle.APPROVED:
            return
        ref = self.lifecycle.artifact_ref(path)
        contexts = sorted(
            (self.workspace / "gate-contexts" / "final-gate").glob("*.json")
        )
        if not contexts:
            return
        context = BoundGateContext.model_validate_json(contexts[-1].read_bytes())
        if (
            artifact.envelope.provenance.get("gate_context_hash")
            != context.context_hash
        ):
            raise ValueError("terminal approval does not match active gate context")
        gate_path = self.workspace / "gates" / f"{context.gate_id}.json"
        gate = GateRequest.model_validate_json(gate_path.read_bytes())
        if gate.decision is None or gate.status is not GateStatus.APPROVED:
            return
        self.decisions.append(
            DecisionLogEntry(
                event_id=f"terminal:{ref.content_hash}:{context.gate_id}",
                timestamp=gate.decision.decided_at,
                actor=gate.decision.decided_by,
                decision_kind="terminal_approval",
                status="approved",
                subject=ref.artifact_id,
                reason=gate.decision.rationale,
                metadata={"artifact_ref": ref.model_dump(mode="json")},
            )
        )
