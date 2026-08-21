from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation.contracts import materialize_id
from envresearch.personal_validation.repair_contracts import (
    CanonicalReplacementBlob,
    ProtocolRegressionCaseResult,
    ProtocolRegressionReport,
    RepairClosure,
    ReplacementOperation,
    ReviewPublicationBinding,
    VerifiedFindingResolution,
)

SHA = "a" * 64


def ref(name: str) -> ArtifactRef:
    return ArtifactRef(artifact_id=name, artifact_version=1, content_hash=SHA)


def test_replacement_blob_binds_exact_utf8_bytes() -> None:
    content = '{"claim":"bounded"}\n'
    blob = CanonicalReplacementBlob(
        schema_version="personal.replacement-blob.v1",
        media_type="application/json",
        utf8_content=content,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
    )
    with pytest.raises(ValidationError):
        CanonicalReplacementBlob.model_validate(
            blob.model_copy(update={"utf8_content": '{"claim":"changed"}\n'})
        )


@pytest.mark.parametrize(
    "path", ["../escape.json", "/absolute.json", "a/./b.json", "a//b.json"]
)
def test_replacement_target_must_be_safe_relative_posix_path(path: str) -> None:
    with pytest.raises(ValidationError):
        ReplacementOperation(
            operation_kind="replace-canonical-file",
            applicator_version="personal-replace-file-v1",
            logical_target=path,
            target_ref=ref("target"),
            before_sha256=SHA,
            replacement_blob_ref=ref("blob"),
            after_sha256="b" * 64,
        )


def test_protocol_regression_rejects_swapped_case_kind_order() -> None:
    kinds = (
        "correct-stop",
        "successful-end-to-end",
        "data-method-incompatibility",
        "evidence-citation-challenge",
    )
    results = tuple(
        ProtocolRegressionCaseResult(
            kind=kind,
            case_ref=ref(kind),
            attempt_ref=ref(f"{kind}-attempt"),
            report_ref=ref(f"{kind}-report"),
            evaluation_ref=ref(f"{kind}-evaluation"),
        )
        for kind in kinds
    )
    payload: dict[str, object] = {
        "schema_version": "personal.protocol-regression-report.v1",
        "protocol_ref": ref("protocol"),
        "session_ref": ref("session"),
        "case_results": results,
    }
    payload["regression_id"] = materialize_id("personal-protocol-regression-", payload)
    with pytest.raises(ValidationError, match="four-case order"):
        ProtocolRegressionReport.model_validate(payload)


def test_repair_closure_rejects_swapped_successor_review_roles() -> None:
    payload: dict[str, object] = {
        "schema_version": "personal.repair-closure.v1",
        "before_attempt_ref": ref("before-attempt"),
        "before_report_ref": ref("before-report"),
        "approval_ref": ref("approval"),
        "successor_attempt_ref": ref("successor-attempt"),
        "successor_review_publication_refs": (
            ReviewPublicationBinding(
                role="evidence", publication_ref=ref("evidence-publication")
            ),
            ReviewPublicationBinding(
                role="scientific", publication_ref=ref("scientific-publication")
            ),
            ReviewPublicationBinding(
                role="synthesis", publication_ref=ref("synthesis-publication")
            ),
        ),
        "successor_report_ref": ref("successor-report"),
        "successor_evaluation_ref": ref("successor-evaluation"),
        "rerun_evidence_refs": (ref("rerun"),),
        "verified_resolution_refs": (ref("resolution"),),
        "new_finding_refs": (),
        "protocol_regression_ref": ref("regression"),
    }
    payload["closure_id"] = materialize_id("personal-repair-closure-", payload)
    with pytest.raises(ValidationError, match="role publication order"):
        RepairClosure.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("finding_ref", ref("other-finding")),
        ("resolution", "limited"),
        ("successor_report_ref", ref("other-report")),
        ("successor_evaluation_ref", ref("other-evaluation")),
        ("witness_refs", (ref("other-witness"),)),
    ],
)
def test_resolution_identity_rejects_each_component_tamper(
    field: str, replacement: object
) -> None:
    payload: dict[str, object] = {
        "schema_version": "personal.verified-finding-resolution.v1",
        "finding_ref": ref("finding"),
        "resolution": "closed",
        "successor_report_ref": ref("successor-report"),
        "successor_evaluation_ref": ref("successor-evaluation"),
        "witness_refs": (ref("witness"),),
    }
    payload["resolution_id"] = materialize_id("personal-finding-resolution-", payload)
    resolution = VerifiedFindingResolution.model_validate(payload)
    forged = resolution.model_dump(mode="python")
    forged[field] = replacement
    with pytest.raises(ValidationError):
        VerifiedFindingResolution.model_validate(forged)
