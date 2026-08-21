"""Tests for verified source-claim benchmark contracts."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_claims import (
    ClaimFactMap,
    ClaimFactMappingEntry,
    ClaimUsage,
    ClaimVerificationStatus,
    CuratorSourceSheet,
    RestrictedTerm,
    SourceLocator,
    VerifiedClaim,
)

UTC_TIME = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)
SHA256 = "a" * 64
PASSAGE_SHA256 = "b" * 64


def verified_claim() -> VerifiedClaim:
    """Return one independently verified, source-bound claim."""
    return VerifiedClaim(
        claim_id="claim-001",
        normalized_claim="The policy applies to eligible facilities.",
        source_item_key="2PPVRAL8",
        source_attachment_key="7S8T9UVW",
        source_content_hash=SHA256,
        locator=SourceLocator(page=12, section="Eligibility", paragraph=2),
        supporting_passage_hash=PASSAGE_SHA256,
        status=ClaimVerificationStatus.CLAIM_VERIFIED,
        extractor_principal="extractor-1",
        verifier_principal="verifier-1",
        verified_at=UTC_TIME,
    )


def source_sheet() -> CuratorSourceSheet:
    """Return a complete source sheet whose claim has the same source identity."""
    return CuratorSourceSheet(
        case_id="pilot-001",
        method_family="difference-in-differences",
        zotero_item_key="2PPVRAL8",
        zotero_attachment_key="7S8T9UVW",
        doi="10.1000/example.policy",
        title="A policy evaluation",
        authors=("Author One", "Author Two"),
        source_content_hash=SHA256,
        source_generation=1,
        institutional_context=("Eligible facilities report annual emissions.",),
        restricted_terms=(
            RestrictedTerm(term="example policy", rationale="Source title wording"),
        ),
        distinctive_phrase_hashes=(PASSAGE_SHA256,),
        claims=(verified_claim(),),
    )


def artifact_ref(artifact_id: str) -> ArtifactRef:
    """Return a durable artifact reference for map tests."""
    return ArtifactRef(
        artifact_id=artifact_id,
        artifact_version=1,
        content_hash=SHA256,
    )


def test_claim_verified_requires_independent_verifier() -> None:
    """A verifier cannot attest to their own extraction."""
    payload = verified_claim().model_copy(update={"verifier_principal": "extractor-1"})

    with pytest.raises(ValidationError, match="extractor and verifier must differ"):
        VerifiedClaim.model_validate(payload.model_dump(mode="python"))


def test_source_sheet_rejects_claim_from_another_attachment() -> None:
    """A source sheet cannot combine claims from separate attachments."""
    sheet = source_sheet()
    alien = sheet.claims[0].model_copy(update={"source_attachment_key": "OTHER123"})

    with pytest.raises(ValidationError, match="claim source identity mismatch"):
        CuratorSourceSheet.model_validate(
            sheet.model_copy(update={"claims": (alien,)}).model_dump(mode="python")
        )


def test_claim_verified_requires_reviewable_locator_and_utc_time() -> None:
    """Verified claims need a reproducible location and canonical review time."""
    payload = verified_claim().model_dump(mode="python")
    payload["locator"] = {
        "page": None,
        "section": None,
        "table": None,
        "paragraph": None,
    }

    with pytest.raises(
        ValidationError,
        match="locator must identify a page, section, table, or paragraph",
    ):
        VerifiedClaim.model_validate(payload)

    payload = verified_claim().model_dump(mode="python")
    payload["verified_at"] = datetime(
        2026, 8, 9, 16, 0, tzinfo=timezone(timedelta(hours=8))
    )
    with pytest.raises(ValidationError, match="timestamps must be UTC-aware"):
        VerifiedClaim.model_validate(payload)


def test_claim_fact_map_requires_unique_claim_and_fact_mappings() -> None:
    """A blind fact must have one unambiguous source-claim mapping."""
    entry = ClaimFactMappingEntry(claim_id="claim-001", fact_id="fact-001")

    with pytest.raises(
        ValidationError, match="entries must not contain duplicate claim or fact IDs"
    ):
        ClaimFactMap(
            case_id="pilot-001",
            source_sheet_ref=artifact_ref("curator-source-sheet"),
            blinded_brief_ref=artifact_ref("blinded-brief"),
            entries=(entry, entry.model_copy(update={"claim_id": "claim-002"})),
            mapper_principal="masker-1",
        )


def test_claim_usage_requires_canonical_statement_hash_and_json_pointer() -> None:
    """Citation uses must resolve to a single canonical accepted-artifact field."""
    with pytest.raises(
        ValidationError,
        match="statement_sha256 must be a 64-character lowercase SHA-256",
    ):
        ClaimUsage(
            claim_id="claim-001",
            statement_sha256="A" * 64,
            json_pointer="/diagnostics/0",
        )

    with pytest.raises(
        ValidationError, match="json_pointer must be an RFC 6901 pointer"
    ):
        ClaimUsage(
            claim_id="claim-001",
            statement_sha256=SHA256,
            json_pointer="diagnostics/0",
        )
