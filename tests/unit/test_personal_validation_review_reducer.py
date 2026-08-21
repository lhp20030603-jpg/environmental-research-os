"""Pure advisory review reducer contract tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation._strict import canonical_json
from envresearch.personal_validation.contracts import materialize_id
from envresearch.personal_validation.review_contracts import (
    AgentDispatchReceipt,
    AgentFindingResponse,
    AgentReview,
    AgentReviewResponse,
    AuthenticatedReviewPublication,
    CaseBehaviorEvaluation,
    CaseBehaviorObservation,
    PersonalFinding,
    ReviewAssignment,
    ReviewPublication,
    reduce_case_state,
)

SHA = "a" * 64


def ref(name: str, digest: str = SHA) -> ArtifactRef:
    return ArtifactRef(artifact_id=name, artifact_version=1, content_hash=digest)


def content_ref(name: str, value: object) -> ArtifactRef:
    assert hasattr(value, "model_dump_json")
    encoded = value.model_dump_json().encode()  # type: ignore[union-attr]
    return ref(name, hashlib.sha256(encoded).hexdigest())


def finding_payload(
    severity: str = "minor",
    source_review_ref: ArtifactRef | None = None,
    problem: str = "The identification claim exceeds its support.",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "domain": "method-identification",
        "severity": severity,
        "target_refs": (ref("target"),),
        "evidence_refs": (ref("evidence"),),
        "problem": problem,
        "impact": "The causal interpretation is unsafe.",
        "repair_proposal": "Narrow the claim to the supported estimand.",
        "source_review_refs": (source_review_ref or ref("source-review"),),
    }
    payload["finding_id"] = materialize_id("personal-finding-", payload)
    return payload


def authenticated_publication(
    role: str,
    *,
    severity: str | None = None,
    completion: str = "complete",
    omit_durable_findings: bool = False,
    authentication_mutation: str | None = None,
    problem: str = "The identification claim exceeds its support.",
) -> AuthenticatedReviewPublication:
    primary_refs = (
        (ref("evidence-publication"), ref("scientific-publication"))
        if role == "synthesis"
        else ()
    )
    assignment_payload: dict[str, object] = {
        "schema_version": "personal.review-assignment.v1",
        "attempt_ref": ref("attempt"),
        "bundle_ref": ref(f"{role}-bundle"),
        "role": role,
        "policy_sha256": SHA,
        "invocation_id": f"{role}-invocation",
        "primary_publication_refs": primary_refs,
    }
    assignment_payload["assignment_id"] = materialize_id(
        "personal-review-assignment-", assignment_payload
    )
    assignment = ReviewAssignment.model_validate(assignment_payload)
    assignment_ref = content_ref(assignment.assignment_id, assignment)
    dispatch_receipt = AgentDispatchReceipt(
        schema_version="personal.agent-dispatch-receipt.v1",
        assignment_ref=assignment_ref,
        invocation_id=assignment.invocation_id,
        observed_model_id="review-model-v1",
        observed_runtime_id="review-runtime-v1",
        dispatched_at=datetime(2026, 8, 21, tzinfo=UTC),
    )
    dispatch_receipt_ref = content_ref(f"{role}-dispatch-receipt", dispatch_receipt)
    raw_findings = ()
    if severity is not None:
        raw_findings = (
            AgentFindingResponse(
                local_finding_key="finding-1",
                domain="method-identification",
                severity=severity,
                target_refs=(ref("target"),),
                evidence_refs=(ref("evidence"),),
                problem=problem,
                impact="The causal interpretation is unsafe.",
                repair_proposal="Narrow the claim to the supported estimand.",
            ),
        )
    response = AgentReviewResponse(
        schema_version="personal.agent-review-response.v1",
        role=role,
        findings=raw_findings,
        external_access_requests=(),
        completion_status=completion,
    )
    review_payload: dict[str, object] = {
        "schema_version": "personal.agent-review.v1",
        "assignment_ref": assignment_ref,
        "attempt_ref": ref("attempt"),
        "bundle_ref": ref(f"{role}-bundle"),
        "role": role,
        "policy_sha256": SHA,
        "dispatch_receipt_ref": dispatch_receipt_ref,
        "raw_response_sha256": hashlib.sha256(
            canonical_json(response.model_dump(mode="json"))
        ).hexdigest(),
        "response": response,
    }
    review_payload["review_id"] = materialize_id("personal-review-", review_payload)
    review = AgentReview.model_validate(review_payload)
    findings = ()
    finding_refs = ()
    if severity is not None and not omit_durable_findings:
        review_ref = content_ref(review.review_id, review)
        finding = PersonalFinding.model_validate(
            finding_payload(severity, review_ref, problem)
        )
        findings = (finding,)
        finding_refs = (content_ref(finding.finding_id, finding),)
    access_record_refs = ()
    publication_payload: dict[str, object] = {
        "schema_version": "personal.review-publication.v1",
        "assignment_ref": review.assignment_ref,
        "review_ref": content_ref(review.review_id, review),
        "finding_refs": finding_refs,
        "external_access_record_refs": access_record_refs,
    }
    publication_payload["publication_id"] = materialize_id(
        "personal-review-publication-", publication_payload
    )
    publication = ReviewPublication.model_validate(publication_payload)
    supplied_assignment = assignment
    supplied_receipt = dispatch_receipt
    if authentication_mutation in {"assignment", "role"}:
        substituted = assignment.model_dump(mode="python", exclude={"assignment_id"})
        if authentication_mutation == "assignment":
            substituted["invocation_id"] = "substituted-invocation"
        else:
            substituted["role"] = "evidence"
        substituted["assignment_id"] = materialize_id(
            "personal-review-assignment-", substituted
        )
        supplied_assignment = ReviewAssignment.model_validate(substituted)
    elif authentication_mutation == "receipt":
        supplied_receipt = AgentDispatchReceipt(
            schema_version="personal.agent-dispatch-receipt.v1",
            assignment_ref=assignment_ref,
            invocation_id=assignment.invocation_id,
            observed_model_id="substituted-model",
            observed_runtime_id="review-runtime-v1",
            dispatched_at=datetime(2026, 8, 21, tzinfo=UTC),
        )
    return AuthenticatedReviewPublication(
        assignment=supplied_assignment,
        dispatch_receipt=supplied_receipt,
        publication=publication,
        review=review,
        findings=findings,
        external_access_records=(),
    )


def test_raw_material_finding_cannot_be_omitted_from_authenticated_publication() -> (
    None
):
    with pytest.raises(ValidationError, match="raw finding"):
        authenticated_publication(
            "scientific", severity="important", omit_durable_findings=True
        )


@pytest.mark.parametrize("mutation", ["assignment", "receipt", "role"])
def test_authenticated_publication_rejects_assignment_or_receipt_substitution(
    mutation: str,
) -> None:
    with pytest.raises(ValidationError, match="assignment|dispatch receipt"):
        authenticated_publication("scientific", authentication_mutation=mutation)


def evaluation(verdict: str = "expected-behavior-observed") -> CaseBehaviorEvaluation:
    observation = CaseBehaviorObservation(
        observation_kind="factory-chain-coherent",
        evidence_refs=(ref("evaluation-evidence"),),
    )
    payload: dict[str, object] = {
        "schema_version": "personal.case-behavior-evaluation.v1",
        "case_ref": ref("case"),
        "attempt_ref": ref("attempt"),
        "expected_behavior_ref": ref("expected"),
        "target_ref": ref("target"),
        "inventory_ref": ref("inventory"),
        "verifier_version": "personal-case-verifier-v1",
        "observations": (observation,),
        "verdict": verdict,
    }
    payload["evaluation_id"] = materialize_id("personal-evaluation-", payload)
    return CaseBehaviorEvaluation.model_validate(payload)


def complete_publications(
    *, scientific_important: bool = False
) -> tuple[AuthenticatedReviewPublication, ...]:
    return (
        authenticated_publication(
            "scientific", severity="important" if scientific_important else None
        ),
        authenticated_publication("evidence"),
        authenticated_publication("synthesis"),
    )


def test_synthesis_cannot_erase_primary_important_finding() -> None:
    state = reduce_case_state(
        evaluation(), complete_publications(scientific_important=True)
    )
    assert state == "needs-revision"


def test_caller_boolean_or_unverified_closure_cannot_enter_reducer() -> None:
    assert reduce_case_state.__code__.co_argcount == 2
    with pytest.raises(TypeError):
        reduce_case_state(evaluation(), complete_publications(), ())


def test_deviation_or_incomplete_review_requires_review() -> None:
    assert (
        reduce_case_state(evaluation("behavior-deviation"), complete_publications())
        == "review-required"
    )
    incomplete = (
        authenticated_publication("scientific"),
        authenticated_publication(
            "evidence", completion="external-verification-pending"
        ),
        authenticated_publication("synthesis"),
    )
    assert reduce_case_state(evaluation(), incomplete) == "review-required"


def test_expected_behavior_with_complete_clean_union_passes_personal_baseline() -> None:
    state = reduce_case_state(evaluation(), complete_publications())
    assert state == "personal-baseline-passed"


@pytest.mark.parametrize(
    "publications",
    [
        lambda values: (values[1], values[0], values[2]),
        lambda values: (values[0], values[0], values[2]),
    ],
)
def test_reducer_rejects_role_order_permutation_or_replay(publications: object) -> None:
    complete = complete_publications()
    mutated = publications(complete)  # type: ignore[operator]
    with pytest.raises(ValueError, match="scientific, evidence, and synthesis"):
        reduce_case_state(evaluation(), mutated)


def test_derived_primary_reviewer_disagreement_requires_review() -> None:
    publications = (
        authenticated_publication(
            "scientific", severity="minor", problem="Identification is too strong."
        ),
        authenticated_publication(
            "evidence", severity="minor", problem="Identification is well bounded."
        ),
        authenticated_publication("synthesis"),
    )
    assert reduce_case_state(evaluation(), publications) == "review-required"
