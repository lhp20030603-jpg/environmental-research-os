from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation._strict import canonical_json
from envresearch.personal_validation.contracts import materialize_id
from envresearch.personal_validation.review_contracts import (
    AgentReview,
    AgentReviewResponse,
    ExternalAccessRequest,
)

DIGEST = "a" * 64
NOW = datetime(2026, 8, 21, tzinfo=UTC)
REF = ArtifactRef(
    artifact_id="external-access-policy",
    artifact_version=1,
    content_hash=DIGEST,
)


def test_review_digest_binds_shared_canonical_raw_bytes() -> None:
    response = AgentReviewResponse(
        schema_version="personal.agent-review-response.v1",
        role="scientific",
        findings=(),
        external_access_requests=(),
        completion_status="complete",
    )
    canonical_raw = canonical_json(response.model_dump(mode="json"))
    assert canonical_raw != response.model_dump_json().encode()
    payload: dict[str, object] = {
        "schema_version": "personal.agent-review.v1",
        "assignment_ref": REF,
        "attempt_ref": REF,
        "bundle_ref": REF,
        "role": "scientific",
        "policy_sha256": DIGEST,
        "dispatch_receipt_ref": REF,
        "raw_response_sha256": hashlib.sha256(canonical_raw).hexdigest(),
        "response": response,
    }
    payload["review_id"] = materialize_id("personal-review-", payload)
    review = AgentReview.model_validate(payload)
    assert review.raw_response_sha256 == hashlib.sha256(canonical_raw).hexdigest()


def test_external_access_requires_exact_allowlisted_read_provenance() -> None:
    with pytest.raises(ValidationError):
        ExternalAccessRequest(
            provider="zotero",
            operation="delete-item",
            source_locator="zotero://library/items/ABC",
            local_finding_keys=(),
        )
