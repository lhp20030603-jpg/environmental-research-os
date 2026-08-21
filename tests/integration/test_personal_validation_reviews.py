"""Role-safe Personal review lifecycle over the real private store."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from personal_validation_review_fixtures import ReviewCase, Role, make_review_case
from pydantic import BaseModel

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation._strict import canonical_json
from envresearch.personal_validation.contracts import (
    CASE_ORDER,
    PersonalCanonicalCase,
    PersonalValidationAttempt,
    materialize_id,
)
from envresearch.personal_validation.errors import (
    PersonalValidationAuthorityInvalid,
    PersonalValidationIntegrityInvalid,
)
from envresearch.personal_validation.report import _authenticate_publication
from envresearch.personal_validation.review_closure import require_bundle_closure_safe
from envresearch.personal_validation.review_contracts import (
    AgentFindingResponse,
    AgentReviewResponse,
    CaseBehaviorEvaluation,
    ExternalAccessRequest,
    PersonalFinding,
    ReviewAssignment,
    ReviewPublication,
)


class ClosureNode(BaseModel):
    finding_id: str
    next_ref: ArtifactRef | None = None
    payload: str


class ClosureProbe(BaseModel):
    behavioral_contract_ref: ArtifactRef
    target_refs: tuple[ArtifactRef, ...]
    evidence_refs: tuple[ArtifactRef, ...]


@pytest.fixture
def review_case(tmp_path: Path):  # type: ignore[no-untyped-def]
    review = make_review_case(tmp_path)
    try:
        yield review
    finally:
        review.close()


def _canonical_object_bytes(review_case: ReviewCase, reference: ArtifactRef) -> bytes:
    relative = (
        Path("exit/objects")
        / reference.artifact_id
        / f"v{reference.artifact_version}-{reference.content_hash}.json"
    )
    return review_case.store.objects.read_file(relative, description="review object")


@pytest.mark.parametrize("role", ["scientific", "evidence"])
def test_primary_bundle_withholds_oracle_and_prior_review_material(
    review_case: ReviewCase, role: Role
) -> None:
    prepared = review_case.prepare(role)
    encoded = _canonical_object_bytes(review_case, prepared.bundle_ref)
    assert b"seeded/private/location" not in encoded
    assert review_case.oracle_digest.encode() not in encoded
    assert b"prior_review" not in encoded
    assert b"predecessor_finding" not in encoded


def test_bundle_rejects_canary_in_referenced_projection_artifact(
    tmp_path: Path,
) -> None:
    review = make_review_case(tmp_path, projection_canary="prior_review")
    try:
        with pytest.raises(PersonalValidationAuthorityInvalid, match="canary"):
            review.prepare("scientific")
    finally:
        review.close()


@pytest.mark.parametrize("case_kind", CASE_ORDER)
def test_every_role_and_target_kind_reopens_safe_closure(
    tmp_path: Path, case_kind: str
) -> None:
    review = make_review_case(tmp_path, case_kind=case_kind)
    try:
        scientific = review.record("scientific", invocation_id=f"{case_kind}-science")
        evidence = review.record("evidence", invocation_id=f"{case_kind}-evidence")
        prepared = review.service.prepare_bundle(
            review.attempt_ref,
            role="synthesis",
            primary_publication_refs=(
                scientific.publication_ref,
                evidence.publication_ref,
            ),
        )
        assert prepared.bundle.role == "synthesis"
    finally:
        review.close()


@pytest.mark.parametrize("hops", [1, 2])
def test_recursive_closure_rejects_direct_and_second_hop_canary(
    tmp_path: Path, hops: int
) -> None:
    review = make_review_case(tmp_path)
    try:
        leaf = ClosureNode(finding_id="canary-leaf", payload="prior_review")
        leaf_ref = review.store.publish(leaf.finding_id, leaf)
        evidence_ref = leaf_ref
        if hops == 2:
            parent = ClosureNode(
                finding_id="canary-parent", next_ref=leaf_ref, payload="clean"
            )
            evidence_ref = review.store.publish(parent.finding_id, parent)
        attempt = review.store.load(review.attempt_ref, PersonalValidationAttempt)
        case = review.store.load(attempt.case_ref, PersonalCanonicalCase)
        assert attempt.target.target_type == "correct-stop"
        probe = ClosureProbe(
            behavioral_contract_ref=case.reviewer_contract_ref,
            target_refs=(attempt.target.inspection_ref,),
            evidence_refs=(evidence_ref,),
        )
        with pytest.raises(PersonalValidationAuthorityInvalid, match="canary"):
            require_bundle_closure_safe(review.store, case, attempt, probe)
    finally:
        review.close()


def test_assignment_rejects_cross_role_replay(review_case: ReviewCase) -> None:
    assignment = review_case.assign("scientific", invocation_id="invoke-0001")
    receipt = review_case.record_dispatch(assignment)
    response = review_case.canonical_response_bytes(role="evidence")
    with pytest.raises(PersonalValidationAuthorityInvalid, match="role"):
        review_case.service.record_review(assignment, receipt, response)


def test_scientific_review_records_exact_immutable_closure_and_replays(
    review_case: ReviewCase,
) -> None:
    prepared = review_case.prepare("scientific")
    assignment = review_case.assign("scientific", invocation_id="invoke-science")
    receipt = review_case.record_dispatch(assignment)
    finding = AgentFindingResponse(
        local_finding_key="finding-001",
        domain="method-identification",
        severity="important",
        target_refs=prepared.bundle.target_refs,
        evidence_refs=(prepared.bundle.evidence_refs[0],),
        problem="The identification claim exceeds the available support.",
        impact="The causal interpretation is unsafe.",
        repair_proposal="Limit the claim to the supported estimand.",
    )
    raw = review_case.canonical_response_bytes(role="scientific", findings=(finding,))
    recorded = review_case.service.record_review(assignment, receipt, raw)
    assert (
        review_case.store.load(recorded.publication_ref, ReviewPublication).finding_refs
        == recorded.finding_refs
    )
    assert (
        review_case.store.load(recorded.finding_refs[0], PersonalFinding).severity
        == "important"
    )
    event_count = len(review_case.store.read_events())
    assert review_case.service.record_review(assignment, receipt, raw) == recorded
    assert len(review_case.store.read_events()) == event_count


@pytest.mark.parametrize("missing", ["review-published", "dispatch-recorded"])
def test_publication_authentication_rejects_missing_closure_event(
    review_case: ReviewCase, missing: str
) -> None:
    recorded = review_case.record("scientific", invocation_id=f"missing-{missing}")
    events = tuple(
        event for event in review_case.store.read_events() if event.operation != missing
    )
    with pytest.raises(PersonalValidationAuthorityInvalid, match="event"):
        _authenticate_publication(review_case.store, events, recorded.publication_ref)


def test_report_authenticates_orchestration_external_receipt_closure(
    review_case: ReviewCase,
) -> None:
    review_case.prepare("scientific")
    assignment = review_case.assign("scientific", invocation_id="external-report")
    dispatch_receipt = review_case.record_dispatch(assignment)
    request = ExternalAccessRequest(
        provider="web",
        operation="read-official-documentation",
        source_locator="https://example.org/policy",
        local_finding_keys=(),
    )
    access_dispatch = review_case.service.record_external_access_dispatch(
        assignment,
        assignment,
        canonical_json(request.model_dump(mode="json")),
    )
    access_receipt = review_case.service.record_external_access_receipt(
        access_dispatch,
        b'{"status":"observed"}',
        outcome="success",
        observed_at=datetime(2026, 8, 21, tzinfo=UTC),
    )
    response = AgentReviewResponse(
        schema_version="personal.agent-review-response.v1",
        role="scientific",
        findings=(),
        external_access_requests=(request,),
        completion_status="complete",
    )
    scientific = review_case.service.record_review(
        assignment,
        dispatch_receipt,
        canonical_json(response.model_dump(mode="json")),
        external_access_receipt_refs=(access_receipt,),
    )
    authenticated = _authenticate_publication(
        review_case.store,
        review_case.store.read_events(),
        scientific.publication_ref,
    )
    assert authenticated.external_access_records[0].receipt.outcome == "success"
    without_receipt = tuple(
        event
        for event in review_case.store.read_events()
        if event.operation != "external-access-received"
    )
    with pytest.raises(PersonalValidationAuthorityInvalid, match="receipt closure"):
        _authenticate_publication(
            review_case.store, without_receipt, scientific.publication_ref
        )
    evidence = review_case.record("evidence", invocation_id="external-evidence")
    synthesis = review_case.record(
        "synthesis",
        invocation_id="external-synthesis",
        primary_publication_refs=(
            scientific.publication_ref,
            evidence.publication_ref,
        ),
    )
    evaluation_ref = review_case.service.evaluate_case(review_case.attempt_ref)
    finalized = review_case.service.finalize_report(
        review_case.attempt_ref,
        evaluation_ref,
        scientific,
        evidence,
        synthesis,
    )
    assert finalized.report.state == "personal-baseline-passed"


def test_review_finding_cannot_introduce_unrelated_reference(
    review_case: ReviewCase,
) -> None:
    review_case.prepare("evidence")
    assignment = review_case.assign("evidence", invocation_id="invoke-evidence")
    receipt = review_case.record_dispatch(assignment)
    unrelated = ArtifactRef(
        artifact_id="unrelated-private-object",
        artifact_version=1,
        content_hash="d" * 64,
    )
    finding = AgentFindingResponse(
        local_finding_key="finding-001",
        domain="evidence-numbers-citations",
        severity="minor",
        target_refs=(unrelated,),
        evidence_refs=(unrelated,),
        problem="An unrelated object was supplied.",
        impact="The review closure would escape its role projection.",
        repair_proposal="Use only assigned bundle references.",
    )
    raw = review_case.canonical_response_bytes(role="evidence", findings=(finding,))
    with pytest.raises(PersonalValidationAuthorityInvalid, match="allowlist"):
        review_case.service.record_review(assignment, receipt, raw)


def test_synthesis_omission_cannot_remove_primary_finding(
    review_case: ReviewCase,
) -> None:
    prepared = review_case.prepare("scientific")
    important = AgentFindingResponse(
        local_finding_key="finding-001",
        domain="method-identification",
        severity="important",
        target_refs=prepared.bundle.target_refs,
        evidence_refs=(prepared.bundle.evidence_refs[0],),
        problem="The primary design does not support the stated causal claim.",
        impact="The reported interpretation is not scientifically defensible.",
        repair_proposal="Revise the claim and rerun the advisory review.",
    )
    scientific = review_case.record(
        "scientific", invocation_id="science-primary", findings=(important,)
    )
    evidence = review_case.record("evidence", invocation_id="evidence-primary")
    synthesis = review_case.record(
        "synthesis",
        invocation_id="synthesis-review",
        primary_publication_refs=(
            scientific.publication_ref,
            evidence.publication_ref,
        ),
    )
    evaluation_ref = review_case.service.evaluate_case(review_case.attempt_ref)
    finalized = review_case.service.finalize_report(
        review_case.attempt_ref,
        evaluation_ref,
        scientific,
        evidence,
        synthesis,
    )
    assert finalized.report.state == "needs-revision"
    assert finalized.report.finding_refs == scientific.finding_refs
    synthesis_publication = review_case.store.load(
        synthesis.publication_ref, ReviewPublication
    )
    synthesis_assignment = review_case.store.load(
        synthesis_publication.assignment_ref, ReviewAssignment
    )
    assert set(synthesis_assignment.primary_publication_refs) == {
        scientific.publication_ref,
        evidence.publication_ref,
    }


def test_report_recomputes_and_rejects_forged_evaluation_verdict(
    review_case: ReviewCase,
) -> None:
    scientific = review_case.record("scientific", invocation_id="science-clean")
    evidence = review_case.record("evidence", invocation_id="evidence-clean")
    synthesis = review_case.record(
        "synthesis",
        invocation_id="synthesis-clean",
        primary_publication_refs=(
            scientific.publication_ref,
            evidence.publication_ref,
        ),
    )
    evaluation_ref = review_case.service.evaluate_case(review_case.attempt_ref)
    evaluation = review_case.store.load(evaluation_ref, CaseBehaviorEvaluation)
    forged_payload = evaluation.model_dump(mode="python", exclude={"evaluation_id"})
    forged_payload["verdict"] = "behavior-deviation"
    forged_payload["evaluation_id"] = materialize_id(
        "personal-evaluation-", forged_payload
    )
    forged = CaseBehaviorEvaluation.model_validate(forged_payload)
    forged_ref = review_case.store.publish(forged.evaluation_id, forged)
    with pytest.raises(PersonalValidationIntegrityInvalid, match="evaluation"):
        review_case.service.finalize_report(
            review_case.attempt_ref,
            forged_ref,
            scientific,
            evidence,
            synthesis,
        )


def test_evaluation_observes_actual_stop_code_from_case_source(tmp_path: Path) -> None:
    review = make_review_case(tmp_path)
    try:
        evaluation_ref = review.service.evaluate_case(review.attempt_ref)
        evaluation = review.store.load(evaluation_ref, CaseBehaviorEvaluation)
        blocker = next(
            item
            for item in evaluation.observations
            if item.observation_kind == "correct-stop-blocker"
        )
        assert blocker.exact_code == "RESEARCH_RUN_BLOCKED"
        assert blocker.exact_finding_kind == "design-blocking-finding"
    finally:
        review.close()
