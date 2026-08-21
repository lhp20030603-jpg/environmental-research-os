"""Exact Personal session and attempt preparation over private storage."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from envresearch.factory.service import FactoryRunService
from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation.contracts import (
    PERSONAL_ATTEMPT_ROOTS_V1,
    AttemptRootInventory,
    CompletedFactoryRunTarget,
    CorrectStopTarget,
    InputSnapshot,
    PersonalCanonicalCase,
    PersonalValidationAttempt,
    PersonalValidationProtocol,
    PersonalValidationSession,
    SystemSnapshot,
)
from envresearch.personal_validation.errors import (
    PersonalValidationError,
    PersonalValidationIntegrityInvalid,
    PersonalValidationSupportInvalid,
)
from envresearch.personal_validation.events import (
    PersonalValidationEvent,
    session_events,
)
from envresearch.personal_validation.external_access import ExternalAccessLifecycleMixin
from envresearch.personal_validation.private_store import PersonalValidationStore
from envresearch.personal_validation.report import ReportLifecycleMixin
from envresearch.personal_validation.review_bundle import BundleLifecycleMixin
from envresearch.personal_validation.reviews import ReviewLifecycleMixin
from envresearch.personal_validation.targets import (
    completed_factory_target,
    correct_stop_target,
    model_ref,
    personal_attempt,
    personal_session,
    require_attempt_closure,
    require_start_event,
    require_target_kind,
)
from envresearch.research.stop_contracts import ResearchStopInspection

FailureInjector = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class PreparedAttempt:
    session_ref: ArtifactRef
    attempt_ref: ArtifactRef
    completion_event_id: str


@dataclass(frozen=True, slots=True)
class PersonalValidationStatus:
    session_ref: ArtifactRef
    session: PersonalValidationSession
    attempt_refs: tuple[ArtifactRef, ...]
    attempts: tuple[PersonalValidationAttempt, ...]
    completion_event_ids: tuple[str, ...]


class PersonalValidationService(
    BundleLifecycleMixin,
    ReviewLifecycleMixin,
    ExternalAccessLifecycleMixin,
    ReportLifecycleMixin,
):
    """Prepare immutable attempts without gaining product authority."""

    def __init__(
        self,
        *,
        store: PersonalValidationStore,
        factory_service: FactoryRunService | None,
        session_nonce: str,
        system_snapshot_ref: ArtifactRef,
        attempt_inventory_ref: ArtifactRef,
        failure_injector: FailureInjector | None = None,
    ) -> None:
        self.store = store
        self.factory_service = factory_service
        self.session_nonce = session_nonce
        self.system_snapshot_ref = system_snapshot_ref
        self.attempt_inventory_ref = attempt_inventory_ref
        self.failure_injector = failure_injector

    @property
    def system_snapshot(self) -> SystemSnapshot:
        return self.store.load(self.system_snapshot_ref, SystemSnapshot)

    def prepare_existing_run(
        self,
        protocol_ref: ArtifactRef,
        case_ref: ArtifactRef,
        run_ref: ArtifactRef,
        *,
        predecessor_attempt_ref: ArtifactRef | None = None,
    ) -> PreparedAttempt:
        if self.factory_service is None:
            raise PersonalValidationSupportInvalid(
                "Factory service is required for a completed target",
                finding_kind="factory-target-unavailable",
            )
        target = completed_factory_target(self.factory_service, run_ref)
        return self._prepare(
            protocol_ref,
            case_ref,
            target,
            predecessor_attempt_ref=predecessor_attempt_ref,
        )

    def prepare_correct_stop(
        self,
        protocol_ref: ArtifactRef,
        case_ref: ArtifactRef,
        inspection_ref: ArtifactRef,
        inspection: ResearchStopInspection,
        *,
        predecessor_attempt_ref: ArtifactRef | None = None,
    ) -> PreparedAttempt:
        reopened = self.store.load(inspection_ref, ResearchStopInspection)
        if reopened != inspection:
            raise PersonalValidationIntegrityInvalid(
                "correct-stop inspection differs from its private object",
                finding_kind="correct-stop-inspection-invalid",
            )
        inventory = self.store.load(self.attempt_inventory_ref, AttemptRootInventory)
        target = correct_stop_target(
            inspection_ref,
            reopened,
            self.attempt_inventory_ref,
            inventory,
        )
        return self._prepare(
            protocol_ref,
            case_ref,
            target,
            predecessor_attempt_ref=predecessor_attempt_ref,
        )

    def status(self, session_ref: ArtifactRef) -> PersonalValidationStatus:
        """Strictly reopen a completed session without reconciliation or writes."""
        try:
            first_events = self.store.read_events()
            session = self.store.load(session_ref, PersonalValidationSession)
            events = session_events(first_events, session.session_id)
            start = require_start_event(events, session_ref)
            completions = tuple(
                event for event in events if event.operation == "attempt-completed"
            )
            attempts = tuple(
                self.store.load(event.object_ref, PersonalValidationAttempt)
                for event in completions
            )
            require_attempt_closure(session_ref, session, start, completions, attempts)
            completed_refs = tuple(event.object_ref for event in completions)
            orphans = tuple(
                reference
                for reference, attempt in self.store.attempt_objects()
                if attempt.start_event_id == start.event_id
                and reference not in completed_refs
            )
            if orphans or not completions:
                raise PersonalValidationIntegrityInvalid(
                    "Personal attempt has no exact completion event",
                    finding_kind="attempt-event-incomplete",
                )
            if self.store.read_events() != first_events:
                raise PersonalValidationIntegrityInvalid(
                    "Personal event history changed during status",
                    finding_kind="event-history-changed",
                )
            return PersonalValidationStatus(
                session_ref=session_ref,
                session=session,
                attempt_refs=completed_refs,
                attempts=attempts,
                completion_event_ids=tuple(event.event_id for event in completions),
            )
        except PersonalValidationError:
            raise
        except (OSError, TypeError, ValueError, ValidationError) as error:
            raise PersonalValidationIntegrityInvalid(
                "Personal session status is invalid",
                finding_kind="session-status-invalid",
            ) from error

    def _prepare(
        self,
        protocol_ref: ArtifactRef,
        case_ref: ArtifactRef,
        target: CompletedFactoryRunTarget | CorrectStopTarget,
        *,
        predecessor_attempt_ref: ArtifactRef | None,
    ) -> PreparedAttempt:
        protocol, case, _system, _inventory = self._reopen_inputs(
            protocol_ref, case_ref
        )
        require_target_kind(case, target)
        session = personal_session(self.session_nonce, protocol_ref, protocol)
        expected_session_ref = model_ref(session.session_id, session)
        try:
            with self.store.session_lock(session.session_id):
                history = self.store._writer_events()
                session_ref = self.store.publish(session.session_id, session)
                if session_ref != expected_session_ref:
                    raise PersonalValidationIntegrityInvalid(
                        "Personal session ref is not canonical",
                        finding_kind="session-object-invalid",
                    )
                start = self.store._append_event_locked(
                    session_id=session.session_id,
                    operation="session-started",
                    object_ref=session_ref,
                    expected_sequence=1,
                )
                history = self.store._writer_events()
                attempt = personal_attempt(
                    protocol_ref=protocol_ref,
                    case_ref=case_ref,
                    input_snapshot_ref=case.input_snapshot_ref,
                    system_snapshot_ref=self.system_snapshot_ref,
                    inventory_ref=self.attempt_inventory_ref,
                    target=target,
                    start_event_id=start.event_id,
                    predecessor_attempt_ref=predecessor_attempt_ref,
                )
                expected_attempt_ref = model_ref(attempt.attempt_id, attempt)
                prior = self._prior_case_attempt(
                    history.events,
                    session,
                    case_ref,
                    start,
                    expected_attempt_ref,
                    predecessor_attempt_ref,
                )
                if prior is not None:
                    if history.recovery_event_id is not None:
                        if history.recovery_event_id != prior.completion_event_id:
                            raise PersonalValidationIntegrityInvalid(
                                "pending event recovery differs from exact retry",
                                finding_kind="event-recovery-not-exact",
                            )
                        self.store._recover_event_head_locked(prior.completion_event_id)
                    return prior
                if history.recovery_event_id is not None:
                    raise PersonalValidationIntegrityInvalid(
                        "pending event recovery differs from requested attempt",
                        finding_kind="event-recovery-not-exact",
                    )
                self._require_orphan_exact(history.events, start, expected_attempt_ref)
                attempt_ref = self.store.publish(attempt.attempt_id, attempt)
                self._fail("attempt-object")
                sequence = len(session_events(history.events, session.session_id)) + 1
                completed = self.store._append_event_locked(
                    session_id=session.session_id,
                    operation="attempt-completed",
                    object_ref=attempt_ref,
                    expected_sequence=sequence,
                )
                self._fail("completion-event")
                return PreparedAttempt(session_ref, attempt_ref, completed.event_id)
        except PersonalValidationError:
            raise
        except (OSError, TypeError, ValueError, ValidationError) as error:
            raise PersonalValidationIntegrityInvalid(
                "Personal attempt preparation failed",
                finding_kind="attempt-preparation-invalid",
            ) from error

    def _reopen_inputs(
        self, protocol_ref: ArtifactRef, case_ref: ArtifactRef
    ) -> tuple[
        PersonalValidationProtocol,
        PersonalCanonicalCase,
        SystemSnapshot,
        AttemptRootInventory,
    ]:
        protocol = self.store.load(protocol_ref, PersonalValidationProtocol)
        case = self.store.load(case_ref, PersonalCanonicalCase)
        binding = tuple(item for item in protocol.cases if item.case_ref == case_ref)
        if len(binding) != 1 or binding[0].kind != case.kind:
            raise PersonalValidationIntegrityInvalid(
                "case is not exactly bound by the protocol",
                finding_kind="protocol-case-binding-invalid",
            )
        self.store.load(case.input_snapshot_ref, InputSnapshot)
        system = self.store.load(self.system_snapshot_ref, SystemSnapshot)
        inventory = self.store.load(self.attempt_inventory_ref, AttemptRootInventory)
        roots = tuple(item.logical_root for item in inventory.root_identities)
        if system.protocol_ref != protocol_ref or roots != case.required_logical_roots:
            raise PersonalValidationIntegrityInvalid(
                "attempt snapshots differ from protocol or case authority",
                finding_kind="attempt-snapshot-binding-invalid",
            )
        if set(roots) != set(PERSONAL_ATTEMPT_ROOTS_V1):
            raise PersonalValidationIntegrityInvalid(
                "attempt inventory is incomplete",
                finding_kind="attempt-root-inventory-incomplete",
            )
        return protocol, case, system, inventory

    def _prior_case_attempt(
        self,
        history: tuple[PersonalValidationEvent, ...],
        session: PersonalValidationSession,
        case_ref: ArtifactRef,
        start: PersonalValidationEvent,
        expected_ref: ArtifactRef,
        predecessor_attempt_ref: ArtifactRef | None,
    ) -> PreparedAttempt | None:
        completions = tuple(
            event
            for event in session_events(history, session.session_id)
            if event.operation == "attempt-completed"
        )
        case_attempts: list[
            tuple[PersonalValidationEvent, PersonalValidationAttempt]
        ] = []
        for event in completions:
            attempt = self.store.load(event.object_ref, PersonalValidationAttempt)
            if attempt.case_ref != case_ref:
                continue
            if attempt.start_event_id != start.event_id:
                raise PersonalValidationIntegrityInvalid(
                    "completed attempt differs from the session start",
                    finding_kind="attempt-retry-divergent",
                )
            prior_ref = case_attempts[-1][0].object_ref if case_attempts else None
            if attempt.predecessor_attempt_ref != prior_ref:
                raise PersonalValidationIntegrityInvalid(
                    "completed attempt lineage is invalid",
                    finding_kind="attempt-retry-divergent",
                )
            case_attempts.append((event, attempt))
        for event, _attempt in case_attempts:
            if event.object_ref == expected_ref:
                return PreparedAttempt(
                    session_ref=model_ref(session.session_id, session),
                    attempt_ref=event.object_ref,
                    completion_event_id=event.event_id,
                )
        latest_ref = case_attempts[-1][0].object_ref if case_attempts else None
        if latest_ref != predecessor_attempt_ref:
            raise PersonalValidationIntegrityInvalid(
                "attempt retry differs from the current case lineage",
                finding_kind="attempt-retry-divergent",
            )
        return None

    def _require_orphan_exact(
        self,
        history: tuple[PersonalValidationEvent, ...],
        start: PersonalValidationEvent,
        expected_ref: ArtifactRef,
    ) -> None:
        completed_refs = {
            event.object_ref
            for event in history
            if event.operation == "attempt-completed"
        }
        orphans = tuple(
            reference
            for reference, attempt in self.store.attempt_objects()
            if attempt.start_event_id == start.event_id
            and reference not in completed_refs
        )
        if orphans and orphans != (expected_ref,):
            raise PersonalValidationIntegrityInvalid(
                "attempt retry differs from the orphaned object",
                finding_kind="attempt-retry-divergent",
            )

    def _fail(self, boundary: str) -> None:
        if self.failure_injector is not None:
            self.failure_injector(boundary)


__all__ = [
    "PersonalValidationService",
    "PersonalValidationStatus",
    "PreparedAttempt",
]
