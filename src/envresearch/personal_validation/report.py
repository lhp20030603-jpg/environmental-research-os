"""Independent evaluation and immutable advisory report publication."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation import _strict as strict
from envresearch.personal_validation import review_contracts as review
from envresearch.personal_validation.errors import (
    PersonalValidationAuthorityInvalid,
    PersonalValidationIntegrityInvalid,
)
from envresearch.personal_validation.evaluation import (
    attempt_authority,
    attempt_session,
    derive_evaluation,
    matching_events,
)
from envresearch.personal_validation.events import (
    PersonalValidationEvent,
    PersonalWriterHistory,
    session_events,
)
from envresearch.personal_validation.external_access import (
    ExternalAccessDispatch,
    ExternalAccessReceipt,
)
from envresearch.personal_validation.private_store import PersonalValidationStore
from envresearch.personal_validation.repair_contracts import ReviewPublicationBinding

ModelT = TypeVar("ModelT", bound=BaseModel)
_OBJECT = re.compile(r"^v([1-9][0-9]*)-([0-9a-f]{64})\.json$")


@dataclass(frozen=True, slots=True)
class RecordedReview:
    publication_ref: ArtifactRef
    review_ref: ArtifactRef
    finding_refs: tuple[ArtifactRef, ...]
    access_record_refs: tuple[ArtifactRef, ...]
    completion_event_id: str


@dataclass(frozen=True, slots=True)
class FinalizedReport:
    report_ref: ArtifactRef
    report: review.PersonalValidationReport
    completion_event_id: str


class LifecycleService(Protocol):
    store: PersonalValidationStore

    def _fail(self, boundary: str) -> None: ...


def authority_error(message: str) -> PersonalValidationAuthorityInvalid:
    return PersonalValidationAuthorityInvalid(
        message, finding_kind="review-authority-invalid"
    )


def integrity_error(
    message: str, finding_kind: str
) -> PersonalValidationIntegrityInvalid:
    return PersonalValidationIntegrityInvalid(message, finding_kind=finding_kind)


def model_ref(identity: str, model: BaseModel) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=identity,
        artifact_version=1,
        content_hash=hashlib.sha256(model.model_dump_json().encode()).hexdigest(),
    )


def objects(
    store: PersonalValidationStore, prefix: str, model: type[ModelT]
) -> tuple[tuple[ArtifactRef, ModelT], ...]:
    found: list[tuple[ArtifactRef, ModelT]] = []
    identity_pattern = re.compile(rf"{re.escape(prefix)}[0-9a-f]{{64}}")
    for identity in store.objects.list_directory(Path("exit/objects")):
        if identity_pattern.fullmatch(identity) is None:
            continue
        parent = Path("exit/objects") / identity
        for name in store.objects.list_directory(parent):
            match = _OBJECT.fullmatch(name)
            if match is None:
                raise integrity_error(
                    "review object filename is invalid", "personal-object-invalid"
                )
            reference = ArtifactRef(
                artifact_id=identity,
                artifact_version=int(match.group(1)),
                content_hash=match.group(2),
            )
            found.append((reference, store.load(reference, model)))
    return tuple(sorted(found, key=lambda item: strict.artifact_ref_key(item[0])))


def publish_event_object(
    service: LifecycleService,
    history: PersonalWriterHistory,
    session_id: str,
    operation: str,
    identity: str,
    model: ModelT,
    *,
    slot: Callable[[ModelT], bool],
    boundary: str,
) -> str:
    reference = model_ref(identity, model)
    completed = tuple(
        event
        for event in session_events(history.events, session_id)
        if event.operation == operation
    )
    event_slots = tuple(
        event
        for event in completed
        if slot(service.store.load(event.object_ref, type(model)))
    )
    if event_slots:
        if len(event_slots) != 1 or event_slots[0].object_ref != reference:
            raise authority_error(f"{boundary} event slot was divergently reused")
        if history.recovery_event_id is not None:
            if history.recovery_event_id != event_slots[0].event_id:
                raise integrity_error(
                    "pending review recovery differs from exact retry",
                    "event-recovery-not-exact",
                )
            service.store._recover_event_head_locked(event_slots[0].event_id)
        return event_slots[0].event_id
    prefix = identity.rsplit("-", 1)[0] + "-"
    completed_refs = {event.object_ref for event in completed}
    orphans = tuple(
        reference
        for reference, item in objects(service.store, prefix, type(model))
        if slot(item) and reference not in completed_refs
    )
    if orphans and orphans != (reference,):
        raise integrity_error(
            f"{boundary} retry differs from orphaned object",
            "review-retry-divergent",
        )
    if history.recovery_event_id is not None:
        raise integrity_error(
            "pending review recovery differs from requested object",
            "event-recovery-not-exact",
        )
    if service.store.publish(identity, model) != reference:
        raise integrity_error(
            f"{boundary} object is noncanonical",
            "personal-object-publication-invalid",
        )
    service._fail(f"{boundary}-object")
    event = service.store._append_event_locked(
        session_id=session_id,
        operation=operation,
        object_ref=reference,
        expected_sequence=len(session_events(history.events, session_id)) + 1,
    )
    service._fail(f"{boundary}-event")
    return event.event_id


def require_receipt(
    store: PersonalValidationStore,
    events: tuple[PersonalValidationEvent, ...],
    receipt_ref: ArtifactRef,
    assignment_ref: ArtifactRef,
    assignment: strict.ReviewAssignment,
) -> strict.AgentDispatchReceipt:
    receipt = store.load(receipt_ref, strict.AgentDispatchReceipt)
    matches = matching_events(events, "dispatch-recorded", receipt_ref)
    if (
        len(matches) != 1
        or receipt.assignment_ref != assignment_ref
        or receipt.invocation_id != assignment.invocation_id
    ):
        raise authority_error("dispatch receipt differs from exact assignment")
    return receipt


class ReportLifecycleMixin:
    def evaluate_case(self: LifecycleService, attempt_ref: ArtifactRef) -> ArtifactRef:
        session_id = attempt_session(self.store, attempt_ref)
        with self.store.session_lock(session_id):
            history, attempt, case, _protocol = attempt_authority(
                self.store, session_id, attempt_ref
            )
            evaluation = derive_evaluation(self.store, attempt_ref, attempt, case)
            event_id = publish_event_object(
                self,
                history,
                session_id,
                "evaluation-published",
                evaluation.evaluation_id,
                evaluation,
                slot=lambda item: item.attempt_ref == attempt_ref,
                boundary="evaluation",
            )
            return self.store.require_event(event_id).object_ref

    def finalize_report(
        self: LifecycleService,
        attempt_ref: ArtifactRef,
        evaluation_ref: ArtifactRef,
        scientific: ArtifactRef | RecordedReview,
        evidence: ArtifactRef | RecordedReview,
        synthesis: ArtifactRef | RecordedReview,
    ) -> FinalizedReport:
        session_id = attempt_session(self.store, attempt_ref)
        with self.store.session_lock(session_id):
            history, attempt, case, protocol = attempt_authority(
                self.store, session_id, attempt_ref
            )
            events = session_events(history.events, session_id)
            recomputed = derive_evaluation(self.store, attempt_ref, attempt, case)
            if (
                model_ref(recomputed.evaluation_id, recomputed) != evaluation_ref
                or self.store.load(evaluation_ref, review.CaseBehaviorEvaluation)
                != recomputed
                or not any(
                    event.operation == "evaluation-published"
                    and event.object_ref == evaluation_ref
                    for event in events
                )
            ):
                raise PersonalValidationIntegrityInvalid(
                    "persisted case evaluation differs from recomputation",
                    finding_kind="evaluation-recomputation-invalid",
                )
            refs = tuple(
                _publication_ref(item) for item in (scientific, evidence, synthesis)
            )
            publications = tuple(
                _authenticate_publication(self.store, events, ref) for ref in refs
            )
            roles: tuple[Literal["scientific", "evidence", "synthesis"], ...] = (
                "scientific",
                "evidence",
                "synthesis",
            )
            if tuple(item.assignment.role for item in publications) != roles or any(
                item.assignment.policy_sha256
                != getattr(protocol, f"{role}_policy_sha256")
                for role, item in zip(roles, publications, strict=True)
            ):
                raise authority_error("report publication role order is invalid")
            if publications[2].assignment.primary_publication_refs != tuple(
                sorted(refs[:2], key=strict.artifact_ref_key)
            ):
                raise authority_error(
                    "synthesis publications differ from primary inputs"
                )
            findings = tuple(
                sorted(
                    {
                        ref
                        for item in publications
                        for ref in item.publication.finding_refs
                    },
                    key=strict.artifact_ref_key,
                )
            )
            payload: dict[str, object] = {
                "schema_version": "personal.validation-report.v1",
                "attempt_ref": attempt_ref,
                "evaluation_ref": evaluation_ref,
                "review_publication_refs": tuple(
                    ReviewPublicationBinding(role=role, publication_ref=ref)
                    for role, ref in zip(roles, refs, strict=True)
                ),
                "finding_refs": findings,
                "state": review.reduce_case_state(recomputed, publications),
                "scope": protocol.scope,
                "blocks": protocol.blocks,
                "hidden_evaluation_status": protocol.hidden_evaluation_status,
                "product_release_status": protocol.product_release_status,
            }
            payload["report_id"] = strict.materialize_id("personal-report-", payload)
            report = review.PersonalValidationReport.model_validate(payload)
            event_id = publish_event_object(
                self,
                history,
                session_id,
                "report-published",
                report.report_id,
                report,
                slot=lambda item: item.attempt_ref == attempt_ref,
                boundary="report",
            )
            return FinalizedReport(
                model_ref(report.report_id, report), report, event_id
            )


def _publication_ref(value: ArtifactRef | RecordedReview) -> ArtifactRef:
    return value if isinstance(value, ArtifactRef) else value.publication_ref


def _authenticate_publication(
    store: PersonalValidationStore,
    events: tuple[PersonalValidationEvent, ...],
    reference: ArtifactRef,
) -> review.AuthenticatedReviewPublication:
    publication = store.load(reference, review.ReviewPublication)
    agent_review = store.load(publication.review_ref, review.AgentReview)
    if (
        len(matching_events(events, "review-published", reference)) != 1
        or len(
            matching_events(
                events, "dispatch-recorded", agent_review.dispatch_receipt_ref
            )
        )
        != 1
    ):
        raise authority_error("review publication closure event is invalid")
    return review.AuthenticatedReviewPublication(
        assignment=store.load(publication.assignment_ref, strict.ReviewAssignment),
        dispatch_receipt=store.load(
            agent_review.dispatch_receipt_ref, strict.AgentDispatchReceipt
        ),
        publication=publication,
        review=agent_review,
        findings=tuple(
            store.load(ref, review.PersonalFinding) for ref in publication.finding_refs
        ),
        external_access_records=tuple(
            _authenticate_access_record(store, events, ref)
            for ref in publication.external_access_record_refs
        ),
    )


def _authenticate_access_record(
    store: PersonalValidationStore,
    events: tuple[PersonalValidationEvent, ...],
    reference: ArtifactRef,
) -> review.AuthenticatedExternalAccessRecord:
    record = store.load(reference, review.ExternalAccessRecord)
    receipt = store.load(record.receipt_ref, ExternalAccessReceipt)
    dispatch = store.load(receipt.dispatch_ref, ExternalAccessDispatch)
    if (
        len(matching_events(events, "external-access-dispatched", receipt.dispatch_ref))
        != 1
        or len(matching_events(events, "external-access-received", record.receipt_ref))
        != 1
        or dispatch.assignment_ref != receipt.assignment_ref
        or dispatch.request != receipt.request
        or dispatch.authorization_ref != receipt.authorization_ref
        or dispatch.policy_ref != receipt.policy_ref
        or dispatch.request_sha256 != receipt.request_sha256
    ):
        raise authority_error("external access receipt closure event is invalid")
    return review.AuthenticatedExternalAccessRecord(
        record_ref=reference,
        record=record,
        receipt=receipt,
    )
