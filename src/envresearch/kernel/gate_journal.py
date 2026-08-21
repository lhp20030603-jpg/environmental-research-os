"""Journaled publication for terminal approval-gate transitions."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from envresearch.kernel.events import EventLog, EventRecord, append_event_once
from envresearch.kernel.task_identity import payload_hash
from envresearch.storage.artifacts import ArtifactStore


class GateTransitionJournal:
    """Publish a gate artifact and its exact audit event idempotently."""

    def __init__(self, artifacts: ArtifactStore, events: EventLog) -> None:
        self.artifacts = artifacts
        self.events = events

    def publish(
        self,
        gate_id: str,
        source_gate: Mapping[str, object],
        target_gate: Mapping[str, object],
        event: EventRecord,
    ) -> dict[str, object]:
        """Journal exact transition contents before publishing the gate artifact."""
        if self._journal_file(gate_id).exists():
            pending = self.reconcile(gate_id)
            assert pending is not None
            return pending
        core: dict[str, object] = {
            "gate_id": gate_id,
            "source_gate": dict(source_gate),
            "target_gate": dict(target_gate),
            "event": event.model_dump(mode="json"),
        }
        self.artifacts.write_json(
            self._journal_path(gate_id),
            {**core, "record_hash": payload_hash(core)},
        )
        return self._apply(gate_id, core)

    def reconcile(self, gate_id: str) -> dict[str, object] | None:
        """Complete a pending gate transition without caller-supplied decision data."""
        if not self._journal_file(gate_id).exists():
            return None
        payload = self.artifacts.read_json(self._journal_path(gate_id))
        expected = {
            "gate_id",
            "source_gate",
            "target_gate",
            "event",
            "record_hash",
        }
        if set(payload) != expected:
            raise RuntimeError("gate transition journal fields changed")
        record_hash = payload.pop("record_hash")
        if not isinstance(record_hash, str) or payload_hash(payload) != record_hash:
            raise RuntimeError("gate transition journal hash mismatch")
        return self._apply(gate_id, payload)

    def _apply(
        self, gate_id: str, payload: Mapping[str, object]
    ) -> dict[str, object]:
        recorded_id = payload.get("gate_id")
        source = payload.get("source_gate")
        target = payload.get("target_gate")
        event_value = payload.get("event")
        if (
            recorded_id != gate_id
            or not isinstance(source, Mapping)
            or not isinstance(target, Mapping)
            or not isinstance(event_value, Mapping)
        ):
            raise RuntimeError("gate transition journal payload is invalid")
        source_payload = dict(source)
        target_payload = dict(target)
        if source_payload.get("id") != gate_id or target_payload.get("id") != gate_id:
            raise RuntimeError("gate transition identity mismatch")
        event = EventRecord.model_validate(event_value)
        if event.run_id != gate_id or event.payload.get("gate_id") != gate_id:
            raise RuntimeError("gate transition event identity mismatch")

        relative = Path("gates") / f"{gate_id}.json"
        current = self.artifacts.read_json(relative)
        if current == source_payload:
            self.artifacts.write_json(relative, target_payload)
        elif current != target_payload:
            raise RuntimeError("gate transition artifact diverged")
        append_event_once(self.events, event)
        self._journal_file(gate_id).unlink()
        return target_payload

    @staticmethod
    def _journal_path(gate_id: str) -> Path:
        return Path("gates") / ".transitions" / f"{gate_id}.json"

    def _journal_file(self, gate_id: str) -> Path:
        return self.artifacts.root / self._journal_path(gate_id)
