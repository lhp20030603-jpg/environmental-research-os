from __future__ import annotations

import hashlib

import pytest
from pydantic import TypeAdapter, ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation._strict import canonical_json
from envresearch.personal_validation.contracts import (
    AttemptTarget,
    CorrectStopTarget,
    InputEntry,
    InputSnapshot,
    PersonalCanonicalCaseBinding,
    PersonalValidationAttempt,
    PersonalValidationProtocol,
    PersonalValidationSession,
    materialize_id,
)
from envresearch.personal_validation.review_contracts import (
    AgentReview,
    AgentReviewResponse,
    AuthenticatedReviewPublication,
    PersonalFinding,
    PersonalValidationReport,
    ReviewPublicationBinding,
)
from envresearch.research.stop_contracts import (
    ResearchCheckpointEvidence,
    ResearchFileEvidence,
    ResearchStopInspection,
)

SHA = "a" * 64


def artifact_ref(name: str) -> ArtifactRef:
    return ArtifactRef(artifact_id=name, artifact_version=1, content_hash=SHA)


def protocol_payload() -> dict[str, object]:
    cases = tuple(
        PersonalCanonicalCaseBinding(case_ref=artifact_ref(kind), kind=kind)
        for kind in (
            "successful-end-to-end",
            "correct-stop",
            "data-method-incompatibility",
            "evidence-citation-challenge",
        )
    )
    payload: dict[str, object] = {
        "schema_version": "personal.validation-protocol.v1",
        "protocol_version": "1",
        "cases": cases,
        "scientific_policy_sha256": SHA,
        "evidence_policy_sha256": SHA,
        "synthesis_policy_sha256": SHA,
        "rubric_sha256": SHA,
        "report_schema_sha256": SHA,
        "external_access_policy_sha256": SHA,
        "scope": "personal-advisory-only",
        "blocks": (),
        "hidden_evaluation_status": "not-run",
        "product_release_status": "scientific_release_pending",
    }
    payload["protocol_id"] = materialize_id("personal-protocol-", payload)
    return payload


def test_protocol_is_personal_only_and_exactly_four_cases() -> None:
    protocol = PersonalValidationProtocol.model_validate(protocol_payload())
    assert protocol.scope == "personal-advisory-only"
    assert protocol.blocks == ()
    assert protocol.hidden_evaluation_status == "not-run"
    assert protocol.product_release_status == "scientific_release_pending"
    assert tuple(case.kind for case in protocol.cases) == (
        "successful-end-to-end",
        "correct-stop",
        "data-method-incompatibility",
        "evidence-citation-challenge",
    )


def test_attempt_target_is_strict_discriminated_union() -> None:
    inspection = stop_inspection()
    target = CorrectStopTarget(
        target_type="correct-stop",
        inspection_ref=ArtifactRef(
            artifact_id="inspection",
            artifact_version=1,
            content_hash=hashlib.sha256(
                inspection.model_dump_json().encode()
            ).hexdigest(),
        ),
        inspection=inspection,
        attempt_inventory_ref=artifact_ref("inventory"),
    )
    payload = target.model_dump(mode="python")
    payload["target_type"] = "unknown-target"
    with pytest.raises(ValidationError) as caught:
        TypeAdapter(AttemptTarget).validate_python(payload)
    assert [item["type"] for item in caught.value.errors()] == ["union_tag_invalid"]


def test_attempt_has_no_forward_bundle_or_completion_reference() -> None:
    fields = PersonalValidationAttempt.model_fields
    assert "review_bundle_ref" not in fields
    assert "completion_event_ref" not in fields


def test_authenticated_publication_requires_assignment_and_dispatch_receipt() -> None:
    assert {"assignment", "dispatch_receipt"} <= set(
        AuthenticatedReviewPublication.model_fields
    )


def test_finding_is_immutable_and_has_no_status_field() -> None:
    payload: dict[str, object] = {
        "domain": "method-identification",
        "severity": "minor",
        "target_refs": (artifact_ref("target"),),
        "evidence_refs": (artifact_ref("evidence"),),
        "problem": "The identification claim exceeds its support.",
        "impact": "The causal interpretation is unsafe.",
        "repair_proposal": "Narrow the claim to the supported estimand.",
        "source_review_refs": (artifact_ref("source-review"),),
    }
    payload["finding_id"] = materialize_id("personal-finding-", payload)
    finding = PersonalFinding.model_validate(payload)
    assert "status" not in PersonalFinding.model_fields
    with pytest.raises(ValidationError) as caught:
        PersonalFinding.model_validate(
            {**finding.model_dump(mode="python"), "status": "verified-closed"}
        )
    assert [item["type"] for item in caught.value.errors()] == ["extra_forbidden"]


def test_review_raw_response_digest_binds_exact_canonical_response() -> None:
    response = AgentReviewResponse(
        schema_version="personal.agent-review-response.v1",
        role="scientific",
        findings=(),
        external_access_requests=(),
        completion_status="complete",
    )
    payload: dict[str, object] = {
        "schema_version": "personal.agent-review.v1",
        "assignment_ref": artifact_ref("scientific-assignment"),
        "attempt_ref": artifact_ref("attempt"),
        "bundle_ref": artifact_ref("scientific-bundle"),
        "role": "scientific",
        "policy_sha256": SHA,
        "dispatch_receipt_ref": artifact_ref("scientific-receipt"),
        "raw_response_sha256": SHA,
        "response": response,
    }
    payload["review_id"] = materialize_id("personal-review-", payload)
    with pytest.raises(ValidationError):
        AgentReview.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "assignment_ref",
        "attempt_ref",
        "bundle_ref",
        "policy_sha256",
        "dispatch_receipt_ref",
    ],
)
def test_review_identity_rejects_each_independent_binding_tamper(field: str) -> None:
    response = AgentReviewResponse(
        schema_version="personal.agent-review-response.v1",
        role="scientific",
        findings=(),
        external_access_requests=(),
        completion_status="complete",
    )
    payload: dict[str, object] = {
        "schema_version": "personal.agent-review.v1",
        "assignment_ref": artifact_ref("scientific-assignment"),
        "attempt_ref": artifact_ref("attempt"),
        "bundle_ref": artifact_ref("scientific-bundle"),
        "role": "scientific",
        "policy_sha256": SHA,
        "dispatch_receipt_ref": artifact_ref("scientific-receipt"),
        "raw_response_sha256": hashlib.sha256(
            canonical_json(response.model_dump(mode="json"))
        ).hexdigest(),
        "response": response,
    }
    payload["review_id"] = materialize_id("personal-review-", payload)
    review = AgentReview.model_validate(payload)
    forged = review.model_dump(mode="python")
    forged[field] = (
        "b" * 64 if field == "policy_sha256" else artifact_ref(f"tampered-{field}")
    )
    with pytest.raises(ValidationError):
        AgentReview.model_validate(forged)


def test_report_rejects_swapped_role_publication_order() -> None:
    payload: dict[str, object] = {
        "schema_version": "personal.validation-report.v1",
        "attempt_ref": artifact_ref("attempt"),
        "evaluation_ref": artifact_ref("evaluation"),
        "review_publication_refs": (
            ReviewPublicationBinding(
                role="evidence", publication_ref=artifact_ref("evidence-publication")
            ),
            ReviewPublicationBinding(
                role="scientific",
                publication_ref=artifact_ref("scientific-publication"),
            ),
            ReviewPublicationBinding(
                role="synthesis",
                publication_ref=artifact_ref("synthesis-publication"),
            ),
        ),
        "finding_refs": (),
        "state": "personal-baseline-passed",
        "scope": "personal-advisory-only",
        "blocks": (),
        "hidden_evaluation_status": "not-run",
        "product_release_status": "scientific_release_pending",
    }
    payload["report_id"] = materialize_id("personal-report-", payload)
    with pytest.raises(ValidationError, match="role publication order"):
        PersonalValidationReport.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("scientific_policy_sha256", "b" * 64),
        ("evidence_policy_sha256", "b" * 64),
        ("synthesis_policy_sha256", "b" * 64),
        ("rubric_sha256", "b" * 64),
        ("report_schema_sha256", "b" * 64),
        ("external_access_policy_sha256", "b" * 64),
    ],
)
def test_protocol_id_binds_every_policy_digest(field: str, replacement: str) -> None:
    payload = protocol_payload()
    payload[field] = replacement
    with pytest.raises(ValidationError):
        PersonalValidationProtocol.model_validate(payload)


def test_protocol_revalidates_forged_preconstructed_case_binding() -> None:
    payload = protocol_payload()
    cases = list(payload["cases"])  # type: ignore[arg-type]
    cases[0] = PersonalCanonicalCaseBinding.model_construct(
        case_ref=artifact_ref("forged"), kind="not-a-case"
    )
    payload["cases"] = tuple(cases)
    with pytest.raises(ValidationError):
        PersonalValidationProtocol.model_validate(payload)


def test_nested_artifact_refs_reject_preconstructed_scalar_coercion() -> None:
    forged = ArtifactRef.model_construct(
        artifact_id="forged", artifact_version="1", content_hash=SHA
    )
    with pytest.raises(ValidationError):
        PersonalCanonicalCaseBinding(case_ref=forged, kind="correct-stop")


def test_input_snapshot_rejects_reordered_or_duplicate_entries() -> None:
    entries = (
        InputEntry(
            logical_name="a.json",
            kind="file",
            sha256=SHA,
            size_bytes=1,
            mode=0o600,
        ),
        InputEntry(
            logical_name="b.json",
            kind="file",
            sha256=SHA,
            size_bytes=1,
            mode=0o600,
        ),
    )
    for invalid in ((entries[1], entries[0]), (entries[0], entries[0])):
        payload: dict[str, object] = {
            "schema_version": "personal.input-snapshot.v1",
            "entries": invalid,
        }
        payload["snapshot_id"] = materialize_id("personal-input-snapshot-", payload)
        with pytest.raises(ValidationError):
            InputSnapshot.model_validate(payload)


def test_session_nonce_retry_converges_and_fresh_nonce_is_distinct() -> None:
    protocol = PersonalValidationProtocol.model_validate(protocol_payload())

    def make(nonce: str) -> PersonalValidationSession:
        payload: dict[str, object] = {
            "schema_version": "personal.validation-session.v1",
            "session_nonce": nonce,
            "protocol_ref": artifact_ref(protocol.protocol_id),
            "cases": protocol.cases,
        }
        payload["session_id"] = materialize_id("personal-session-", payload)
        return PersonalValidationSession.model_validate(payload)

    first = make("persisted-nonce-1")
    assert make("persisted-nonce-1").session_id == first.session_id
    assert make("persisted-nonce-2").session_id != first.session_id
    forged = first.model_dump(mode="python")
    forged["session_nonce"] = "persisted-nonce-2"
    with pytest.raises(ValidationError):
        PersonalValidationSession.model_validate(forged)


def stop_inspection() -> ResearchStopInspection:
    return ResearchStopInspection(
        schema_version="research.stop-inspection.v1",
        run_id="blocked-run",
        phase="blocked",
        stop_code="RESEARCH_RUN_BLOCKED",
        findings=(artifact_ref("finding"),),
        checkpoints=(
            ResearchCheckpointEvidence(
                node_id="screen-methods",
                checkpoint_sha256=SHA,
                artifact_refs=(),
            ),
        ),
        research_evidence=(
            ResearchFileEvidence(
                relative_path="artifacts/design-review-findings.json",
                kind="file",
                sha256=SHA,
                size_bytes=1,
                mode=0o600,
            ),
        ),
    )


def test_research_stop_digests_require_full_lowercase_sha256() -> None:
    with pytest.raises(ValidationError):
        ResearchCheckpointEvidence(
            node_id="screen-methods",
            checkpoint_sha256="A" * 64,
            artifact_refs=(),
        )
    with pytest.raises(ValidationError):
        ResearchFileEvidence(
            relative_path="finding.json",
            kind="file",
            sha256="a" * 63,
            size_bytes=1,
            mode=0o600,
        )


def test_correct_stop_target_binds_exact_inspection_bytes() -> None:
    inspection = stop_inspection()
    with pytest.raises(ValidationError):
        CorrectStopTarget(
            target_type="correct-stop",
            inspection_ref=artifact_ref("inspection"),
            inspection=inspection,
            attempt_inventory_ref=artifact_ref("inventory"),
        )
