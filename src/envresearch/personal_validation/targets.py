"""Fresh read-only adapters for exact Personal attempt targets."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.factory.service import FactoryRunService
from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation._strict import materialize_id
from envresearch.personal_validation.contracts import (
    AttemptRootInventory,
    CompletedFactoryRunTarget,
    CorrectStopTarget,
    PersonalCanonicalCase,
    PersonalValidationAttempt,
    PersonalValidationProtocol,
    PersonalValidationSession,
)
from envresearch.personal_validation.errors import (
    PersonalValidationAuthorityInvalid,
    PersonalValidationError,
    PersonalValidationIntegrityInvalid,
    PersonalValidationSupportInvalid,
)
from envresearch.personal_validation.events import PersonalValidationEvent
from envresearch.personal_validation.snapshots import require_correct_stop_inventory
from envresearch.research.stop_contracts import ResearchStopInspection
from envresearch.workers.filesystem import PinnedRoot

Payload = TypeVar("Payload", bound=BaseModel)
_ATTEMPT_ID = re.compile(r"^personal-attempt-[0-9a-f]{64}$")
_OBJECT_NAME = re.compile(r"^v([1-9][0-9]*)-([0-9a-f]{64})\.json$")
_ARTIFACT_ID = re.compile(r"^[a-z0-9._-]+$")
_IDENTITY_FIELDS = (
    "protocol_id",
    "case_id",
    "snapshot_id",
    "inventory_id",
    "session_id",
    "attempt_id",
    "bundle_id",
    "assignment_id",
    "review_id",
    "finding_id",
    "publication_id",
    "evaluation_id",
    "report_id",
    "proposal_id",
    "approval_id",
    "closure_id",
)


class PersonalObjectRegistry:
    """Canonical Personal objects over the store-owned pinned registry."""

    def __init__(
        self,
        *,
        registry: ExitRegistry,
        objects: PinnedRoot,
        verify_authority: Callable[[], None],
        require_writable: Callable[[], None],
    ) -> None:
        self.registry = registry
        self.objects = objects
        self.verify_authority = verify_authority
        self.require_writable = require_writable

    def publish(self, artifact_id: str, payload: Payload) -> ArtifactRef:
        self.require_writable()
        self.verify_authority()
        _require_payload_identity(artifact_id, payload)
        try:
            reference = self.registry.publish(artifact_id, payload)
            reopened = self._load_exact(reference, type(payload))
            if reopened != payload:
                raise ValueError("published Personal object differs after reopen")
            self.verify_authority()
            return reference
        except PersonalValidationError:
            raise
        except (OSError, TypeError, ValueError, ValidationError) as error:
            raise PersonalValidationIntegrityInvalid(
                "Personal object publication failed",
                finding_kind="personal-object-publication-invalid",
            ) from error

    def load(self, reference: ArtifactRef, model: type[Payload]) -> Payload:
        self.verify_authority()
        try:
            payload = self._load_exact(reference, model)
            _require_payload_identity(reference.artifact_id, payload)
            self.verify_authority()
            return payload
        except PersonalValidationError:
            raise
        except (OSError, TypeError, ValueError, ValidationError) as error:
            raise PersonalValidationIntegrityInvalid(
                "Personal object bytes are invalid",
                finding_kind="personal-object-invalid",
            ) from error

    def attempts(
        self,
    ) -> tuple[tuple[ArtifactRef, PersonalValidationAttempt], ...]:
        """Enumerate only attempt objects to detect an explicit orphan boundary."""
        self.verify_authority()
        found: list[tuple[ArtifactRef, PersonalValidationAttempt]] = []
        try:
            for artifact_id in self.objects.list_directory(Path("exit/objects")):
                if not _ATTEMPT_ID.fullmatch(artifact_id):
                    continue
                directory = Path("exit/objects") / artifact_id
                for filename in self.objects.list_directory(directory):
                    match = _OBJECT_NAME.fullmatch(filename)
                    if match is None:
                        raise ValueError("attempt object filename is invalid")
                    reference = ArtifactRef(
                        artifact_id=artifact_id,
                        artifact_version=int(match.group(1)),
                        content_hash=match.group(2),
                    )
                    attempt = self._load_exact(reference, PersonalValidationAttempt)
                    _require_payload_identity(artifact_id, attempt)
                    found.append((reference, attempt))
            self.verify_authority()
            return tuple(sorted(found, key=lambda item: _ref_key(item[0])))
        except PersonalValidationError:
            raise
        except (OSError, TypeError, ValueError, ValidationError) as error:
            raise PersonalValidationIntegrityInvalid(
                "Personal attempt object inventory is invalid",
                finding_kind="attempt-object-inventory-invalid",
            ) from error

    def _load_exact(self, reference: ArtifactRef, model: type[Payload]) -> Payload:
        if not _ARTIFACT_ID.fullmatch(reference.artifact_id):
            raise ValueError("Personal object artifact ID is not canonical")
        relative = (
            Path("exit/objects")
            / reference.artifact_id
            / f"v{reference.artifact_version}-{reference.content_hash}.json"
        )
        data = self.registry.files.read(relative)
        if hashlib.sha256(data).hexdigest() != reference.content_hash:
            raise ValueError("Personal object content hash mismatch")
        permissive = model.model_validate_json(data, strict=False)
        if data != permissive.model_dump_json().encode():
            raise ValueError("Personal object bytes are noncanonical")
        strict = model.model_validate(
            permissive.model_dump(mode="python", round_trip=True), strict=True
        )
        if strict != permissive:
            raise ValueError("Personal object strict reopen changed its value")
        return strict


def completed_factory_target(
    service: FactoryRunService, run_ref: ArtifactRef
) -> CompletedFactoryRunTarget:
    status = service.status(run_ref)
    return CompletedFactoryRunTarget(
        target_type="completed-factory-run",
        run_ref=status.run_ref,
        run=status.run,
    )


def require_exact_object_layout(objects: PinnedRoot) -> None:
    """Require the initialized registry layout without recreating damage."""
    try:
        for relative in (
            Path("exit/objects"),
            Path("exit/current"),
            Path("exit/locks"),
        ):
            descriptor = objects.open_directory(relative, create=False)
            try:
                metadata = os.fstat(descriptor)
                if (
                    metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o700
                ):
                    raise ValueError("Personal object layout is not owner-private")
            finally:
                os.close(descriptor)
    except (OSError, TypeError, ValueError) as error:
        raise PersonalValidationAuthorityInvalid(
            "Personal object layout authority is invalid",
            finding_kind="private-object-layout-invalid",
        ) from error


def correct_stop_target(
    inspection_ref: ArtifactRef,
    inspection: ResearchStopInspection,
    inventory_ref: ArtifactRef,
    inventory: AttemptRootInventory,
) -> CorrectStopTarget:
    require_correct_stop_inventory(inventory)
    return CorrectStopTarget(
        target_type="correct-stop",
        inspection_ref=inspection_ref,
        inspection=inspection,
        attempt_inventory_ref=inventory_ref,
    )


def personal_session(
    nonce: str,
    protocol_ref: ArtifactRef,
    protocol: PersonalValidationProtocol,
) -> PersonalValidationSession:
    payload: dict[str, object] = {
        "schema_version": "personal.validation-session.v1",
        "session_nonce": nonce,
        "protocol_ref": protocol_ref,
        "cases": protocol.cases,
    }
    payload["session_id"] = materialize_id("personal-session-", payload)
    return PersonalValidationSession.model_validate(payload)


def personal_attempt(
    *,
    protocol_ref: ArtifactRef,
    case_ref: ArtifactRef,
    input_snapshot_ref: ArtifactRef,
    system_snapshot_ref: ArtifactRef,
    inventory_ref: ArtifactRef,
    target: CompletedFactoryRunTarget | CorrectStopTarget,
    start_event_id: str,
    predecessor_attempt_ref: ArtifactRef | None,
) -> PersonalValidationAttempt:
    payload: dict[str, object] = {
        "schema_version": "personal.validation-attempt.v1",
        "protocol_ref": protocol_ref,
        "case_ref": case_ref,
        "input_snapshot_ref": input_snapshot_ref,
        "system_snapshot_ref": system_snapshot_ref,
        "attempt_inventory_ref": inventory_ref,
        "target": target,
        "start_event_id": start_event_id,
        "predecessor_attempt_ref": predecessor_attempt_ref,
    }
    payload["attempt_id"] = materialize_id("personal-attempt-", payload)
    return PersonalValidationAttempt.model_validate(payload)


def require_start_event(
    events: tuple[PersonalValidationEvent, ...], session_ref: ArtifactRef
) -> PersonalValidationEvent:
    if not events:
        raise PersonalValidationIntegrityInvalid(
            "Personal session start event is missing",
            finding_kind="attempt-event-incomplete",
        )
    start = events[0]
    if (
        start.sequence != 1
        or start.operation != "session-started"
        or start.object_ref != session_ref
        or any(event.operation == "session-started" for event in events[1:])
    ):
        raise PersonalValidationIntegrityInvalid(
            "Personal session start event is invalid",
            finding_kind="session-start-event-invalid",
        )
    return start


def require_attempt_closure(
    session_ref: ArtifactRef,
    session: PersonalValidationSession,
    start: PersonalValidationEvent,
    completions: tuple[PersonalValidationEvent, ...],
    attempts: tuple[PersonalValidationAttempt, ...],
) -> None:
    case_refs = {item.case_ref for item in session.cases}
    seen: set[ArtifactRef] = set()
    latest_by_case: dict[ArtifactRef, ArtifactRef] = {}
    for event, attempt in zip(completions, attempts, strict=True):
        if (
            event.object_ref in seen
            or attempt.protocol_ref != session.protocol_ref
            or attempt.case_ref not in case_refs
            or attempt.start_event_id != start.event_id
            or attempt.predecessor_attempt_ref != latest_by_case.get(attempt.case_ref)
        ):
            raise PersonalValidationIntegrityInvalid(
                "Personal attempt completion does not bind its session",
                finding_kind="attempt-event-invalid",
            )
        seen.add(event.object_ref)
        latest_by_case[attempt.case_ref] = event.object_ref
    if session_ref.artifact_id != session.session_id:
        raise PersonalValidationIntegrityInvalid(
            "Personal status session reference is invalid",
            finding_kind="session-object-invalid",
        )


def require_target_kind(
    case: PersonalCanonicalCase,
    target: CompletedFactoryRunTarget | CorrectStopTarget,
) -> None:
    expected = (
        "factory-run"
        if isinstance(target, CompletedFactoryRunTarget)
        else "correct-stop"
    )
    if case.expected_terminal_kind != expected:
        raise PersonalValidationSupportInvalid(
            "attempt target kind differs from the canonical case",
            finding_kind="attempt-target-kind-invalid",
        )


def model_ref(
    identity: str, payload: PersonalValidationSession | PersonalValidationAttempt
) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=identity,
        artifact_version=1,
        content_hash=hashlib.sha256(payload.model_dump_json().encode()).hexdigest(),
    )


def _require_payload_identity(artifact_id: str, payload: BaseModel) -> None:
    for field in _IDENTITY_FIELDS:
        if field in type(payload).model_fields:
            if getattr(payload, field) != artifact_id:
                raise ValueError("Personal object ref does not bind its internal ID")
            return


def _ref_key(reference: ArtifactRef) -> tuple[str, int, str]:
    return (
        reference.artifact_id,
        reference.artifact_version,
        reference.content_hash,
    )


__all__ = [
    "PersonalObjectRegistry",
    "completed_factory_target",
    "correct_stop_target",
    "model_ref",
    "personal_attempt",
    "personal_session",
    "require_attempt_closure",
    "require_exact_object_layout",
    "require_start_event",
    "require_target_kind",
]
