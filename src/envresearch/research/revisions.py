"""Append-only revision intents and crash-resumable local recomputation."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from envresearch.kernel.artifact_graph import ArtifactGraph, ArtifactNode
from envresearch.kernel.node_checkpoints import NodeCheckpointStore
from envresearch.models.enums import ArtifactLifecycle
from envresearch.models.principal import PrincipalAssignment
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.audit_state import ResearchAuditState
from envresearch.research.gate_context import BoundGateManager
from envresearch.research.principal_registry import PrincipalRegistry
from envresearch.research.revision_intents import ProtectedRevisionIntents
from envresearch.research.revision_models import (
    RevisionArtifact,
    RevisionGateTarget,
    RevisionIntent,
)
from envresearch.research.revision_recovery import require_recorded_side_effects
from envresearch.research.revision_scope import live_revision_scope
from envresearch.storage.secure_journal import SecureJournal
from envresearch.workers.queue import FilesystemWorkerQueue

__all__ = [
    "RevisionArtifact",
    "RevisionGateTarget",
    "RevisionIntent",
    "RevisionTransaction",
]


class RevisionTransaction:
    """Persist intent first, then idempotently resume every revision boundary."""

    def __init__(
        self,
        *,
        workspace: Path,
        run_id: str,
        graph: ArtifactGraph,
        nodes: dict[str, ArtifactNode],
        lifecycle: ResearchArtifactLifecycle,
        checkpoints: NodeCheckpointStore,
        queue: FilesystemWorkerQueue,
        audit: ResearchAuditState,
        gates: BoundGateManager,
    ) -> None:
        self.workspace = workspace
        self.run_id = run_id
        self.graph = graph
        self.nodes = nodes
        self.lifecycle = lifecycle
        self.checkpoints = checkpoints
        self.queue = queue
        self.audit = audit
        self.gates = gates
        self.journal = SecureJournal(
            workspace / "revisions/journal.jsonl",
            storage_root=workspace,
            control_root=queue.control.path,
        )
        self.intent_store = ProtectedRevisionIntents(queue)

    def close(self) -> None:
        """Release descriptor-pinned revision journal state."""
        self.journal.close()

    def request(
        self,
        node_id: str,
        *,
        reason: str,
        actor: str,
        principal: PrincipalAssignment,
    ) -> RevisionIntent:
        reason = _required(reason, "revision reason")
        _required(actor, "revision actor attestation")
        trusted = PrincipalRegistry(self.queue.control, self.run_id).human_revision()
        if principal != trusted:
            raise ValueError("revision lacks the authenticated revision principal")
        actor = principal.principal_id
        if node_id not in self.nodes or self.nodes[node_id].worker_role is None:
            raise ValueError("revision target must be a worker node")
        intent = self._select_or_create(node_id, reason, actor, principal)
        self._resume(intent)
        return intent

    def recover_pending(self) -> tuple[RevisionIntent, ...]:
        """Resume every durable intent before ordinary ready-order issuance."""
        intents = tuple(
            sorted(
                self._all_intents(),
                key=lambda item: (item.created_at, item.revision_id),
            )
        )
        pending = tuple(
            item for item in intents if not self._has_event(item, "revision_completed")
        )
        for intent in intents:
            self._require_recorded_side_effects(intent)
        if not pending:
            return ()
        for intent in pending:
            self._resume(intent)
        return pending

    def _select_or_create(
        self,
        node_id: str,
        reason: str,
        actor: str,
        principal: PrincipalAssignment,
    ) -> RevisionIntent:
        prior = self._intents_for(node_id)
        if prior and self._is_still_revising(prior[-1]):
            if prior[-1].reason != reason or prior[-1].actor != actor:
                raise RuntimeError("conflicting revision is already active")
            return prior[-1]
        completed = self.checkpoints.completed_nodes(self.graph)
        if node_id not in completed:
            raise ValueError("revision target has no completed checkpoint")
        generation = 1 if not prior else prior[-1].generation + 1
        checkpoint_nodes = tuple(sorted(self.graph.invalidate(node_id, completed)))
        affected = live_revision_scope(self, node_id, completed)
        worker_nodes = tuple(
            candidate
            for candidate in affected
            if self.nodes[candidate].worker_role is not None
            and self.queue.has_generation(candidate)
        )
        artifacts = tuple(
            RevisionArtifact(path=path, ref=self.lifecycle.artifact_ref(path))
            for affected_id in affected
            for path in self.nodes[affected_id].output_paths
            if (self.workspace / path).exists()
            and path.suffix in {".json", ".yaml", ".csv", ".md"}
            and not path.name.endswith(".meta.json")
        )
        if not artifacts:
            raise ValueError("revision target has no versioned authoritative artifact")
        gate_targets = self._gate_targets(affected)
        identity = json.dumps(
            {
                "run_id": self.run_id,
                "generation": generation,
                "node_id": node_id,
                "actor": actor,
                "principal_assignment": principal.model_dump(mode="json"),
                "reason": reason,
                "refs": [item.model_dump(mode="json") for item in artifacts],
                "checkpoint_nodes": checkpoint_nodes,
                "worker_nodes": worker_nodes,
                "gate_targets": [item.model_dump(mode="json") for item in gate_targets],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        revision_id = f"rev-{hashlib.sha256(identity).hexdigest()[:20]}"
        intent = RevisionIntent(
            revision_id=revision_id,
            run_id=self.run_id,
            generation=generation,
            node_id=node_id,
            actor=actor,
            principal_assignment=principal,
            reason=reason,
            target_artifacts=artifacts,
            affected_nodes=affected,
            checkpoint_nodes=checkpoint_nodes,
            worker_nodes=worker_nodes,
            gate_targets=gate_targets,
            created_at=datetime.now(UTC),
        )
        self._persist_intent(intent)
        self._append(intent, "revision_intent")
        return intent

    def _resume(self, intent: RevisionIntent) -> None:
        self._require_recorded_side_effects(intent)
        self._append(intent, "revision_intent")
        if not self._has_event(intent, "worker_namespace_archived"):
            for node_id in intent.worker_nodes:
                self.queue.archive_generation(
                    node_id,
                    intent.revision_id,
                    allow_cancellation=node_id not in intent.checkpoint_nodes,
                )
            self._append(intent, "worker_namespace_archived")
        self._require_target_refs(intent)
        if not self._has_event(intent, "checkpoints_invalidated"):
            invalidated = self.checkpoints.invalidate(
                self.graph,
                intent.node_id,
                reason=f"revision {intent.revision_id}: {intent.reason}",
            )
            if invalidated != frozenset(intent.checkpoint_nodes):
                raise ValueError("revision invalidated an unexpected checkpoint set")
            self._append(intent, "checkpoints_invalidated")
        if not self._has_event(intent, "artifacts_superseded"):
            for target in intent.target_artifacts:
                self.lifecycle.supersede(
                    target.path,
                    revision_id=intent.revision_id,
                    reason=intent.reason,
                    actor=intent.actor,
                )
            self._append(intent, "artifacts_superseded")
        if not self._has_event(intent, "gates_superseded"):
            for gate_target in intent.gate_targets:
                self.gates.supersede_for_revision(
                    gate_target.base_gate_id,
                    gate_id=gate_target.gate_id,
                    context_hash=gate_target.context_hash,
                    revision_id=intent.revision_id,
                    actor=intent.actor,
                    reason=intent.reason,
                )
            self._append(intent, "gates_superseded")
        if not self._has_event(intent, "revision_completed"):
            self.audit.record_revision(intent)
            self._append(intent, "revision_completed")

    def _require_target_refs(self, intent: RevisionIntent) -> None:
        """CAS every captured artifact before the first supersession mutation."""
        for target in intent.target_artifacts:
            envelope = self.lifecycle.current_envelope(target.path)
            if (
                envelope.validation_status is ArtifactLifecycle.SUPERSEDED
                and envelope.provenance.get("revision_id") == intent.revision_id
            ):
                continue
            if self.lifecycle.artifact_ref(target.path) != target.ref:
                raise RuntimeError("revision target artifact changed after intent")

    def _persist_intent(self, intent: RevisionIntent) -> None:
        self.intent_store.persist(intent)

    def _append(self, intent: RevisionIntent, event: str) -> None:
        entries = self._read_journal()
        same = [
            item
            for item in entries
            if item.get("revision_id") == intent.revision_id
            and item.get("event") == event
        ]
        payload = {
            "event": event,
            "revision_id": intent.revision_id,
            "node_id": intent.node_id,
            "actor": intent.actor,
            "reason": intent.reason,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "intent_sha256": hashlib.sha256(
                _canonical(intent.model_dump(mode="json"))
            ).hexdigest(),
        }
        if same:
            return
        self.journal.append(payload)

    def _read_journal(self) -> list[dict[str, object]]:
        entries = self.journal.read_all()
        intents = {item.revision_id: item for item in self._all_intents()}
        order = {
            event: index
            for index, event in enumerate(
                (
                    "revision_intent",
                    "worker_namespace_archived",
                    "checkpoints_invalidated",
                    "artifacts_superseded",
                    "gates_superseded",
                    "revision_completed",
                )
            )
        }
        progress: dict[str, int] = {}
        for value in entries:
            revision_id = value.get("revision_id")
            event = value.get("event")
            if not isinstance(revision_id, str) or not isinstance(event, str):
                raise TypeError("revision journal record identity is invalid")
            if event not in order or order[event] <= progress.get(revision_id, -1):
                raise ValueError("revision journal event order is invalid")
            if not isinstance(value.get("intent_sha256"), str):
                raise TypeError("revision journal intent digest is missing")
            intent = intents.get(revision_id)
            if (
                intent is None
                or value["intent_sha256"]
                != hashlib.sha256(
                    _canonical(intent.model_dump(mode="json"))
                ).hexdigest()
            ):
                raise ValueError("revision journal intent digest mismatch")
            progress[revision_id] = order[event]
        return entries

    def _has_event(self, intent: RevisionIntent, event: str) -> bool:
        return any(
            item.get("revision_id") == intent.revision_id and item.get("event") == event
            for item in self._read_journal()
        )

    def _intents_for(self, node_id: str) -> list[RevisionIntent]:
        intents = self._all_intents()
        return sorted(
            (item for item in intents if item.node_id == node_id),
            key=lambda item: item.generation,
        )

    def _all_intents(self) -> list[RevisionIntent]:
        """Load protected intents and their verified public projections."""
        self.journal.verify_roots()
        return self.intent_store.all()

    def _require_recorded_side_effects(self, intent: RevisionIntent) -> None:
        events = {
            str(item["event"])
            for item in self._read_journal()
            if item.get("revision_id") == intent.revision_id
        }
        require_recorded_side_effects(
            intent,
            events,
            nodes=self.nodes,
            queue=self.queue,
            lifecycle=self.lifecycle,
            gates=self.gates,
            audit=self.audit,
            checkpoints=self.checkpoints,
        )

    def _is_still_revising(self, intent: RevisionIntent) -> bool:
        path = self.nodes[intent.node_id].output_paths[0]
        current = self.lifecycle.current_envelope(path)
        return (
            current.validation_status is ArtifactLifecycle.SUPERSEDED
            and current.provenance.get("revision_id") == intent.revision_id
        ) or not any(
            item.get("revision_id") == intent.revision_id
            and item.get("event") == "revision_completed"
            for item in self._read_journal()
        )

    def _gate_targets(
        self, affected_nodes: tuple[str, ...]
    ) -> tuple[RevisionGateTarget, ...]:
        targets: list[RevisionGateTarget] = []
        for base_gate_id in self._affected_gates(affected_nodes):
            context = self.gates.active_context(base_gate_id)
            targets.append(
                RevisionGateTarget(
                    base_gate_id=base_gate_id,
                    gate_id=None if context is None else context.gate_id,
                    context_hash=None if context is None else context.context_hash,
                )
            )
        return tuple(targets)

    @staticmethod
    def _affected_gates(affected_nodes: tuple[str, ...]) -> tuple[str, ...]:
        affected = set(affected_nodes)
        gates: list[str] = []
        if affected.intersection(
            {"frame-charters", "normalize-brief", "approve-charter"}
        ):
            gates.append("gate-1")
        if "inspect-data" in affected:
            gates.append("data-gate")
        if affected.intersection({"review-design", "compose-plan", "final-approval"}):
            gates.append("final-gate")
        return tuple(gates)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()


def _required(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be blank")
    return normalized
