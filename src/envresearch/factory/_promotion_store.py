"""Canonical promotion storage plus protected request/decision anchors."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel, ValidationError

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.factory._promotion_events import _PromotionEvents
from envresearch.factory.errors import FactoryAuthorityInvalid, FactoryIntegrityInvalid
from envresearch.factory.promotion_contracts import (
    FactoryPromotionContext,
    FactoryRunPromotion,
)
from envresearch.kernel.events import EventLog, EventRecord
from envresearch.kernel.gates import GateDecision
from envresearch.models.artifact import ArtifactRef
from envresearch.models.enums import GateStatus
from envresearch.models.principal import PrincipalAssignment

if TYPE_CHECKING:
    from envresearch.research.principal_registry import PrincipalRegistry

CONTEXT_PREPARED = "research-factory-promotion-context-prepared"
CONTEXT_COMMITTED = "research-factory-promotion-context"
PROMOTION_PREPARED = "research-factory-promotion-prepared"
PROMOTION_COMMITTED = "research-factory-promotion"
Payload = TypeVar("Payload", bound=BaseModel)


class _FactoryPromotionStore:
    """Two independent pointer pairs and owner-authenticated operational events."""

    def __init__(self, registry: ExitRegistry, principals: PrincipalRegistry) -> None:
        self.registry = ExitRegistry(registry.root, create=False)
        self.principals = principals
        self.context_prepared_subject = CONTEXT_PREPARED
        self.context_committed_subject = CONTEXT_COMMITTED
        self.promotion_prepared_subject = PROMOTION_PREPARED
        self.promotion_committed_subject = PROMOTION_COMMITTED
        self._events = _PromotionEvents(registry.root, principals)

    @property
    def events(self) -> EventLog:
        return self._events.events

    @contextmanager
    def lease(self, *, bootstrap: bool = False) -> Iterator[None]:
        """Acquire promotion subjects after the already-held factory-run lease."""
        if bootstrap:
            self._events.bootstrap()
        registry = ExitRegistry(self.registry.root, create=bootstrap)
        with (
            registry.lock(self.context_prepared_subject),
            registry.lock(self.context_committed_subject),
            registry.lock(self.promotion_prepared_subject),
            registry.lock(self.promotion_committed_subject),
        ):
            yield

    def context_prepared(self) -> ArtifactRef | None:
        return self._pointer(self.context_prepared_subject)

    def context_committed(self) -> ArtifactRef | None:
        return self._pointer(self.context_committed_subject)

    def context_current(self) -> ArtifactRef | None:
        return self._paired_current(
            self.context_prepared(), self.context_committed(), "context"
        )

    def promotion_prepared(self) -> ArtifactRef | None:
        return self._pointer(self.promotion_prepared_subject)

    def promotion_committed(self) -> ArtifactRef | None:
        return self._pointer(self.promotion_committed_subject)

    def promotion_current(self) -> ArtifactRef | None:
        return self._paired_current(
            self.promotion_prepared(), self.promotion_committed(), "promotion"
        )

    def probe_context_intent(self, run_ref: ArtifactRef, requester: str) -> None:
        """Authenticate caller-bound context intent before run reconstruction."""
        prepared = self.context_prepared()
        committed = self.context_committed()
        if committed is not None and prepared != committed:
            if prepared is None:
                raise FactoryIntegrityInvalid(
                    "promotion context recovery pointers conflict",
                    finding_kind="promotion-context-current-invalid",
                )
            context = self.load_context(prepared)
            promotion_ref = self.promotion_current()
            if (
                context.run_ref != run_ref
                or context.requested_by != requester
                or promotion_ref is None
            ):
                raise FactoryIntegrityInvalid(
                    "promotion context recovery intent conflicts with caller",
                    finding_kind="promotion-request-conflict",
                )
            self._require_rejected_predecessor(context, committed, promotion_ref)
            return
        intent = prepared or committed
        if intent is None:
            return
        context = self.load_context(intent)
        if context.run_ref != run_ref or context.requested_by != requester:
            raise FactoryIntegrityInvalid(
                "promotion context recovery intent conflicts with caller",
                finding_kind="promotion-request-conflict",
            )
        if committed is not None:
            self.require_request_event(intent, requester)

    def probe_promotion_intent(
        self,
        context_ref: ArtifactRef,
        decision: GateDecision,
        capability_digest: str,
        principal: PrincipalAssignment,
    ) -> None:
        """Authenticate decision intent and protected anchors before reopening run."""
        prepared = self.promotion_prepared()
        committed = self.promotion_committed()
        if committed is not None and prepared != committed:
            if prepared is None:
                raise FactoryIntegrityInvalid(
                    "promotion recovery pointers conflict",
                    finding_kind="promotion-pointer-conflict",
                )
            promotion = self.load_promotion(prepared)
            context = self.load_context(context_ref)
            if (
                promotion.context_ref != context_ref
                or promotion.context != context
                or promotion.decision != decision
                or promotion.principal_capability_sha256 != capability_digest
                or promotion.authenticated_principal != principal
                or self.context_current() != context_ref
            ):
                raise FactoryIntegrityInvalid(
                    "terminal promotion recovery intent conflicts with caller",
                    finding_kind="promotion-terminal",
                )
            predecessor = self.load_promotion(committed)
            self._require_rejected_predecessor(
                context, predecessor.context_ref, committed
            )
            self.require_request_event(context_ref, context.requested_by)
            terminal = self.terminal_ref(context_ref)
            if terminal is not None:
                if terminal != prepared:
                    raise FactoryIntegrityInvalid(
                        "promotion recovery anchor conflicts with intent",
                        finding_kind="promotion-terminal",
                    )
                self.require_decision_event(prepared, promotion)
            return
        intent = prepared or committed
        if intent is None:
            return
        promotion = self.load_promotion(intent)
        if promotion.context_ref != context_ref:
            context = self.load_context(context_ref)
            if self.context_current() != context_ref:
                raise FactoryIntegrityInvalid(
                    "terminal promotion recovery intent conflicts with caller",
                    finding_kind="promotion-terminal",
                )
            self._require_rejected_predecessor(context, promotion.context_ref, intent)
            return
        if (
            promotion.decision != decision
            or promotion.principal_capability_sha256 != capability_digest
            or promotion.authenticated_principal != principal
        ):
            raise FactoryIntegrityInvalid(
                "terminal promotion recovery intent conflicts with caller",
                finding_kind="promotion-terminal",
            )
        self.require_request_event(context_ref, promotion.context.requested_by)
        terminal = self.terminal_ref(context_ref)
        if terminal is not None:
            if terminal != intent:
                raise FactoryIntegrityInvalid(
                    "promotion recovery anchor conflicts with intent",
                    finding_kind="promotion-terminal",
                )
            if committed is not None:
                self.require_decision_event(intent, promotion)

    def _require_rejected_predecessor(
        self,
        context: FactoryPromotionContext,
        previous_ref: ArtifactRef,
        promotion_ref: ArtifactRef,
    ) -> None:
        previous = self.load_context(previous_ref)
        promotion = self.load_promotion(promotion_ref)
        if (
            promotion.context_ref != previous_ref
            or promotion.context != previous
            or promotion.decision.status is not GateStatus.REJECTED
            or context.generation != previous.generation + 1
            or context.run_ref != previous.run_ref
            or context.requested_by != previous.requested_by
            or self.terminal_ref(previous_ref) != promotion_ref
        ):
            raise FactoryIntegrityInvalid(
                "promotion generation rollover is invalid",
                finding_kind="promotion-terminal",
            )
        self.require_request_event(previous_ref, previous.requested_by)
        self.require_decision_event(promotion_ref, promotion)
        self.require_principal_capability(promotion)

    def publish_context(self, context: FactoryPromotionContext) -> ArtifactRef:
        return self._publish(context.context_id, context, "context")

    def publish_promotion(self, promotion: FactoryRunPromotion) -> ArtifactRef:
        return self._publish(promotion.promotion_id, promotion, "promotion")

    def _publish(self, artifact_id: str, payload: BaseModel, label: str) -> ArtifactRef:
        try:
            return self.registry.publish(artifact_id, payload)
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            raise FactoryIntegrityInvalid(
                f"promotion {label} object publication failed",
                finding_kind=f"promotion-{label}-publish-failed",
            ) from exc

    def install(
        self,
        subject: str,
        reference: ArtifactRef,
        *,
        previous: ArtifactRef | None,
    ) -> None:
        current = self._pointer(subject)
        if current == reference:
            return
        if current != previous:
            raise FactoryIntegrityInvalid(
                "promotion pointer conflicts with another writer",
                finding_kind="promotion-pointer-conflict",
            )
        try:
            self.registry.set_current(subject, reference)
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            if self._pointer(subject) == reference:
                return
            raise FactoryIntegrityInvalid(
                "promotion pointer publication failed",
                finding_kind="promotion-pointer-failed",
            ) from exc

    def compare_and_restore(
        self,
        subject: str,
        *,
        installed: ArtifactRef,
        previous: ArtifactRef | None,
    ) -> None:
        try:
            restored = self.registry.restore_current_if_unchanged(
                subject, installed=installed, previous=previous
            )
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            raise FactoryIntegrityInvalid(
                "promotion pointer recovery failed",
                finding_kind="promotion-recovery-failed",
            ) from exc
        if not restored:
            raise FactoryIntegrityInvalid(
                "promotion recovery lost pointer ownership",
                finding_kind="promotion-recovery-conflict",
            )

    def load_context(self, reference: ArtifactRef) -> FactoryPromotionContext:
        return self._load(reference, FactoryPromotionContext, "context")

    def load_promotion(self, reference: ArtifactRef) -> FactoryRunPromotion:
        return self._load(reference, FactoryRunPromotion, "promotion")

    def require_principal_capability(self, promotion: FactoryRunPromotion) -> None:
        """Rehash the existing protected capability bound into a promotion."""
        try:
            data = self.principals.control.storage.read_file(
                Path("principals/gate.capability"),
                description="gate principal capability",
                required_mode=0o600,
                required_owner=os.getuid(),
            )
            if hashlib.sha256(data).hexdigest() != (
                promotion.principal_capability_sha256
            ):
                raise ValueError("gate principal capability digest changed")
        except (OSError, ValueError) as exc:
            raise FactoryAuthorityInvalid(
                "gate principal capability is missing or invalid",
                finding_kind="promotion-principal-invalid",
            ) from exc

    def ensure_request_event(
        self, context_ref: ArtifactRef, requested_by: str
    ) -> EventRecord:
        return self._events.ensure_request(context_ref, requested_by)

    def require_request_event(
        self, context_ref: ArtifactRef, requested_by: str
    ) -> EventRecord:
        return self._events.require_request(context_ref, requested_by)

    def ensure_decision_event(
        self, reference: ArtifactRef, promotion: FactoryRunPromotion
    ) -> None:
        self._events.ensure_terminal(promotion.context_ref, reference, promotion)

    def require_decision_event(
        self, reference: ArtifactRef, promotion: FactoryRunPromotion
    ) -> None:
        self._events.require_terminal(promotion.context_ref, reference, promotion)

    def ensure_promotion_anchor(self, reference: ArtifactRef) -> None:
        promotion = self.load_promotion(reference)
        self._events.ensure_terminal(promotion.context_ref, reference, promotion)

    def require_promotion_anchor(self, reference: ArtifactRef) -> None:
        promotion = self.load_promotion(reference)
        self._events.require_terminal(promotion.context_ref, reference, promotion)

    def terminal_ref(self, context_ref: ArtifactRef) -> ArtifactRef | None:
        return self._events.terminal_ref(context_ref)

    def context_object_path(self, reference: ArtifactRef) -> Path:
        return self._object_path(reference)

    def promotion_object_path(self, reference: ArtifactRef) -> Path:
        return self._object_path(reference)

    def _paired_current(
        self, prepared: ArtifactRef | None, committed: ArtifactRef | None, label: str
    ) -> ArtifactRef | None:
        if prepared is None and committed is None:
            return None
        if prepared is None or prepared != committed:
            raise FactoryIntegrityInvalid(
                f"promotion {label} pointer pair is torn",
                finding_kind=f"promotion-{label}-current-invalid",
            )
        return prepared

    def _pointer(self, subject: str) -> ArtifactRef | None:
        try:
            return self.registry.current(subject)
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            raise FactoryIntegrityInvalid(
                "promotion pointer is invalid", finding_kind="promotion-pointer-invalid"
            ) from exc

    def _load(
        self, reference: ArtifactRef, model: type[Payload], label: str
    ) -> Payload:
        try:
            data = self.registry.files.read(self._object_path(reference))
            if hashlib.sha256(data).hexdigest() != reference.content_hash:
                raise ValueError("content hash mismatch")
            payload = model.model_validate_json(data)
            if data != payload.model_dump_json().encode():
                raise ValueError("noncanonical bytes")
            return payload
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            raise FactoryIntegrityInvalid(
                f"promotion {label} immutable bytes are invalid",
                finding_kind=f"promotion-{label}-bytes-invalid",
            ) from exc

    @staticmethod
    def _object_path(reference: ArtifactRef) -> Path:
        return (
            Path("exit/objects")
            / reference.artifact_id
            / f"v{reference.artifact_version}-{reference.content_hash}.json"
        )


__all__ = ["_FactoryPromotionStore"]
