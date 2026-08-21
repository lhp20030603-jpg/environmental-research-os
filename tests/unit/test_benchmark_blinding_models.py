"""Tests for source-blind brief and leakage-report contracts."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_blinding import (
    BlindedBrief,
    BlindedFact,
    LeakageCategory,
    LeakageFinding,
    LeakageReport,
    LeakageSeverity,
)

UTC_TIME = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)
SHA256 = "a" * 64


def artifact_ref(artifact_id: str) -> ArtifactRef:
    """Return a durable artifact reference for blind-contract tests."""
    return ArtifactRef(
        artifact_id=artifact_id,
        artifact_version=1,
        content_hash=SHA256,
    )


def brief_payload() -> dict[str, object]:
    """Return an identity-free method-selection brief payload."""
    return {
        "case_id": "pilot-001",
        "source_sheet_ref": artifact_ref("curator-source-sheet"),
        "policy_setting": "An emissions reporting requirement.",
        "population": "Eligible regulated facilities.",
        "unit": "Facility-year",
        "treatment_or_exposure": "Requirement adoption date.",
        "timing": "Annual observations before and after adoption.",
        "candidate_outcomes": ("Reported emissions",),
        "data_structures": ("Facility-level annual panel",),
        "available_variables": ("Reported emissions", "adoption year"),
        "institutional_rules": ("Eligibility follows a capacity threshold.",),
        "constraints": ("No randomized assignment is available.",),
        "facts": (
            BlindedFact(
                fact_id="fact-001",
                statement="Facilities report annual emissions after eligibility.",
                fact_kind="institution",
            ),
        ),
        "masker_principal": "masker-1",
    }


def leakage_finding(*, resolved: bool) -> LeakageFinding:
    """Return one locator-bound leakage finding for verdict tests."""
    return LeakageFinding(
        category=LeakageCategory.IDENTITY,
        severity=LeakageSeverity.HIGH,
        locator="/policy_setting",
        disposition="Remove the identifying phrase.",
        resolved=resolved,
    )


def test_brief_rejects_source_identifiers() -> None:
    """A blinded handoff cannot serialize a direct source identifier."""
    payload = brief_payload()
    payload["zotero_item_key"] = "2PPVRAL8"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        BlindedBrief.model_validate(payload)


def test_passing_report_rejects_open_finding() -> None:
    """A PASS verdict is impossible while any leakage remains unresolved."""
    with pytest.raises(ValidationError, match="PASS report cannot contain open findings"):
        LeakageReport(
            source_sheet_ref=artifact_ref("curator-source-sheet"),
            blinded_brief_ref=artifact_ref("blinded-brief"),
            findings=(leakage_finding(resolved=False),),
            verdict="pass",
            validator_principal="validator-1",
            scanner_version="blind-leakage-v1",
            scanner_config_sha256=SHA256,
            checked_at=UTC_TIME,
        )


def test_brief_requires_unique_nonblank_facts_and_method_inputs() -> None:
    """A frozen brief keeps every method-relevant list deterministic."""
    payload = brief_payload()
    payload["candidate_outcomes"] = ("Reported emissions", "Reported emissions")

    with pytest.raises(
        ValidationError, match="candidate_outcomes must not contain duplicate values"
    ):
        BlindedBrief.model_validate(payload)

    payload = brief_payload()
    payload["facts"] = (
        BlindedFact(
            fact_id="fact-001",
            statement="Facilities report annual emissions after eligibility.",
            fact_kind="institution",
        ),
        BlindedFact(
            fact_id="fact-001",
            statement="Adoption is observed annually.",
            fact_kind="timing",
        ),
    )

    with pytest.raises(ValidationError, match="facts must not contain duplicate fact IDs"):
        BlindedBrief.model_validate(payload)


def test_report_requires_distinct_exact_source_and_brief_references() -> None:
    """A validator must bind its result to two separately named artifacts."""
    shared_ref = artifact_ref("shared-artifact")

    with pytest.raises(
        ValidationError, match="source_sheet_ref and blinded_brief_ref must differ"
    ):
        LeakageReport(
            source_sheet_ref=shared_ref,
            blinded_brief_ref=shared_ref,
            findings=(),
            verdict="pass",
            validator_principal="validator-1",
            scanner_version="blind-leakage-v1",
            scanner_config_sha256=SHA256,
            checked_at=UTC_TIME,
        )


def test_rejected_report_allows_open_finding() -> None:
    """A rejected report retains unresolved evidence for remediation."""
    report = LeakageReport(
        source_sheet_ref=artifact_ref("curator-source-sheet"),
        blinded_brief_ref=artifact_ref("blinded-brief"),
        findings=(leakage_finding(resolved=False),),
        verdict="rejected",
        validator_principal="validator-1",
        scanner_version="blind-leakage-v1",
        scanner_config_sha256=SHA256,
        checked_at=UTC_TIME,
    )

    assert report.findings[0].resolved is False


def test_report_requires_canonical_scanner_configuration_digest() -> None:
    """A leakage verdict must bind the validator's exact scanner configuration."""
    with pytest.raises(
        ValidationError,
        match="scanner_config_sha256 must be a 64-character lowercase SHA-256",
    ):
        LeakageReport(
            source_sheet_ref=artifact_ref("curator-source-sheet"),
            blinded_brief_ref=artifact_ref("blinded-brief"),
            findings=(),
            verdict="pass",
            validator_principal="validator-1",
            scanner_version="blind-leakage-v1",
            scanner_config_sha256="A" * 64,
            checked_at=UTC_TIME,
        )
