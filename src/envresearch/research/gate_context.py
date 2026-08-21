"""Immutable artifact context binding for durable human research gates."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from envresearch.kernel.artifact_graph import ArtifactNode
from envresearch.kernel.gates import GateRequest, GateStore, utc_now
from envresearch.kernel.task_identity import payload_hash
from envresearch.models.artifact import ArtifactRef
from envresearch.models.enums import GateStatus
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.storage.artifacts import ArtifactStore

__all__ = ["BoundGateContext", "BoundGateManager"]


class BoundGateContext(BaseModel):
    """Exact immutable artifact versions reviewed in one gate revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_gate_id: str
    gate_id: str
    revision: int = Field(ge=1)
    supersedes_gate_id: str | None = None
    artifact_refs: tuple[ArtifactRef, ...]
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    context_hash: str | None = None

    @field_validator("requested_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
            raise ValueError("gate context timestamp must be UTC-aware")
        return value

    @model_validator(mode="after")
    def bind_hash(self) -> BoundGateContext:
        if (self.revision == 1) is (self.supersedes_gate_id is not None):
            raise ValueError("gate revision supersession identity is inconsistent")
        core = self.model_dump(mode="json", exclude={"context_hash"})
        digest = payload_hash(core)
        if self.context_hash is not None and self.context_hash != digest:
            raise ValueError("gate context hash mismatch")
        object.__setattr__(self, "context_hash", digest)
        return self


class BoundGateManager:
    """Create revisioned gates and verify request plus decision context exactly."""

    def __init__(
        self,
        workspace: Path,
        gates: GateStore,
        requested_by: str,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.workspace = workspace
        self.raw = ArtifactStore(workspace)
        self.gates = gates
        self.requested_by = requested_by
        self.clock = clock

    def ensure(
        self, base_gate_id: str, name: str, refs: tuple[ArtifactRef, ...]
    ) -> BoundGateContext:
        """Reuse an exact context or create a new immutable gate revision."""
        latest = self._latest_context(base_gate_id)
        if (
            latest is not None
            and not self._is_superseded(latest)
            and latest.artifact_refs == refs
        ):
            self._require_request(latest, name)
            return latest
        revision = 1 if latest is None else latest.revision + 1
        gate_id = base_gate_id if revision == 1 else f"{base_gate_id}-r{revision}"
        context = BoundGateContext(
            base_gate_id=base_gate_id,
            gate_id=gate_id,
            revision=revision,
            supersedes_gate_id=None if latest is None else latest.gate_id,
            artifact_refs=refs,
            requested_at=self.clock(),
        )
        relative = self._context_path(base_gate_id, revision)
        if (self.workspace / relative).exists():
            raise FileExistsError("gate context revision already exists")
        self.raw.write_json(relative, context.model_dump(mode="json"))
        self._require_request(context, name)
        return context

    def require_approved(
        self, base_gate_id: str, name: str, refs: tuple[ArtifactRef, ...]
    ) -> BoundGateContext | None:
        """Return the active approved context only when every audit binding matches."""
        context = self.active_context(base_gate_id)
        if context is None:
            return None
        gate = self._require_request(context, name)
        if context.artifact_refs != refs:
            if gate.status is GateStatus.APPROVED:
                raise ValueError("approved gate artifact context is stale or corrupt")
            return None
        if gate.status is not GateStatus.APPROVED:
            return None
        self.gates.require_approved(context.gate_id)
        assert gate.decision is not None
        supplied = gate.decision.conditions.get("gate_context")
        if supplied != context.model_dump(mode="json"):
            raise ValueError("gate decision context does not match its request")
        return context

    def active_context(self, base_gate_id: str) -> BoundGateContext | None:
        context = self._latest_context(base_gate_id)
        if context is None or self._is_superseded(context):
            return None
        return context

    def supersede_for_revision(
        self,
        base_gate_id: str,
        *,
        gate_id: str | None,
        context_hash: str | None,
        revision_id: str,
        actor: str,
        reason: str,
    ) -> None:
        """Supersede the exact context captured by an authenticated intent."""
        if gate_id is None and context_hash is None:
            return
        if gate_id is None or context_hash is None:
            raise ValueError("revision gate target identity is incomplete")
        matches = [
            context
            for context in self._contexts(base_gate_id)
            if context.gate_id == gate_id and context.context_hash == context_hash
        ]
        if len(matches) != 1:
            raise ValueError("revision gate target context changed")
        context = matches[0]
        relative = (
            Path("gate-contexts")
            / base_gate_id
            / "superseded"
            / f"{context.gate_id}.json"
        )
        payload = {
            "gate_id": context.gate_id,
            "context_hash": context.context_hash,
            "revision_id": revision_id,
            "actor": actor,
            "reason": reason,
        }
        if (self.workspace / relative).exists():
            if self.raw.read_json(relative) != payload:
                raise RuntimeError("gate supersession identity collision")
            return
        self.raw.write_json(relative, payload)

    def revision_effect_is_durable(
        self,
        base_gate_id: str,
        gate_id: str | None,
        context_hash: str | None,
        revision_id: str,
        actor: str,
        reason: str,
    ) -> bool:
        """Verify an exact supersession or authenticated no-active-gate state."""
        if gate_id is None and context_hash is None:
            return True
        if gate_id is None or context_hash is None:
            return False
        relative = (
            Path("gate-contexts") / base_gate_id / "superseded" / f"{gate_id}.json"
        )
        if not (self.workspace / relative).exists():
            return False
        expected = {
            "gate_id": gate_id,
            "context_hash": context_hash,
            "revision_id": revision_id,
            "actor": actor,
            "reason": reason,
        }
        return self.raw.read_json(relative) == expected

    def _latest_context(self, base_gate_id: str) -> BoundGateContext | None:
        contexts = self._contexts(base_gate_id)
        return contexts[-1] if contexts else None

    def _contexts(self, base_gate_id: str) -> tuple[BoundGateContext, ...]:
        """Load and validate the complete immutable context chain in order."""
        directory = self.workspace / "gate-contexts" / base_gate_id
        if not directory.exists():
            return ()
        names = sorted(directory.glob("*.json"))
        if not names:
            return ()
        contexts: list[BoundGateContext] = []
        for revision, name in enumerate(names, 1):
            context = BoundGateContext.model_validate_json(name.read_bytes())
            expected_gate_id = (
                base_gate_id if revision == 1 else f"{base_gate_id}-r{revision}"
            )
            expected_prior = None if not contexts else contexts[-1].gate_id
            if (
                name.name != f"{revision:04d}.json"
                or context.base_gate_id != base_gate_id
                or context.revision != revision
                or context.gate_id != expected_gate_id
                or context.supersedes_gate_id != expected_prior
            ):
                raise ValueError("gate context history identity mismatch")
            contexts.append(context)
        return tuple(contexts)

    def _is_superseded(self, context: BoundGateContext) -> bool:
        relative = (
            Path("gate-contexts")
            / context.base_gate_id
            / "superseded"
            / f"{context.gate_id}.json"
        )
        return (self.workspace / relative).exists()

    def active_gate(self, base_gate_id: str) -> GateRequest | None:
        context = self.active_context(base_gate_id)
        if context is None:
            return None
        return self._load_gate(context.gate_id)

    def decision_conditions(self, base_gate_id: str) -> dict[str, object]:
        context = self.active_context(base_gate_id)
        if context is None:
            raise ValueError("gate has no active artifact context")
        return {"gate_context": context.model_dump(mode="json")}

    @staticmethod
    def artifact_refs(
        gate_id: str,
        lifecycle: ResearchArtifactLifecycle,
        nodes: Mapping[str, ArtifactNode],
    ) -> tuple[ArtifactRef, ...]:
        """Resolve the exact immutable inputs reviewed by each gate."""
        if gate_id == "gate-1":
            return lifecycle.input_refs(nodes["approve-charter"])
        if gate_id == "data-gate":
            return (lifecycle.artifact_ref(Path("artifacts/data-feasibility.yaml")),)
        refs: tuple[ArtifactRef, ...] = (
            lifecycle.artifact_ref(Path("artifacts/design-review-findings.json")),
            lifecycle.validated_history_ref(Path("artifacts/analysis-plan.yaml")),
        )
        if "validate-citations" in nodes:
            refs += (
                lifecycle.artifact_ref(
                    Path("artifacts/citation-integrity-report.json")
                ),
            )
        return refs

    def _require_request(self, context: BoundGateContext, name: str) -> GateRequest:
        expected = GateRequest(
            id=context.gate_id,
            name=self._request_name(name, context),
            requested_by=self.requested_by,
            requested_at=context.requested_at,
        )
        relative = Path("gates") / f"{context.gate_id}.json"
        if not (self.workspace / relative).exists():
            self.gates.request(expected)
            return self._load_gate(context.gate_id)
        gate = self._load_gate(context.gate_id)
        if (
            gate.id != expected.id
            or gate.name != expected.name
            or gate.requested_by != expected.requested_by
            or gate.requested_at != expected.requested_at
        ):
            raise ValueError("gate request context does not match durable artifacts")
        if gate.status is GateStatus.PENDING:
            self.gates.request(expected)
            gate = self._load_gate(context.gate_id)
        return gate

    def _load_gate(self, gate_id: str) -> GateRequest:
        return GateRequest.model_validate(
            self.raw.read_json(Path("gates") / f"{gate_id}.json")
        )

    @staticmethod
    def _request_name(name: str, context: BoundGateContext) -> str:
        serialized = json.dumps(
            context.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return f"{name}|artifact-context={serialized}"

    @staticmethod
    def _context_path(base_gate_id: str, revision: int) -> Path:
        return Path("gate-contexts") / base_gate_id / f"{revision:04d}.json"
