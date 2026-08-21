"""Stable public error categories for the advisory Personal workflow."""

from __future__ import annotations

import hashlib
from typing import Any, ClassVar, cast

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation._strict import (
    ReviewDisagreement,
    artifact_ref_key,
    content_ref_matches,
    materialize_id,
)


class PersonalValidationError(ValueError):
    """Base error with a stable machine-readable code and finding kind."""

    code: ClassVar[str]

    def __init__(self, message: str, *, finding_kind: str) -> None:
        super().__init__(message)
        self.finding_kind = finding_kind


class PersonalValidationAuthorityInvalid(PersonalValidationError):
    code = "PERSONAL_VALIDATION_AUTHORITY_INVALID"


class PersonalValidationIntegrityInvalid(PersonalValidationError):
    code = "PERSONAL_VALIDATION_INTEGRITY_INVALID"


class PersonalValidationSupportInvalid(PersonalValidationError):
    code = "PERSONAL_VALIDATION_SUPPORT_INVALID"


class PersonalValidationScopeExceeded(PersonalValidationError):
    code = "PERSONAL_VALIDATION_SCOPE_EXCEEDED"


def _materialized_finding_payload(
    raw_finding: Any, source_review_ref: ArtifactRef
) -> dict[str, Any]:
    payload = cast(
        dict[str, Any],
        raw_finding.model_dump(mode="json", exclude={"local_finding_key"}),
    )
    payload["source_review_refs"] = [source_review_ref.model_dump(mode="json")]
    payload["finding_id"] = materialize_id("personal-finding-", payload)
    return payload


def require_authenticated_review_closure(
    *,
    assignment: Any,
    dispatch_receipt: Any,
    publication: Any,
    review: Any,
    findings: tuple[Any, ...],
    external_access_records: tuple[Any, ...],
) -> None:
    if (
        not content_ref_matches(
            review.assignment_ref, assignment.assignment_id, assignment
        )
        or publication.assignment_ref != review.assignment_ref
        or assignment.attempt_ref != review.attempt_ref
        or assignment.bundle_ref != review.bundle_ref
        or assignment.role != review.role
        or assignment.policy_sha256 != review.policy_sha256
    ):
        raise ValueError("authenticated assignment does not bind exact review")
    if (
        not content_ref_matches(
            review.dispatch_receipt_ref,
            review.dispatch_receipt_ref.artifact_id,
            dispatch_receipt,
        )
        or dispatch_receipt.assignment_ref != review.assignment_ref
        or dispatch_receipt.invocation_id != assignment.invocation_id
    ):
        raise ValueError("authenticated dispatch receipt does not bind assignment")
    if not content_ref_matches(publication.review_ref, review.review_id, review):
        raise ValueError("publication does not bind its exact review")

    finding_refs = tuple(
        sorted(
            (
                ArtifactRef(
                    artifact_id=item.finding_id,
                    artifact_version=1,
                    content_hash=hashlib.sha256(
                        item.model_dump_json().encode()
                    ).hexdigest(),
                )
                for item in findings
            ),
            key=artifact_ref_key,
        )
    )
    if publication.finding_refs != finding_refs:
        raise ValueError("publication does not bind its complete finding union")
    expected_findings = tuple(
        sorted(
            (
                _materialized_finding_payload(item, publication.review_ref)
                for item in review.response.findings
            ),
            key=lambda item: str(item["finding_id"]),
        )
    )
    actual_findings = tuple(
        sorted(
            (item.model_dump(mode="json") for item in findings),
            key=lambda item: str(item["finding_id"]),
        )
    )
    if actual_findings != expected_findings:
        raise ValueError("raw finding materialization is incomplete or substituted")

    finding_ref_by_id = {item.artifact_id: item for item in finding_refs}
    expected_access: dict[str, tuple[ArtifactRef, ...]] = {}
    raw_findings = {item.local_finding_key: item for item in review.response.findings}
    for request in review.response.external_access_requests:
        resolved: list[ArtifactRef] = []
        for local_key in request.local_finding_keys:
            raw_finding = raw_findings[local_key]
            payload = _materialized_finding_payload(raw_finding, publication.review_ref)
            resolved.append(finding_ref_by_id[str(payload["finding_id"])])
        expected_access[request.model_dump_json()] = tuple(
            sorted(resolved, key=artifact_ref_key)
        )
    authenticated_access_refs = tuple(
        item.record_ref for item in external_access_records
    )
    if authenticated_access_refs != publication.external_access_record_refs:
        raise ValueError("publication does not bind exact external access record refs")
    records = tuple(item.record for item in external_access_records)
    if any(
        record.review_ref != publication.review_ref
        or authenticated.receipt.assignment_ref != review.assignment_ref
        for authenticated, record in zip(external_access_records, records, strict=True)
    ):
        raise ValueError("external access record does not bind its source review")
    actual_access = {
        record.request.model_dump_json(): record.finding_refs for record in records
    }
    if (
        len(actual_access) != len(records)
        or any(
            expected_access.get(request) != finding_refs
            for request, finding_refs in actual_access.items()
        )
        or (
            review.response.completion_status == "complete"
            and actual_access != expected_access
        )
    ):
        raise ValueError("external access materialization is incomplete or substituted")


def derive_review_disagreements(
    publications: tuple[Any, ...],
) -> tuple[ReviewDisagreement, ...]:
    left, right = publications[:2]
    left_findings = {(item.domain, item.target_refs): item for item in left.findings}
    right_findings = {(item.domain, item.target_refs): item for item in right.findings}
    disagreements: list[ReviewDisagreement] = []
    for key in sorted(set(left_findings) & set(right_findings), key=str):
        left_finding = left_findings[key]
        right_finding = right_findings[key]
        assessment_fields = (
            "severity",
            "evidence_refs",
            "problem",
            "impact",
            "repair_proposal",
        )
        if all(
            getattr(left_finding, field) == getattr(right_finding, field)
            for field in assessment_fields
        ):
            continue
        payload: dict[str, Any] = {
            "schema_version": "personal.review-disagreement.v1",
            "left_review_ref": left.publication.review_ref,
            "right_review_ref": right.publication.review_ref,
            "domain": left_finding.domain,
            "target_refs": left_finding.target_refs,
            "left_finding_ref": next(
                item
                for item in left.publication.finding_refs
                if item.artifact_id == left_finding.finding_id
            ),
            "right_finding_ref": next(
                item
                for item in right.publication.finding_refs
                if item.artifact_id == right_finding.finding_id
            ),
            "disagreement_kind": "assessment-conflict",
        }
        payload["disagreement_id"] = materialize_id(
            "personal-review-disagreement-", payload
        )
        disagreements.append(ReviewDisagreement.model_validate(payload))
    return tuple(sorted(disagreements, key=lambda item: item.disagreement_id))


__all__ = [
    "PersonalValidationAuthorityInvalid",
    "PersonalValidationError",
    "PersonalValidationIntegrityInvalid",
    "PersonalValidationScopeExceeded",
    "PersonalValidationSupportInvalid",
]
