"""Protected context-scoped request and terminal promotion events."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, ValidationError

from envresearch.factory._event_log import (
    append_event_atomically as append_event_once,
)
from envresearch.factory.errors import FactoryIntegrityInvalid
from envresearch.factory.promotion_contracts import FactoryRunPromotion
from envresearch.kernel.events import EventLog, EventRecord
from envresearch.models.artifact import ArtifactRef
from envresearch.models.enums import WorkflowStatus

if TYPE_CHECKING:
    from envresearch.research.principal_registry import PrincipalRegistry

_ROOT = Path("principals/factory-promotions")
_STRICT = ConfigDict(extra="forbid", frozen=True, strict=True)


class _ProtectedRequest(BaseModel):
    model_config = _STRICT
    event: EventRecord
    mac: str


class _ProtectedTerminal(BaseModel):
    model_config = _STRICT
    context_ref: ArtifactRef
    promotion_ref: ArtifactRef
    mac: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


class _PromotionEvents:
    def __init__(self, root: Path, principals: PrincipalRegistry) -> None:
        self.principals = principals
        self.events = EventLog(root / "factory-events.jsonl")

    def bootstrap(self) -> None:
        control = self.principals.control
        control.storage.ensure_directory(_ROOT)
        control.storage.ensure_directory(_ROOT / "requests")
        control.storage.ensure_directory(_ROOT / "decisions")

    def ensure_request(
        self, context_ref: ArtifactRef, requested_by: str
    ) -> EventRecord:
        path = self._request_path(context_ref)
        control = self.principals.control
        if not control.storage.exists(path):
            event = self._request_event(context_ref, requested_by)
            protected = _ProtectedRequest(
                event=event,
                mac=hmac.new(
                    control.key,
                    _canonical(event.model_dump(mode="json")),
                    hashlib.sha256,
                ).hexdigest(),
            )
            try:
                control.storage.write_file_noreplace(
                    path, _canonical(protected.model_dump(mode="json")), mode=0o600
                )
            except FileExistsError:
                pass
        event = self._load_request(context_ref, requested_by)
        self._append(event)
        return self.require_request(context_ref, requested_by)

    def require_request(
        self, context_ref: ArtifactRef, requested_by: str
    ) -> EventRecord:
        event = self._load_request(context_ref, requested_by)
        matches = tuple(
            item for item in self._read() if item.event_id == event.event_id
        )
        if matches != (event,):
            raise FactoryIntegrityInvalid(
                "promotion request authentication failed",
                finding_kind="promotion-request-invalid",
            )
        return event

    def _load_request(self, context_ref: ArtifactRef, requested_by: str) -> EventRecord:
        try:
            data = self.principals.control.storage.read_file(
                self._request_path(context_ref),
                description="factory promotion request",
                required_mode=0o600,
                required_owner=os.getuid(),
            )
            protected = _ProtectedRequest.model_validate_json(data)
        except (OSError, ValueError, ValidationError) as exc:
            raise FactoryIntegrityInvalid(
                "promotion request authentication is missing or invalid",
                finding_kind="promotion-request-invalid",
            ) from exc
        expected = hmac.new(
            self.principals.control.key,
            _canonical(protected.event.model_dump(mode="json")),
            hashlib.sha256,
        ).hexdigest()
        event = protected.event
        if (
            data != _canonical(protected.model_dump(mode="json"))
            or not hmac.compare_digest(protected.mac, expected)
            or event != self._request_event(context_ref, requested_by, event.timestamp)
        ):
            raise FactoryIntegrityInvalid(
                "promotion request authentication failed",
                finding_kind="promotion-request-invalid",
            )
        return event

    def terminal_ref(self, context_ref: ArtifactRef) -> ArtifactRef | None:
        path = self._terminal_path(context_ref)
        if not self.principals.control.storage.exists(path):
            return None
        return self._load_terminal(context_ref).promotion_ref

    def ensure_terminal(
        self,
        context_ref: ArtifactRef,
        reference: ArtifactRef,
        promotion: FactoryRunPromotion,
    ) -> None:
        current = self.terminal_ref(context_ref)
        if current is not None and current != reference:
            raise FactoryIntegrityInvalid(
                "promotion context already has a terminal decision",
                finding_kind="promotion-terminal",
            )
        path = self._terminal_path(context_ref)
        if current is None:
            record = self._terminal_record(context_ref, reference)
            try:
                self.principals.control.storage.write_file_noreplace(
                    path, _canonical(record.model_dump(mode="json")), mode=0o600
                )
            except FileExistsError:
                pass
        terminal = self._load_terminal(context_ref)
        if terminal.promotion_ref != reference:
            raise FactoryIntegrityInvalid(
                "promotion context has a different terminal decision",
                finding_kind="promotion-terminal",
            )
        self._append(self._decision_event(context_ref, reference, promotion))
        self.require_terminal(context_ref, reference, promotion)

    def require_terminal(
        self,
        context_ref: ArtifactRef,
        reference: ArtifactRef,
        promotion: FactoryRunPromotion,
    ) -> None:
        terminal = self._load_terminal(context_ref)
        if terminal.promotion_ref != reference:
            raise FactoryIntegrityInvalid(
                "promotion context has a different terminal decision",
                finding_kind="promotion-terminal",
            )
        expected = self._decision_event(context_ref, reference, promotion)
        matches = tuple(
            item for item in self._read() if item.event_id == expected.event_id
        )
        if matches != (expected,):
            raise FactoryIntegrityInvalid(
                "promotion decision event authentication failed",
                finding_kind="promotion-decision-event-invalid",
            )

    def _load_terminal(self, context_ref: ArtifactRef) -> _ProtectedTerminal:
        try:
            data = self.principals.control.storage.read_file(
                self._terminal_path(context_ref),
                description="factory promotion decision",
                required_mode=0o600,
                required_owner=os.getuid(),
            )
            terminal = _ProtectedTerminal.model_validate_json(data)
        except (OSError, ValueError, ValidationError) as exc:
            raise FactoryIntegrityInvalid(
                "promotion decision authentication is missing or invalid",
                finding_kind="promotion-decision-invalid",
            ) from exc
        identity = {
            "context_ref": terminal.context_ref.model_dump(mode="json"),
            "promotion_ref": terminal.promotion_ref.model_dump(mode="json"),
        }
        expected = hmac.new(
            self.principals.control.key, _canonical(identity), hashlib.sha256
        ).hexdigest()
        if (
            data != _canonical(terminal.model_dump(mode="json"))
            or terminal.context_ref != context_ref
            or not hmac.compare_digest(terminal.mac, expected)
        ):
            raise FactoryIntegrityInvalid(
                "promotion decision authentication failed",
                finding_kind="promotion-decision-invalid",
            )
        return terminal

    def _terminal_record(
        self, context_ref: ArtifactRef, reference: ArtifactRef
    ) -> _ProtectedTerminal:
        identity = {
            "context_ref": context_ref.model_dump(mode="json"),
            "promotion_ref": reference.model_dump(mode="json"),
        }
        return _ProtectedTerminal(
            context_ref=context_ref,
            promotion_ref=reference,
            mac=hmac.new(
                self.principals.control.key, _canonical(identity), hashlib.sha256
            ).hexdigest(),
        )

    def _append(self, event: EventRecord) -> None:
        try:
            append_event_once(self.events, event)
        except RuntimeError as exc:
            raise FactoryIntegrityInvalid(
                "promotion event identity conflicts",
                finding_kind="promotion-event-conflict",
            ) from exc
        except (OSError, TypeError, ValueError) as exc:
            raise FactoryIntegrityInvalid(
                "promotion event log is invalid",
                finding_kind="promotion-event-log-invalid",
            ) from exc

    def _read(self) -> tuple[EventRecord, ...]:
        try:
            return tuple(self.events.read_all())
        except (OSError, TypeError, ValueError) as exc:
            raise FactoryIntegrityInvalid(
                "promotion event log is invalid",
                finding_kind="promotion-event-log-invalid",
            ) from exc

    @staticmethod
    def _request_event(
        context_ref: ArtifactRef,
        requested_by: str,
        timestamp: datetime | None = None,
    ) -> EventRecord:
        return EventRecord(
            event_id=f"{context_ref.artifact_id}.requested",
            run_id=context_ref.artifact_id,
            event_type="factory_promotion_requested",
            actor=requested_by,
            timestamp=timestamp or datetime.now(UTC),
            from_status=WorkflowStatus.REVIEW_REQUIRED,
            to_status=WorkflowStatus.REVIEW_REQUIRED,
            payload={"context_ref": context_ref.model_dump(mode="json")},
        )

    @staticmethod
    def _decision_event(
        context_ref: ArtifactRef,
        reference: ArtifactRef,
        promotion: FactoryRunPromotion,
    ) -> EventRecord:
        return EventRecord(
            event_id=f"{context_ref.artifact_id}.decided",
            run_id=context_ref.artifact_id,
            event_type="factory_promotion_decided",
            actor=promotion.decision.decided_by,
            timestamp=promotion.decision.decided_at,
            from_status=WorkflowStatus.REVIEW_REQUIRED,
            to_status=(
                WorkflowStatus.APPROVED
                if promotion.decision.status.value == "approved"
                else WorkflowStatus.REJECTED
            ),
            payload={"promotion_ref": reference.model_dump(mode="json")},
        )

    @staticmethod
    def _request_path(reference: ArtifactRef) -> Path:
        return _ROOT / "requests" / f"{reference.artifact_id}.json"

    @staticmethod
    def _terminal_path(context_ref: ArtifactRef) -> Path:
        return _ROOT / "decisions" / f"{context_ref.artifact_id}.json"


__all__ = ["_PromotionEvents"]
