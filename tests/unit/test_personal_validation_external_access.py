"""Orchestration-owned external access and unambiguous review responses."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation._strict import canonical_json
from envresearch.personal_validation.errors import PersonalValidationAuthorityInvalid
from envresearch.personal_validation.review_closure import (
    WithheldReviewManifest,
    require_manifest_safe_bytes,
)
from envresearch.personal_validation.review_contracts import (
    AgentFindingResponse,
    AgentReviewResponse,
    ExternalAccessRequest,
)

SHA = "a" * 64
REF = ArtifactRef(artifact_id="target", artifact_version=1, content_hash=SHA)


def _request(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider": "web",
        "operation": "read-official-documentation",
        "source_locator": "https://example.org/policy?a=1",
        "local_finding_keys": (),
    }
    payload.update(changes)
    return payload


@pytest.mark.parametrize(
    ("provider", "operation", "locator"),
    (
        ("web", "read-citation-metadata", "https://example.org/item"),
        ("web", "read-official-documentation", "http://example.org/policy"),
        ("zotero", "read-official-documentation", "zotero://library/items/ABC"),
        ("zotero", "read-authorized-paper", "https://example.org/paper"),
    ),
)
def test_external_request_rejects_provider_operation_or_scheme_mismatch(
    provider: str, operation: str, locator: str
) -> None:
    with pytest.raises(ValidationError):
        ExternalAccessRequest.model_validate(
            _request(provider=provider, operation=operation, source_locator=locator)
        )


def test_external_request_normalizes_locator_before_identity() -> None:
    left = ExternalAccessRequest.model_validate(
        _request(source_locator="HTTPS://EXAMPLE.ORG:443/a/../policy?b=2&a=1#fragment")
    )
    right = ExternalAccessRequest.model_validate(
        _request(source_locator="https://example.org/policy?a=1&b=2")
    )
    assert left == right
    assert canonical_json(left.model_dump(mode="json")) == canonical_json(
        right.model_dump(mode="json")
    )


def test_agent_request_cannot_assert_response_provenance() -> None:
    with pytest.raises(ValidationError):
        ExternalAccessRequest.model_validate(
            _request(
                request_sha256=SHA,
                response_sha256=SHA,
                retrieved_at="2026-08-21T00:00:00Z",
                policy_ref=REF,
                authorization_ref=REF,
            )
        )


def test_oracle_digest_is_forbidden_inside_larger_decoded_string() -> None:
    manifest = WithheldReviewManifest(
        expected_behavior_ref=REF,
        forbidden_values=(SHA,),
        normalized_locators=(),
    )
    raw = canonical_json({"value": f"prefix-{SHA}-suffix"})
    with pytest.raises(PersonalValidationAuthorityInvalid, match="oracle"):
        require_manifest_safe_bytes(manifest, raw)


@pytest.mark.parametrize(
    ("forbidden", "payload"),
    (
        (SHA, {f"prefix-{SHA}-suffix": "clean"}),
        (SHA, {"value": SHA.upper()}),
        ("prior_review", {"value": "PRIOR_REVIEW"}),
    ),
)
def test_oracle_tokens_cover_object_keys_and_case_variants(
    forbidden: str, payload: dict[str, str]
) -> None:
    manifest = WithheldReviewManifest(
        expected_behavior_ref=REF,
        forbidden_values=(forbidden,),
        normalized_locators=(),
    )
    with pytest.raises(PersonalValidationAuthorityInvalid, match="oracle"):
        require_manifest_safe_bytes(manifest, canonical_json(payload))


@pytest.mark.parametrize(
    "candidate",
    (
        "/Users/private/root/document",
        "%2FUsers%2Fprivate%2Froot%2Fdocument",
        "%252FUsers%252Fprivate%252Froot%252Fdocument",
        "％252FUsers％252Fprivate％252Froot％252Fdocument",
        r"\Users\private\root\document",
    ),
)
def test_private_locator_is_forbidden_after_bounded_normalization(
    candidate: str,
) -> None:
    manifest = WithheldReviewManifest(
        expected_behavior_ref=REF,
        forbidden_values=(),
        normalized_locators=("/Users/private/root",),
    )
    with pytest.raises(PersonalValidationAuthorityInvalid, match="canary"):
        require_manifest_safe_bytes(manifest, canonical_json({"value": candidate}))


@pytest.mark.parametrize(
    "value",
    (
        "/Users/private/root",
        "%2FUsers%2Fprivate%2Froot",
        "%252FUsers%252Fprivate%252Froot",
        "％252FUsers％252Fprivate％252Froot",
        r"\Users\private\root",
        "/USERS/PRIVATE/ROOT",
    ),
)
def test_private_locator_is_forbidden_inside_normalized_query(value: str) -> None:
    manifest = WithheldReviewManifest(
        expected_behavior_ref=REF,
        forbidden_values=(),
        normalized_locators=("/Users/private/root",),
    )
    raw = canonical_json({"value": f"https://example.org/?q={value}"})
    with pytest.raises(PersonalValidationAuthorityInvalid, match="canary"):
        require_manifest_safe_bytes(manifest, raw)


def test_review_response_rejects_duplicate_domain_target_assessments() -> None:
    common = {
        "domain": "method-identification",
        "severity": "minor",
        "target_refs": (REF,),
        "evidence_refs": (REF,),
        "impact": "The assessment is ambiguous.",
        "repair_proposal": "Return one normalized assessment.",
    }
    first = AgentFindingResponse(
        local_finding_key="finding-001",
        problem="The first assessment says one thing.",
        **common,
    )
    second = AgentFindingResponse(
        local_finding_key="finding-002",
        problem="The second assessment conflicts with it.",
        **common,
    )
    with pytest.raises(ValidationError, match="domain.*target"):
        AgentReviewResponse(
            schema_version="personal.agent-review-response.v1",
            role="scientific",
            findings=(first, second),
            external_access_requests=(),
            completion_status="complete",
        )
