"""Durable human approval gates with independent terminal decisions."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self

from filelock import FileLock, Timeout
from pydantic import BaseModel, Field, JsonValue, field_validator, model_validator

from envresearch.kernel.decision_log import normalize_json_object
from envresearch.kernel.events import EventLog, EventRecord, append_event_once
from envresearch.kernel.gate_journal import GateTransitionJournal
from envresearch.models.enums import GateStatus, WorkflowStatus
from envresearch.storage.artifacts import ArtifactStore


def utc_now() -> datetime:
    """Return the canonical timestamp for a newly created gate record."""
    return datetime.now(UTC)


def _require_utc(value: datetime) -> datetime:
    """Reject time values that do not have the canonical UTC representation."""
    if value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
        raise ValueError("timestamps must be UTC-aware")
    return value


def _canonical_actor(value: str) -> str:
    """Normalize a human principal identifier before authorization checks."""
    normalized = value.strip().casefold()
    if not normalized:
        raise ValueError("gate actor must not be blank")
    return normalized


def _require_safe_gate_id(value: str) -> str:
    """Keep a gate identifier confined to one portable filename segment."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise ValueError("gate ID must be a safe filename segment")
    return value


class _GateLock:
    """An OS-managed cross-process advisory lock with a bounded wait.

    Pre-v0.1 directory locks used the ``.lock`` suffix. This implementation
    uses ``.filelock`` so an offline upgrade preserves stale legacy directories
    without blocking; mixed-version concurrent operation is unsupported.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = FileLock(str(path), timeout=10)

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.lock.acquire()
        except Timeout as error:
            raise TimeoutError(f"timed out acquiring gate lock: {self.path.name}") from error
        return self

    def __exit__(self, *_: object) -> None:
        self.lock.release()


class GateDecision(BaseModel):
    """A terminal, independent human decision for a requested gate."""

    status: GateStatus
    decided_by: str
    rationale: str
    conditions: dict[str, JsonValue] = Field(
        default_factory=dict,
        exclude_if=lambda value: not value,
    )
    decided_at: datetime = Field(default_factory=utc_now)

    @field_validator("status")
    @classmethod
    def require_terminal_status(cls, value: GateStatus) -> GateStatus:
        """A gate decision can only approve or reject a request."""
        if value not in (GateStatus.APPROVED, GateStatus.REJECTED):
            raise ValueError("gate decision status must be approved or rejected")
        return value

    @field_validator("decided_by")
    @classmethod
    def require_actor(cls, value: str) -> str:
        """Store only one canonical spelling for a decision maker."""
        return _canonical_actor(value)

    @field_validator("conditions", mode="before")
    @classmethod
    def require_json_conditions(cls, value: object) -> dict[str, JsonValue]:
        """Retain only finite JSON metadata that can be durably replayed."""
        try:
            return normalize_json_object(value, field_name="conditions")
        except TypeError as error:
            raise ValueError(str(error)) from error

    @field_validator("decided_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """Ensure persisted decision timestamps are unambiguous."""
        return _require_utc(value)


class GateRequest(BaseModel):
    """A durable request awaiting one independent human decision."""

    id: str
    name: str
    requested_by: str
    requested_at: datetime = Field(default_factory=utc_now)
    status: GateStatus = GateStatus.PENDING
    decision: GateDecision | None = None

    @field_validator("id")
    @classmethod
    def require_safe_id(cls, value: str) -> str:
        """Reject IDs that could escape the gate artifact namespace."""
        return _require_safe_gate_id(value)

    @field_validator("requested_by")
    @classmethod
    def require_actor(cls, value: str) -> str:
        """Store only one canonical spelling for a requesting actor."""
        return _canonical_actor(value)

    @field_validator("requested_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """Ensure persisted request timestamps are unambiguous."""
        return _require_utc(value)

    @model_validator(mode="after")
    def require_consistent_decision(self) -> "GateRequest":
        """Keep a pending request separate from a terminal decision."""
        if self.status is GateStatus.PENDING and self.decision is not None:
            raise ValueError("pending gate cannot contain a decision")
        if self.status is not GateStatus.PENDING and self.decision is None:
            raise ValueError("decided gate requires a decision")
        if self.decision is not None and self.decision.status is not self.status:
            raise ValueError("gate status must match its decision")
        return self


class GateStore:
    """Store approval gates as immutable request-to-decision artifacts."""

    def __init__(self, artifacts: ArtifactStore, events: EventLog) -> None:
        self.artifacts = artifacts
        self.events = events
        self.transitions = GateTransitionJournal(artifacts, events)

    def request(self, gate: GateRequest) -> None:
        """Persist a new pending gate and record its request event."""
        gate = self._revalidate_gate(gate)
        if gate.status is not GateStatus.PENDING or gate.decision is not None:
            raise ValueError("new gate requests must be pending")
        with self._lock(gate.id):
            relative = self._relative_path(gate.id)
            if (self.artifacts.root / relative).exists():
                if self._load(gate.id) != gate:
                    raise ValueError("gate already exists; create a new GateRequest")
            else:
                self.artifacts.write_json(relative, gate.model_dump(mode="json"))
            self._append_if_missing(self._requested_event(gate))

    def decide(self, gate_id: str, decision: GateDecision) -> GateRequest:
        """Persist one independent terminal decision for a pending gate."""
        decision = self._revalidate_decision(decision)
        with self._lock(gate_id):
            self.transitions.reconcile(gate_id)
            gate = self._load(gate_id)
            self._append_if_missing(self._requested_event(gate))
            if decision.decided_by == gate.requested_by:
                raise ValueError("gate requires an independent decision maker")
            if decision.decided_at < gate.requested_at:
                raise ValueError("decision cannot predate the gate request")
            event = self._decision_event(gate_id, decision)
            if gate.status is not GateStatus.PENDING:
                if gate.decision == decision:
                    self._append_if_missing(event)
                    return gate
                if gate.status is GateStatus.REJECTED:
                    raise ValueError("rejected gate requires a new GateRequest")
                raise ValueError("approved gate cannot be decided again")

            decided = gate.model_copy(
                update={"status": decision.status, "decision": decision}
            )
            payload = self.transitions.publish(
                gate_id,
                gate.model_dump(mode="json"),
                decided.model_dump(mode="json"),
                event,
            )
            return GateRequest.model_validate(payload)

    def require_approved(self, gate_id: str) -> None:
        """Raise unless a durable gate has a recorded approval."""
        with self._lock(gate_id):
            self.transitions.reconcile(gate_id)
            gate = self._load(gate_id)
            if gate.status is not GateStatus.APPROVED:
                raise PermissionError(f"{gate_id} is not approved")
            self._require_exact_event(
                self._requested_event(gate), "matching request event"
            )
            assert gate.decision is not None
            self._require_exact_event(
                self._decision_event(gate_id, gate.decision),
                "matching approval event",
            )

    def _load(self, gate_id: str) -> GateRequest:
        return GateRequest.model_validate(
            self.artifacts.read_json(self._relative_path(gate_id))
        )

    @staticmethod
    def _revalidate_gate(gate: GateRequest) -> GateRequest:
        """Validate model instances again at the persistence boundary."""
        return GateRequest.model_validate(dict(gate.__dict__))

    @staticmethod
    def _revalidate_decision(decision: GateDecision) -> GateDecision:
        """Validate model instances again at the persistence boundary."""
        return GateDecision.model_validate(dict(decision.__dict__))

    def _lock(self, gate_id: str) -> _GateLock:
        _require_safe_gate_id(gate_id)
        return _GateLock(
            self.artifacts.root / "gates" / ".locks" / f"{gate_id}.filelock"
        )

    def _append_if_missing(self, event: EventRecord) -> None:
        append_event_once(self.events, event)

    def _require_exact_event(self, expected: EventRecord, missing: str) -> None:
        matches = [
            event
            for event in self.events.read_all()
            if event.event_id == expected.event_id
        ]
        if not matches:
            raise PermissionError(f"{expected.run_id} has no {missing}")
        if matches != [expected]:
            raise RuntimeError("event identity collision")

    @staticmethod
    def _requested_event(gate: GateRequest) -> EventRecord:
        return EventRecord(
            event_id=f"{gate.id}.requested",
            run_id=gate.id,
            event_type="gate.requested",
            actor=gate.requested_by,
            timestamp=gate.requested_at,
            from_status=WorkflowStatus.PENDING,
            to_status=WorkflowStatus.PENDING,
            payload={"gate_id": gate.id, "name": gate.name},
        )

    @staticmethod
    def _decision_event(gate_id: str, decision: GateDecision) -> EventRecord:
        payload: dict[str, object] = {
            "gate_id": gate_id,
            "rationale": decision.rationale,
        }
        if decision.conditions:
            payload["conditions"] = decision.conditions
        return EventRecord(
            event_id=f"{gate_id}.{decision.status}",
            run_id=gate_id,
            event_type=f"gate.{decision.status}",
            actor=decision.decided_by,
            timestamp=decision.decided_at,
            from_status=WorkflowStatus.PENDING,
            to_status=WorkflowStatus(decision.status),
            payload=payload,
        )

    @staticmethod
    def _relative_path(gate_id: str) -> Path:
        _require_safe_gate_id(gate_id)
        return Path("gates") / f"{gate_id}.json"
