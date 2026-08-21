from __future__ import annotations

import hashlib

from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation._strict import (
    AgentDispatchObservation,
    AgentDispatchReceipt,
    ReviewAssignment,
    ReviewRole,
    artifact_ref_key,
    canonical_json,
    materialize_id,
)
from envresearch.personal_validation.canonical_handoff import (
    require_allowlisted_findings,
)
from envresearch.personal_validation.errors import (
    PersonalValidationIntegrityInvalid,
)
from envresearch.personal_validation.evaluation import (
    attempt_authority,
    attempt_session,
)
from envresearch.personal_validation.events import (
    PersonalWriterHistory,
    session_events,
)
from envresearch.personal_validation.external_access import (
    ExternalAccessReceipt,
    authenticate_external_receipts,
    materialize_external_records,
    require_external_completion,
)
from envresearch.personal_validation.report import (
    LifecycleService,
    RecordedReview,
    authority_error,
    model_ref,
    objects,
    publish_event_object,
    require_receipt,
)
from envresearch.personal_validation.review_bundle import (
    parse_response,
    policy_for,
    reopen_bundle,
)
from envresearch.personal_validation.review_closure import (
    require_bundle_closure_safe,
    require_review_bytes_safe,
)
from envresearch.personal_validation.review_contracts import (
    AgentFindingResponse,
    AgentReview,
    AgentReviewResponse,
    ExternalAccessRecord,
    PersonalFinding,
    ReviewPublication,
)


class ReviewLifecycleMixin:
    def assign_review(
        self: LifecycleService,
        attempt_ref: ArtifactRef,
        bundle_ref: ArtifactRef,
        *,
        role: ReviewRole,
        invocation_id: str,
    ) -> ArtifactRef:
        session_id = attempt_session(self.store, attempt_ref)
        with self.store.session_lock(session_id):
            history, _attempt, _case, protocol = attempt_authority(
                self.store, session_id, attempt_ref
            )
            bundle = reopen_bundle(
                self.store, history.events, attempt_ref, bundle_ref, role
            )
            payload: dict[str, object] = {
                "schema_version": "personal.review-assignment.v1",
                "attempt_ref": attempt_ref,
                "bundle_ref": bundle_ref,
                "role": role,
                "policy_sha256": policy_for(protocol, role),
                "invocation_id": invocation_id,
                "primary_publication_refs": bundle.primary_publication_refs,
            }
            payload["assignment_id"] = materialize_id(
                "personal-review-assignment-", payload
            )
            assignment = ReviewAssignment.model_validate(payload)
            existing = objects(
                self.store, "personal-review-assignment-", ReviewAssignment
            )
            for reference, prior in existing:
                if prior == assignment:
                    return reference
                if (
                    prior.attempt_ref == attempt_ref
                    and prior.role == role
                    or prior.invocation_id == invocation_id
                ):
                    raise authority_error("review assignment is duplicate or replayed")
            return self.store.publish(assignment.assignment_id, assignment)

    def record_dispatch(
        self: LifecycleService, assignment_ref: ArtifactRef, raw: bytes
    ) -> ArtifactRef:
        assignment = self.store.load(assignment_ref, ReviewAssignment)
        session_id = attempt_session(self.store, assignment.attempt_ref)
        try:
            observed = AgentDispatchObservation.model_validate_json(raw)
            if raw != canonical_json(observed.model_dump(mode="json")):
                raise ValueError("dispatch observation bytes are noncanonical")
        except (TypeError, ValueError, ValidationError) as error:
            raise PersonalValidationIntegrityInvalid(
                "agent dispatch observation is invalid",
                finding_kind="dispatch-observation-invalid",
            ) from error
        with self.store.session_lock(session_id):
            history, attempt, case, protocol = attempt_authority(
                self.store, session_id, assignment.attempt_ref
            )
            reopened = self.store.load(assignment_ref, ReviewAssignment)
            bundle = reopen_bundle(
                self.store,
                history.events,
                reopened.attempt_ref,
                reopened.bundle_ref,
                reopened.role,
            )
            require_bundle_closure_safe(self.store, case, attempt, bundle)
            if (
                reopened != assignment
                or observed.invocation_id != assignment.invocation_id
                or assignment.policy_sha256 != policy_for(protocol, assignment.role)
                or assignment.primary_publication_refs
                != bundle.primary_publication_refs
            ):
                raise authority_error(
                    "dispatch assignment policy or invocation differs"
                )
            receipt = AgentDispatchReceipt(
                schema_version="personal.agent-dispatch-receipt.v1",
                assignment_ref=assignment_ref,
                invocation_id=observed.invocation_id,
                observed_model_id=observed.observed_model_id,
                observed_runtime_id=observed.observed_runtime_id,
                dispatched_at=observed.dispatched_at,
            )
            identity = (
                "personal-agent-dispatch-receipt-"
                + hashlib.sha256(
                    canonical_json(receipt.model_dump(mode="json"))
                ).hexdigest()
            )
            event = publish_event_object(
                self,
                history,
                session_id,
                "dispatch-recorded",
                identity,
                receipt,
                slot=lambda item: (
                    item.assignment_ref == assignment_ref
                    or item.invocation_id == assignment.invocation_id
                ),
                boundary="dispatch",
            )
            return self.store.require_event(event).object_ref

    def record_review(
        self: LifecycleService,
        assignment_ref: ArtifactRef,
        dispatch_receipt_ref: ArtifactRef,
        raw: bytes,
        *,
        external_access_receipt_refs: tuple[ArtifactRef, ...] = (),
    ) -> RecordedReview:
        assignment = self.store.load(assignment_ref, ReviewAssignment)
        session_id = attempt_session(self.store, assignment.attempt_ref)
        response = parse_response(raw)
        if response.role != assignment.role:
            raise authority_error("agent response role differs from assignment")
        with self.store.session_lock(session_id):
            history, attempt, case, protocol = attempt_authority(
                self.store, session_id, assignment.attempt_ref
            )
            bundle = reopen_bundle(
                self.store,
                history.events,
                assignment.attempt_ref,
                assignment.bundle_ref,
                assignment.role,
            )
            if (
                assignment.policy_sha256 != policy_for(protocol, assignment.role)
                or assignment.primary_publication_refs
                != bundle.primary_publication_refs
            ):
                raise authority_error("review assignment policy differs")
            require_receipt(
                self.store,
                history.events,
                dispatch_receipt_ref,
                assignment_ref,
                assignment,
            )
            require_bundle_closure_safe(self.store, case, attempt, bundle)
            require_review_bytes_safe(self.store, case, raw)
            require_allowlisted_findings(self.store, bundle, response)
            access_receipts = authenticate_external_receipts(
                self.store,
                history.events,
                assignment_ref,
                response.external_access_requests,
                external_access_receipt_refs,
            )
            require_external_completion(
                response.external_access_requests,
                access_receipts,
                response.completion_status,
            )
            review, findings, accesses, publication = _materialize_review(
                assignment_ref,
                assignment,
                dispatch_receipt_ref,
                raw,
                response,
                external_access_receipt_refs,
                access_receipts,
            )
            return _publish_review_closure(
                self,
                history,
                session_id,
                assignment,
                review,
                findings,
                accesses,
                publication,
            )


def _materialize_review(
    assignment_ref: ArtifactRef,
    assignment: ReviewAssignment,
    receipt_ref: ArtifactRef,
    raw: bytes,
    response: AgentReviewResponse,
    access_receipt_refs: tuple[ArtifactRef, ...],
    access_receipts: tuple[ExternalAccessReceipt, ...],
) -> tuple[
    AgentReview,
    tuple[PersonalFinding, ...],
    tuple[tuple[str, ExternalAccessRecord], ...],
    ReviewPublication,
]:
    review_payload: dict[str, object] = {
        "schema_version": "personal.agent-review.v1",
        "assignment_ref": assignment_ref,
        "attempt_ref": assignment.attempt_ref,
        "bundle_ref": assignment.bundle_ref,
        "role": assignment.role,
        "policy_sha256": assignment.policy_sha256,
        "dispatch_receipt_ref": receipt_ref,
        "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
        "response": response,
    }
    review_payload["review_id"] = materialize_id("personal-review-", review_payload)
    review = AgentReview.model_validate(review_payload)
    review_ref = model_ref(review.review_id, review)
    findings = tuple(
        _materialize_finding(item, review_ref) for item in response.findings
    )
    by_key = {
        raw_finding.local_finding_key: model_ref(finding.finding_id, finding)
        for raw_finding, finding in zip(response.findings, findings, strict=True)
    }
    accesses = materialize_external_records(
        review_ref,
        by_key,
        response.external_access_requests,
        access_receipt_refs,
        access_receipts,
    )
    publication_payload: dict[str, object] = {
        "schema_version": "personal.review-publication.v1",
        "assignment_ref": assignment_ref,
        "review_ref": review_ref,
        "finding_refs": tuple(
            sorted(
                (model_ref(item.finding_id, item) for item in findings),
                key=artifact_ref_key,
            )
        ),
        "external_access_record_refs": tuple(
            sorted(
                (model_ref(identity, item) for identity, item in accesses),
                key=artifact_ref_key,
            )
        ),
    }
    publication_payload["publication_id"] = materialize_id(
        "personal-review-publication-", publication_payload
    )
    return (
        review,
        findings,
        tuple(
            (identity, ExternalAccessRecord.model_validate(item))
            for identity, item in accesses
        ),
        ReviewPublication.model_validate(publication_payload),
    )


def _materialize_finding(
    raw: AgentFindingResponse, review_ref: ArtifactRef
) -> PersonalFinding:
    payload = raw.model_dump(mode="python", exclude={"local_finding_key"})
    payload["source_review_refs"] = (review_ref,)
    payload["finding_id"] = materialize_id("personal-finding-", payload)
    return PersonalFinding.model_validate(payload)


def _publish_review_closure(
    service: LifecycleService,
    history: PersonalWriterHistory,
    session_id: str,
    assignment: ReviewAssignment,
    review: AgentReview,
    findings: tuple[PersonalFinding, ...],
    accesses: tuple[tuple[str, ExternalAccessRecord], ...],
    publication: ReviewPublication,
) -> RecordedReview:
    publication_ref = model_ref(publication.publication_id, publication)
    prior_events = tuple(
        event
        for event in session_events(history.events, session_id)
        if event.operation == "review-published"
        and service.store.load(event.object_ref, ReviewPublication).assignment_ref
        == publication.assignment_ref
    )
    if prior_events:
        if len(prior_events) != 1 or prior_events[0].object_ref != publication_ref:
            raise authority_error("assignment already has a different review")
        if history.recovery_event_id is not None:
            if history.recovery_event_id != prior_events[0].event_id:
                raise PersonalValidationIntegrityInvalid(
                    "pending review recovery differs from exact retry",
                    finding_kind="event-recovery-not-exact",
                )
            service.store._recover_event_head_locked(prior_events[0].event_id)
        return _recorded(publication, prior_events[0].event_id)
    review_ref = model_ref(review.review_id, review)
    conflicting = tuple(
        item
        for _reference, item in objects(service.store, "personal-review-", AgentReview)
        if item.assignment_ref == publication.assignment_ref and item != review
    )
    if conflicting:
        raise authority_error("assignment has a divergent orphaned review")
    service.store.publish(review.review_id, review)
    service._fail("review-object")
    for finding in findings:
        service.store.publish(finding.finding_id, finding)
    for identity, access in accesses:
        service.store.publish(identity, access)
    event_id = publish_event_object(
        service,
        history,
        session_id,
        "review-published",
        publication.publication_id,
        publication,
        slot=lambda item: item.assignment_ref == publication.assignment_ref,
        boundary="publication",
    )
    return RecordedReview(
        publication_ref,
        review_ref,
        publication.finding_refs,
        publication.external_access_record_refs,
        event_id,
    )


def _recorded(publication: ReviewPublication, event_id: str) -> RecordedReview:
    return RecordedReview(
        model_ref(publication.publication_id, publication),
        publication.review_ref,
        publication.finding_refs,
        publication.external_access_record_refs,
        event_id,
    )
