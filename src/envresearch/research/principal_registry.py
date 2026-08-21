"""Owner-controlled principal assignments for research workflow actions."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from envresearch.benchmarks.blind_authority import HumanKeyEnrollment
from envresearch.kernel.gates import GateRequest
from envresearch.models.artifact import ProducerIdentity
from envresearch.models.principal import (
    PrincipalAssignment,
    PrincipalKind,
    PrincipalVerification,
)

_BENCHMARK_ID_CHARACTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
_BENCHMARK_ID_INITIAL = _BENCHMARK_ID_CHARACTERS[:52] + "0123456789"

if TYPE_CHECKING:
    from envresearch.workers.control import QueueControl


class _ProtectedPrincipal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    assignment: PrincipalAssignment
    mac: str


class _ProtectedGateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    gate_id: str
    decision_sha256: str
    mac: str


class PrincipalRegistry:
    """Assign principals in owner-only queue control state."""

    def __init__(self, control: QueueControl, run_id: str) -> None:
        self.control = control
        self.run_id = run_id
        self.control.storage.ensure_directory(Path("principals"))
        self.control.storage.ensure_directory(Path("principals/gates"))
        for kind in (PrincipalKind.GATE, PrincipalKind.REVISION):
            self._ensure_capability(kind)

    def benchmark_worker(
        self, case_id: str, kind: PrincipalKind, generation: int
    ) -> PrincipalAssignment:
        _require_benchmark_kind(kind, human=False)
        _require_benchmark_identity(case_id, "benchmark case ID")
        _require_benchmark_slot(generation, "benchmark generation")
        assignment = self._benchmark_assignment(case_id, kind, generation, human=False)
        path = self._benchmark_path(case_id) / f"{kind.value}-g{generation}.json"
        return self._persist_or_authenticate(assignment, path)

    def require_benchmark_role(
        self, supplied: PrincipalAssignment, kind: PrincipalKind
    ) -> PrincipalAssignment:
        _require_benchmark_kind(kind, human=False)
        if supplied.kind != kind:
            raise ValueError("principal role mismatch")
        case_id = _assignment_case_id(supplied, kind)
        directory = self._benchmark_path(case_id)
        try:
            names = self.control.storage.list_directory(directory)
        except FileNotFoundError as error:
            raise ValueError("principal assignment authentication failed") from error
        for name in names:
            generation = _benchmark_generation(name, kind)
            if generation is None:
                continue
            durable = self._read_benchmark_assignment(directory / name)
            expected = self._benchmark_assignment(case_id, kind, generation, human=False)
            if durable != expected:
                raise ValueError("principal assignment authentication failed")
            if durable == supplied:
                return durable
        raise ValueError("principal assignment authentication failed")

    def enroll_benchmark_humans(
        self, case_id: str, participants: tuple[HumanKeyEnrollment, ...]
    ) -> None:
        """Persist public commitments only; no human signing secret enters control."""
        from envresearch.research.principal_enrollment import enroll_humans
        enroll_humans(self, case_id, participants)

    def benchmark_human(
        self, case_id: str, kind: PrincipalKind, slot: int, generation: int
    ) -> PrincipalAssignment:
        from envresearch.research.principal_enrollment import benchmark_human
        return benchmark_human(self, case_id, kind, slot, generation)

    def enrolled_human_key(
        self, case_id: str, kind: PrincipalKind, slot: int
    ) -> HumanKeyEnrollment:
        from envresearch.research.principal_enrollment import enrolled_human_key
        return enrolled_human_key(self, case_id, kind, slot)

    def worker(self, node_id: str, role: str, node_version: str) -> PrincipalAssignment:
        """Return one deterministic assignment to seal into a work order."""
        kind = PrincipalKind.CRITIC if "critic" in role else PrincipalKind.WORKER
        principal = f"principal-{node_id}"
        context = _digest_token(self.run_id, node_id, node_version, length=24)
        assignment = PrincipalAssignment(
            assignment_id=f"assignment-{_digest_token(principal, context)}",
            principal_id=principal,
            kind=kind,
            producer=ProducerIdentity(
                component=f"assigned-{role}",
                version="0.2.0",
                runtime="owner-control",
                context_id=f"context-{context}",
            ),
            verification=PrincipalVerification.OWNER_CONTROL,
        )
        return assignment

    def human(self, kind: PrincipalKind) -> PrincipalAssignment:
        """Create or authenticate the fixed human control principal."""
        from envresearch.research.principal_authentication import human_assignment

        return self._persist_or_authenticate(human_assignment(kind))

    def human_revision(self) -> PrincipalAssignment:
        return self.human(PrincipalKind.REVISION)

    def require_capability(
        self, kind: PrincipalKind, supplied: str
    ) -> PrincipalAssignment:
        """Authenticate an explicit bearer token for one human control action."""
        if kind not in {PrincipalKind.GATE, PrincipalKind.REVISION}:
            raise ValueError("capability kind must be gate or revision")
        expected = self.control.storage.read_file(
            self._capability_path(kind),
            description=f"{kind.value} principal capability",
            required_mode=0o600,
            required_owner=os.getuid(),
        )
        candidate = supplied.strip().encode()
        if not candidate or not hmac.compare_digest(candidate, expected):
            raise ValueError(f"{kind.value} principal capability is invalid")
        return self.human(kind)

    def require_existing_capability(
        self, kind: PrincipalKind, supplied: str
    ) -> PrincipalAssignment:
        """Authenticate a token and an already-existing human assignment."""
        from envresearch.research.principal_authentication import existing_capability

        return existing_capability(self, kind, supplied)

    def capability_from_file(self, kind: PrincipalKind, supplied: Path) -> str:
        """Read only the exact owner capability through the pinned control root."""
        expected_path = self.control.path / self._capability_path(kind)
        if Path(os.path.abspath(supplied)) != expected_path:
            raise ValueError(f"{kind.value} principal capability file is invalid")
        data = self.control.storage.read_file(
            self._capability_path(kind),
            description=f"{kind.value} principal capability",
            required_mode=0o600,
            required_owner=os.getuid(),
        )
        return data.decode()

    def require_gate_actor(
        self, actor: str, reviewed: tuple[ProducerIdentity, ...]
    ) -> PrincipalAssignment:
        """Authenticate a gate actor and require separation from reviewed workers."""
        assignment = self.human(PrincipalKind.GATE)
        if actor != assignment.principal_id:
            raise ValueError("gate decision lacks the authenticated gate principal")
        contexts = {producer.context_id for producer in reviewed}
        if None in contexts or assignment.producer.context_id in contexts:
            raise ValueError("gate principal must differ from reviewed principals")
        return assignment

    def require_existing_gate_actor(
        self, actor: str, reviewed: tuple[ProducerIdentity, ...]
    ) -> PrincipalAssignment:
        """Authenticate an existing gate assignment without creating it."""
        from envresearch.research.principal_authentication import existing_gate_actor

        return existing_gate_actor(self, actor, reviewed)

    def record_gate_decision(self, gate: GateRequest) -> None:
        """Seal one exact terminal gate decision in owner-only control state."""
        if gate.decision is None:
            raise ValueError("gate decision authentication requires a decision")
        self.require_gate_actor(gate.decision.decided_by, ())
        digest = hashlib.sha256(_canonical(gate.model_dump(mode="json"))).hexdigest()
        identity = _canonical({"gate_id": gate.id, "decision_sha256": digest})
        record = _ProtectedGateDecision(
            gate_id=gate.id,
            decision_sha256=digest,
            mac=hmac.new(self.control.key, identity, hashlib.sha256).hexdigest(),
        )
        path = Path("principals/gates") / f"{gate.id}.json"
        data = _canonical(record.model_dump(mode="json"))
        if not self.control.storage.exists(path):
            try:
                self.control.storage.write_file_noreplace(path, data, mode=0o600)
            except FileExistsError:
                pass
        self.require_gate_decision(gate)

    def require_gate_decision(self, gate: GateRequest) -> None:
        """Authenticate an exact gate decision against protected control state."""
        path = Path("principals/gates") / f"{gate.id}.json"
        try:
            data = self.control.storage.read_file(
                path, description="gate principal decision", required_mode=0o600
            )
        except FileNotFoundError as error:
            raise ValueError("gate decision authentication is missing") from error
        record = _ProtectedGateDecision.model_validate_json(data)
        digest = hashlib.sha256(_canonical(gate.model_dump(mode="json"))).hexdigest()
        identity = _canonical(
            {"gate_id": record.gate_id, "decision_sha256": record.decision_sha256}
        )
        expected = hmac.new(self.control.key, identity, hashlib.sha256).hexdigest()
        if (
            data != _canonical(record.model_dump(mode="json"))
            or record.gate_id != gate.id
            or record.decision_sha256 != digest
            or not hmac.compare_digest(record.mac, expected)
        ):
            raise ValueError("gate decision authentication failed")

    def _persist_or_authenticate(
        self, assignment: PrincipalAssignment, path: Path | None = None
    ) -> PrincipalAssignment:
        path = path or Path("principals") / f"{assignment.kind.value}.json"
        payload = _canonical(assignment.model_dump(mode="json"))
        protected = _ProtectedPrincipal(
            assignment=assignment,
            mac=hmac.new(self.control.key, payload, hashlib.sha256).hexdigest(),
        )
        data = _canonical(protected.model_dump(mode="json"))
        if not self.control.storage.exists(path):
            try:
                self.control.storage.write_file_noreplace(path, data, mode=0o600)
            except FileExistsError:
                pass
        durable_data = self.control.storage.read_file(
            path, description="principal assignment", required_mode=0o600
        )
        durable = _ProtectedPrincipal.model_validate_json(durable_data)
        expected = hmac.new(
            self.control.key,
            _canonical(durable.assignment.model_dump(mode="json")),
            hashlib.sha256,
        ).hexdigest()
        if durable_data != _canonical(durable.model_dump(mode="json")) or not (
            hmac.compare_digest(durable.mac, expected)
            and durable.assignment == assignment
        ):
            raise ValueError("principal assignment authentication failed")
        return durable.assignment

    def _ensure_capability(self, kind: PrincipalKind, path: Path | None = None) -> None:
        path = path or self._capability_path(kind)
        if self.control.storage.exists(path):
            self.control.storage.read_file(
                path,
                description=f"{kind.value} principal capability",
                required_mode=0o600,
                required_owner=os.getuid(),
            )
            return
        try:
            self.control.storage.write_file_noreplace(
                path, secrets.token_hex(32).encode(), mode=0o600
            )
        except FileExistsError:
            self._ensure_capability(kind, path)

    def _read_benchmark_assignment(self, path: Path) -> PrincipalAssignment:
        try:
            data = self.control.storage.read_file(
                path, description="principal assignment", required_mode=0o600
            )
            durable = _ProtectedPrincipal.model_validate_json(data)
            expected = hmac.new(
                self.control.key,
                _canonical(durable.assignment.model_dump(mode="json")),
                hashlib.sha256,
            ).hexdigest()
        except ValueError as error:
            raise ValueError("principal assignment authentication failed") from error
        if data != _canonical(durable.model_dump(mode="json")) or not hmac.compare_digest(
            durable.mac, expected
        ):
            raise ValueError("principal assignment authentication failed")
        return durable.assignment

    def _benchmark_assignment(
        self,
        case_id: str,
        kind: PrincipalKind,
        index: int,
        *,
        human: bool,
        participant: HumanKeyEnrollment | None = None,
    ) -> PrincipalAssignment:
        if human and participant is None:
            raise ValueError("participant enrollment is missing")
        principal = (
            participant.principal_id
            if participant is not None
            else f"principal-{case_id}-{kind.value}"
        )
        key_hash = participant.public_key_sha256 if participant is not None else "worker"
        context = _digest_token(
            self.run_id, case_id, kind.value, str(index), key_hash, length=24
        )
        return PrincipalAssignment(
            assignment_id=f"assignment-{_digest_token(principal, context)}",
            principal_id=principal,
            kind=kind,
            producer=ProducerIdentity(
                component=f"assigned-{kind.value}", version="0.2.0",
                runtime="owner-control", context_id=f"context-{context}",
            ),
            verification=(
                PrincipalVerification.PUBLIC_KEY_SIGNATURE
                if human
                else PrincipalVerification.OWNER_CONTROL
            ),
            key_id=participant.key_id if participant is not None else None,
            public_key_sha256=(
                participant.public_key_sha256 if participant is not None else None
            ),
        )

    @staticmethod
    def _benchmark_path(case_id: str) -> Path:
        return Path("principals/benchmark") / case_id
    @staticmethod
    def _capability_path(kind: PrincipalKind) -> Path:
        return Path("principals") / f"{kind.value}.capability"
def _digest_token(*values: str, length: int = 16) -> str:
    return hashlib.sha256("\0".join(values).encode()).hexdigest()[:length]
def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
def _require_benchmark_kind(kind: PrincipalKind, *, human: bool) -> None:
    allowed = (
        (PrincipalKind.EXPERT, PrincipalKind.ADJUDICATOR)
        if human
        else (
            PrincipalKind.CURATOR,
            PrincipalKind.MASKER,
            PrincipalKind.LEAKAGE_VALIDATOR,
            PrincipalKind.RECOMMENDER,
        )
    )
    if kind not in allowed:
        role = "human" if human else "worker"
        raise ValueError(f"benchmark {role} principal kind is invalid")
def _require_benchmark_identity(value: str, label: str) -> None:
    if (
        not value
        or value[0] not in _BENCHMARK_ID_INITIAL
        or any(character not in _BENCHMARK_ID_CHARACTERS for character in value)
    ):
        raise ValueError(f"{label} must be a canonical identifier")
def _require_benchmark_slot(value: int, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{label} must be a positive integer")


def _assignment_case_id(assignment: PrincipalAssignment, kind: PrincipalKind) -> str:
    prefix = "principal-"
    suffix = f"-{kind.value}"
    if not assignment.principal_id.startswith(prefix) or not assignment.principal_id.endswith(suffix):
        raise ValueError("principal assignment authentication failed")
    case_id = assignment.principal_id[len(prefix) : -len(suffix)]
    _require_benchmark_identity(case_id, "benchmark case ID")
    return case_id


def _benchmark_generation(name: str, kind: PrincipalKind) -> int | None:
    prefix = f"{kind.value}-g"
    if not name.startswith(prefix) or not name.endswith(".json"):
        return None
    value = name[len(prefix) : -len(".json")]
    if not value.isdecimal() or value.startswith("0"):
        return None
    return int(value)
