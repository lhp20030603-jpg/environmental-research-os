from __future__ import annotations

import hashlib
import posixpath
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import AwareDatetime, BaseModel, BeforeValidator, model_validator

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation._strict import (
    STRICT,
    Sha256,
    StrictArtifactRef,
    materialize_id,
    model_payload,
    require_materialized_id,
    require_nonblank,
    require_sorted_unique_strings,
    strict_model_input,
)
from envresearch.personal_validation.events import PersonalValidationEvent
from envresearch.personal_validation.private_store import PersonalValidationStore

Provider = Literal["web", "zotero"]
Operation = Literal[
    "read-citation-metadata",
    "read-authorized-paper",
    "read-official-documentation",
    "read-data-license-provenance",
]

_OPERATIONS = {
    "web": {"read-official-documentation", "read-data-license-provenance"},
    "zotero": {"read-citation-metadata", "read-authorized-paper"},
}


class ExternalAccessRequest(BaseModel):
    model_config = STRICT
    provider: Provider
    operation: Operation
    source_locator: str
    local_finding_keys: tuple[str, ...]

    def dispatch_slot(self) -> tuple[str, str, str]:
        return self.provider, self.operation, self.source_locator

    @model_validator(mode="after")
    def require_canonical_request(self) -> ExternalAccessRequest:
        normalized = normalize_external_locator(
            self.provider, self.operation, require_nonblank(self.source_locator)
        )
        object.__setattr__(self, "source_locator", normalized)
        require_sorted_unique_strings(
            self.local_finding_keys, field="local finding keys"
        )
        return self


StrictExternalRequest = Annotated[
    ExternalAccessRequest, BeforeValidator(strict_model_input)
]


class ExternalAccessDispatch(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.external-access-dispatch.v1"]
    dispatch_id: str
    assignment_ref: StrictArtifactRef
    request: StrictExternalRequest
    authorization_ref: StrictArtifactRef
    authorization_sha256: Sha256
    policy_ref: StrictArtifactRef
    policy_sha256: Sha256
    request_sha256: Sha256

    @model_validator(mode="after")
    def require_exact_dispatch(self) -> ExternalAccessDispatch:
        if (
            self.authorization_sha256 != self.authorization_ref.content_hash
            or self.policy_sha256 != self.policy_ref.content_hash
        ):
            raise ValueError("external dispatch auth or policy digest differs")
        require_materialized_id(
            self.dispatch_id,
            "personal-external-dispatch-",
            model_payload(self, exclude="dispatch_id"),
        )
        return self


class ExternalAccessReceipt(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.external-access-receipt.v1"]
    receipt_id: str
    dispatch_ref: StrictArtifactRef
    assignment_ref: StrictArtifactRef
    request: StrictExternalRequest
    authorization_ref: StrictArtifactRef
    authorization_sha256: Sha256
    policy_ref: StrictArtifactRef
    policy_sha256: Sha256
    request_sha256: Sha256
    response_sha256: Sha256
    outcome: Literal["success", "failed"]
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def require_exact_receipt(self) -> ExternalAccessReceipt:
        if (
            self.authorization_sha256 != self.authorization_ref.content_hash
            or self.policy_sha256 != self.policy_ref.content_hash
        ):
            raise ValueError("external receipt auth or policy digest differs")
        require_materialized_id(
            self.receipt_id,
            "personal-external-receipt-",
            model_payload(self, exclude="receipt_id"),
        )
        return self


def normalize_external_locator(
    provider: Provider, operation: Operation, locator: str
) -> str:
    if operation not in _OPERATIONS[provider]:
        raise ValueError("external operation is unavailable for provider")
    parsed = urlsplit(locator)
    scheme = parsed.scheme.casefold()
    expected_scheme = "https" if provider == "web" else "zotero"
    if scheme != expected_scheme or not parsed.hostname or parsed.username:
        raise ValueError("external locator scheme differs from provider")
    if parsed.password is not None:
        raise ValueError("external locator credentials are forbidden")
    host = parsed.hostname.casefold()
    if parsed.port is not None and not (scheme == "https" and parsed.port == 443):
        host = f"{host}:{parsed.port}"
    normalized_path = posixpath.normpath(parsed.path or "/")
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunsplit((scheme, host, normalized_path, query, ""))


class ExternalAccessLifecycleMixin:
    store: PersonalValidationStore

    def _fail(self, boundary: str) -> None: ...

    def record_external_access_dispatch(
        self,
        assignment_ref: ArtifactRef,
        authorization_ref: ArtifactRef,
        raw_request: bytes,
    ) -> ArtifactRef:
        from envresearch.personal_validation._strict import (
            ReviewAssignment,
            canonical_json,
        )
        from envresearch.personal_validation.canonical_handoff import reopen_policy
        from envresearch.personal_validation.evaluation import (
            attempt_authority,
            attempt_session,
        )
        from envresearch.personal_validation.report import (
            authority_error,
            publish_event_object,
        )
        from envresearch.personal_validation.review_bundle import reopen_bundle
        from envresearch.personal_validation.review_closure import (
            require_review_bytes_safe,
        )

        try:
            request = ExternalAccessRequest.model_validate_json(raw_request)
            if raw_request != canonical_json(request.model_dump(mode="json")):
                raise ValueError("external access request bytes are noncanonical")
        except ValueError as error:
            raise authority_error("external access request is invalid") from error
        assignment = self.store.load(assignment_ref, ReviewAssignment)
        session_id = attempt_session(self.store, assignment.attempt_ref)
        with self.store.session_lock(session_id):
            history, _attempt, case, protocol = attempt_authority(
                self.store, session_id, assignment.attempt_ref
            )
            reopened = self.store.load(assignment_ref, ReviewAssignment)
            reopen_bundle(
                self.store,
                history.events,
                assignment.attempt_ref,
                assignment.bundle_ref,
                assignment.role,
            )
            if reopened != assignment or authorization_ref != assignment_ref:
                raise authority_error("external access authorization differs")
            require_review_bytes_safe(self.store, case, raw_request)
            policy_ref, _policy = reopen_policy(
                self.store, "external-access", protocol.external_access_policy_sha256
            )
            payload: dict[str, object] = {
                "schema_version": "personal.external-access-dispatch.v1",
                "assignment_ref": assignment_ref,
                "request": request,
                "authorization_ref": authorization_ref,
                "authorization_sha256": authorization_ref.content_hash,
                "policy_ref": policy_ref,
                "policy_sha256": policy_ref.content_hash,
                "request_sha256": hashlib.sha256(raw_request).hexdigest(),
            }
            payload["dispatch_id"] = materialize_id(
                "personal-external-dispatch-", payload
            )
            dispatch = ExternalAccessDispatch.model_validate(payload)
            event_id = publish_event_object(
                self,
                history,
                session_id,
                "external-access-dispatched",
                dispatch.dispatch_id,
                dispatch,
                slot=lambda item: (
                    item.assignment_ref == assignment_ref
                    and item.request.dispatch_slot() == request.dispatch_slot()
                ),
                boundary="external-dispatch",
            )
            return self.store.require_event(event_id).object_ref

    def record_external_access_receipt(
        self,
        dispatch_ref: ArtifactRef,
        raw_response: bytes,
        *,
        outcome: Literal["success", "failed"],
        observed_at: datetime,
    ) -> ArtifactRef:
        from envresearch.personal_validation._strict import ReviewAssignment
        from envresearch.personal_validation.canonical_handoff import reopen_policy
        from envresearch.personal_validation.evaluation import (
            attempt_authority,
            attempt_session,
            matching_events,
        )
        from envresearch.personal_validation.report import (
            authority_error,
            publish_event_object,
        )

        dispatch = self.store.load(dispatch_ref, ExternalAccessDispatch)
        assignment = self.store.load(dispatch.assignment_ref, ReviewAssignment)
        session_id = attempt_session(self.store, assignment.attempt_ref)
        with self.store.session_lock(session_id):
            history, _attempt, _case, protocol = attempt_authority(
                self.store, session_id, assignment.attempt_ref
            )
            if (
                len(
                    matching_events(
                        history.events, "external-access-dispatched", dispatch_ref
                    )
                )
                != 1
            ):
                raise authority_error("external access dispatch event is invalid")
            policy_ref, _policy = reopen_policy(
                self.store, "external-access", protocol.external_access_policy_sha256
            )
            if (
                self.store.load(dispatch_ref, ExternalAccessDispatch) != dispatch
                or policy_ref != dispatch.policy_ref
                or dispatch.authorization_ref != dispatch.assignment_ref
            ):
                raise authority_error("external access dispatch authority changed")
            payload: dict[str, object] = {
                "schema_version": "personal.external-access-receipt.v1",
                "dispatch_ref": dispatch_ref,
                "assignment_ref": dispatch.assignment_ref,
                "request": dispatch.request,
                "authorization_ref": dispatch.authorization_ref,
                "authorization_sha256": dispatch.authorization_sha256,
                "policy_ref": dispatch.policy_ref,
                "policy_sha256": dispatch.policy_sha256,
                "request_sha256": dispatch.request_sha256,
                "response_sha256": hashlib.sha256(raw_response).hexdigest(),
                "outcome": outcome,
                "observed_at": observed_at,
            }
            payload["receipt_id"] = materialize_id(
                "personal-external-receipt-", payload
            )
            receipt = ExternalAccessReceipt.model_validate(payload)
            event_id = publish_event_object(
                self,
                history,
                session_id,
                "external-access-received",
                receipt.receipt_id,
                receipt,
                slot=lambda item: item.dispatch_ref == dispatch_ref,
                boundary="external-receipt",
            )
            return self.store.require_event(event_id).object_ref


def authenticate_external_receipts(
    store: PersonalValidationStore,
    events: tuple[PersonalValidationEvent, ...],
    assignment_ref: ArtifactRef,
    requests: tuple[ExternalAccessRequest, ...],
    receipt_refs: tuple[ArtifactRef, ...],
) -> tuple[ExternalAccessReceipt, ...]:
    from envresearch.personal_validation.evaluation import matching_events
    from envresearch.personal_validation.report import authority_error

    if len(receipt_refs) != len(set(receipt_refs)):
        raise authority_error("external access receipt refs are duplicated")
    receipts = tuple(store.load(ref, ExternalAccessReceipt) for ref in receipt_refs)
    for reference, receipt in zip(receipt_refs, receipts, strict=True):
        dispatch = store.load(receipt.dispatch_ref, ExternalAccessDispatch)
        if (
            receipt.assignment_ref != assignment_ref
            or dispatch.assignment_ref != assignment_ref
            or dispatch.request != receipt.request
            or dispatch.authorization_ref != assignment_ref
            or len(
                matching_events(
                    events, "external-access-dispatched", receipt.dispatch_ref
                )
            )
            != 1
            or len(matching_events(events, "external-access-received", reference)) != 1
        ):
            raise authority_error("external access receipt closure is invalid")
    if any(receipt.request not in requests for receipt in receipts) or len(
        {receipt.request.model_dump_json() for receipt in receipts}
    ) != len(receipts):
        raise authority_error("external access receipt escaped declared requests")
    return receipts


def require_external_completion(
    requests: tuple[ExternalAccessRequest, ...],
    receipts: tuple[ExternalAccessReceipt, ...],
    completion_status: Literal["complete", "external-verification-pending"],
) -> None:
    successes = {
        item.request.model_dump_json() for item in receipts if item.outcome == "success"
    }
    complete = all(item.model_dump_json() in successes for item in requests)
    expected = "complete" if complete else "external-verification-pending"
    if completion_status != expected:
        from envresearch.personal_validation.report import authority_error

        raise authority_error("external access outcome differs from completion status")


def materialize_external_records(
    review_ref: ArtifactRef,
    finding_refs_by_key: dict[str, ArtifactRef],
    requests: tuple[ExternalAccessRequest, ...],
    receipt_refs: tuple[ArtifactRef, ...],
    receipts: tuple[ExternalAccessReceipt, ...],
) -> tuple[tuple[str, BaseModel], ...]:
    from envresearch.personal_validation.review_contracts import ExternalAccessRecord

    successful = {
        item.request.model_dump_json(): (reference, item)
        for reference, item in zip(receipt_refs, receipts, strict=True)
        if item.outcome == "success"
    }
    records: list[tuple[str, BaseModel]] = []
    for request in requests:
        matched = successful.get(request.model_dump_json())
        if matched is None:
            continue
        receipt_ref, _receipt = matched
        record = ExternalAccessRecord(
            schema_version="personal.external-access-record.v1",
            review_ref=review_ref,
            request=request,
            receipt_ref=receipt_ref,
            finding_refs=tuple(
                finding_refs_by_key[key] for key in request.local_finding_keys
            ),
        )
        identity = (
            "personal-external-access-record-"
            + hashlib.sha256(record.model_dump_json().encode()).hexdigest()
        )
        records.append((identity, record))
    return tuple(records)
