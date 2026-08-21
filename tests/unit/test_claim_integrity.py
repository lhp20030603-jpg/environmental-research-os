"""Tests for deterministic source-claim citation integrity validation."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from envresearch.benchmarks.claim_integrity import (
    CitationIntegrityReport,
    CitationIntegrityValidator,
    report_binding_is_valid,
)
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
from envresearch.models.benchmark_evaluation import AcceptedArtifactClaims

SHA256 = "a" * 64
PASSAGE_SHA256 = "b" * 64
UTC_TIME = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)


def source_sheet(
    *, claim_status: ClaimVerificationStatus = ClaimVerificationStatus.CLAIM_VERIFIED
) -> CuratorSourceSheet:
    """Return one source sheet with a claim whose status can be varied."""
    verified_fields: dict[str, object] = {}
    if claim_status is ClaimVerificationStatus.CLAIM_VERIFIED:
        verified_fields = {
            "verifier_principal": "verifier-1",
            "verified_at": UTC_TIME,
        }
    claim = VerifiedClaim(
        claim_id="claim-1",
        normalized_claim="The policy reduced emissions by 12%.",
        source_item_key="2PPVRAL8",
        source_attachment_key="7S8T9UVW",
        source_content_hash=SHA256,
        locator=SourceLocator(page=12),
        supporting_passage_hash=PASSAGE_SHA256,
        status=claim_status,
        extractor_principal="extractor-1",
        **verified_fields,
    )
    return CuratorSourceSheet(
        case_id="pilot-1",
        method_family="difference-in-differences",
        zotero_item_key="2PPVRAL8",
        zotero_attachment_key="7S8T9UVW",
        doi="10.1000/example.policy",
        title="A policy evaluation",
        authors=("Author One",),
        source_content_hash=SHA256,
        source_generation=1,
        institutional_context=("Eligible facilities report annual emissions.",),
        restricted_terms=(
            RestrictedTerm(term="example policy", rationale="Source title wording"),
        ),
        distinctive_phrase_hashes=(PASSAGE_SHA256,),
        claims=(claim,),
    )


def claim_fact_map() -> ClaimFactMap:
    """Return the valid blind-fact link for the source claim."""
    return ClaimFactMap(
        case_id="pilot-1",
        source_sheet_ref=artifact_ref("curator-source-sheet"),
        blinded_brief_ref=artifact_ref("blinded-brief"),
        entries=(ClaimFactMappingEntry(claim_id="claim-1", fact_id="fact-1"),),
        mapper_principal="masker-1",
    )


def accepted_artifact(text: str, claim_id: str | None) -> AcceptedArtifactClaims:
    """Return one accepted payload with an optional exact claim attachment."""
    usages: tuple[ClaimUsage, ...] = ()
    if claim_id is not None:
        usages = (
            ClaimUsage(
                claim_id=claim_id,
                statement_sha256=hashlib.sha256(text.encode()).hexdigest(),
                json_pointer="/statement",
            ),
        )
    return AcceptedArtifactClaims(
        artifact_ref=artifact_ref("analysis-plan"),
        payload={"statement": text},
        usages=usages,
    )


def artifact_ref(artifact_id: str) -> ArtifactRef:
    """Return a stable immutable reference for one test artifact."""
    return ArtifactRef(artifact_id=artifact_id, artifact_version=1, content_hash=SHA256)


def validate(
    *,
    source_sheets: tuple[CuratorSourceSheet, ...],
    fact_maps: tuple[ClaimFactMap, ...],
    artifacts: tuple[AcceptedArtifactClaims, ...],
) -> CitationIntegrityReport:
    """Validate one exact fixture generation through the public boundary."""
    return CitationIntegrityValidator().validate(
        source_sheets=source_sheets,
        fact_maps=fact_maps,
        artifacts=artifacts,
        source_sheet_refs=(artifact_ref("curator-source-sheet"),),
        claim_fact_map_refs=tuple(
            artifact_ref(f"claim-fact-map-{index + 1}")
            for index in range(len(fact_maps))
        ),
        blinded_brief_refs=(artifact_ref("blinded-brief"),) * len(fact_maps),
    )


@pytest.mark.parametrize(
    "status",
    (
        ClaimVerificationStatus.UNVERIFIED,
        ClaimVerificationStatus.METADATA_VERIFIED,
        ClaimVerificationStatus.STALE,
    ),
)
def test_gate_rejects_non_claim_verified_support(
    status: ClaimVerificationStatus,
) -> None:
    """Replacing verified support with any noncurrent status must reject it."""
    result = validate(
        source_sheets=(source_sheet(claim_status=status),),
        fact_maps=(claim_fact_map(),),
        artifacts=(
            accepted_artifact("The policy reduced emissions by 12%", "claim-1"),
        ),
    )

    assert result.passed is False
    assert {finding.code for finding in result.findings} == {
        "CLAIM_NOT_CURRENT_VERIFIED"
    }


def test_gate_rejects_quantitative_statement_without_usage() -> None:
    """Removing a usage from a numeric source-dependent statement must reject it."""
    result = validate(
        source_sheets=(source_sheet(),),
        fact_maps=(claim_fact_map(),),
        artifacts=(accepted_artifact("The sample covers 1,204 firms", None),),
    )

    assert result.passed is False
    assert result.findings[0].code == "SOURCE_STATEMENT_UNBOUND"


def test_gate_rejects_forged_statement_hash_and_unknown_claim() -> None:
    """A usage must bind the exact leaf hash and a current mapped claim."""
    artifact = accepted_artifact("The policy reduced emissions by 12%", "claim-1")
    forged = artifact.model_copy(
        update={
            "usages": (
                artifact.usages[0].model_copy(
                    update={"statement_sha256": "c" * 64, "claim_id": "claim-2"}
                ),
            )
        }
    )

    result = validate(
        source_sheets=(source_sheet(),),
        fact_maps=(claim_fact_map(),),
        artifacts=(forged,),
    )

    assert {finding.code for finding in result.findings} == {
        "CLAIM_USAGE_UNBOUND",
        "SOURCE_STATEMENT_UNBOUND",
    }


@pytest.mark.parametrize(
    "payload",
    (
        {"statement": "doi:10.1000/example.policy"},
        {"statement": "Author One (2024) reports a robust effect."},
        {"evidence_refs": ["evidence-001"]},
    ),
)
def test_gate_requires_usage_for_every_source_dependent_leaf(
    payload: dict[str, object],
) -> None:
    """DOI, author-year, and evidence_refs values must never bypass citation checks."""
    artifact = AcceptedArtifactClaims(
        artifact_ref=artifact_ref("analysis-plan"), payload=payload, usages=()
    )

    result = validate(
        source_sheets=(source_sheet(),),
        fact_maps=(claim_fact_map(),),
        artifacts=(artifact,),
    )

    assert result.passed is False
    assert [finding.code for finding in result.findings] == ["SOURCE_STATEMENT_UNBOUND"]


def test_gate_accepts_exact_bound_quantitative_statement() -> None:
    """A verified, mapped claim with an exact pointer and hash passes the gate."""
    result = validate(
        source_sheets=(source_sheet(),),
        fact_maps=(claim_fact_map(),),
        artifacts=(
            accepted_artifact("The policy reduced emissions by 12%", "claim-1"),
        ),
    )

    assert result.passed is True
    assert result.findings == ()
    assert result.validator_version == "claim-integrity-v1"
    assert result.accepted_artifact_bindings
    assert report_binding_is_valid(result)
    assert not report_binding_is_valid(replace(result, binding_sha256="f" * 64))


def test_report_binding_rejects_blinded_brief_generation_mutation() -> None:
    """The report digest must cover the exact brief generation behind fact maps."""
    result = validate(
        source_sheets=(source_sheet(),),
        fact_maps=(claim_fact_map(),),
        artifacts=(
            accepted_artifact("The policy reduced emissions by 12%", "claim-1"),
        ),
    )
    mutated = artifact_ref("blinded-brief").model_copy(
        update={"content_hash": "c" * 64}
    )

    assert result.blinded_brief_refs == (artifact_ref("blinded-brief"),)
    assert not report_binding_is_valid(replace(result, blinded_brief_refs=(mutated,)))


def test_gate_rejects_duplicate_case_fact_maps() -> None:
    """A conflicting fact-map generation must not make support order-dependent."""
    conflicting = claim_fact_map().model_copy(
        update={
            "entries": (ClaimFactMappingEntry(claim_id="claim-2", fact_id="fact-2"),)
        }
    )

    result = validate(
        source_sheets=(source_sheet(),),
        fact_maps=(claim_fact_map(), conflicting),
        artifacts=(
            accepted_artifact("The policy reduced emissions by 12%", "claim-1"),
        ),
    )

    assert [finding.code for finding in result.findings] == [
        "CLAIM_NOT_CURRENT_VERIFIED"
    ]
    reversed_result = validate(
        source_sheets=(source_sheet(),),
        fact_maps=(conflicting, claim_fact_map()),
        artifacts=(
            accepted_artifact("The policy reduced emissions by 12%", "claim-1"),
        ),
    )
    assert [finding.code for finding in reversed_result.findings] == [
        "CLAIM_NOT_CURRENT_VERIFIED"
    ]


def test_gate_rejects_empty_exact_generation_coverage() -> None:
    """Omitting exact source/map/brief refs must never yield a passing report."""
    with pytest.raises(ValueError, match="exact generation coverage"):
        CitationIntegrityValidator().validate(
            source_sheets=(source_sheet(),),
            fact_maps=(claim_fact_map(),),
            artifacts=(
                accepted_artifact("The policy reduced emissions by 12%", "claim-1"),
            ),
        )


def test_gate_rejects_stale_blinded_brief_mapping() -> None:
    """Replacing the current brief ref must invalidate its otherwise valid map."""
    with pytest.raises(ValueError, match="blinded brief"):
        CitationIntegrityValidator().validate(
            source_sheets=(source_sheet(),),
            fact_maps=(claim_fact_map(),),
            artifacts=(
                accepted_artifact("The policy reduced emissions by 12%", "claim-1"),
            ),
            source_sheet_refs=(artifact_ref("curator-source-sheet"),),
            claim_fact_map_refs=(artifact_ref("claim-fact-map"),),
            blinded_brief_refs=(
                artifact_ref("blinded-brief").model_copy(
                    update={"content_hash": "c" * 64}
                ),
            ),
        )


def test_gate_rejects_stale_source_sheet_mapping() -> None:
    """Replacing the source generation must invalidate its otherwise valid map."""
    stale = claim_fact_map().model_copy(
        update={
            "source_sheet_ref": artifact_ref("curator-source-sheet").model_copy(
                update={"content_hash": "c" * 64}
            )
        }
    )
    with pytest.raises(ValueError, match="source sheet"):
        CitationIntegrityValidator().validate(
            source_sheets=(source_sheet(),),
            fact_maps=(stale,),
            artifacts=(
                accepted_artifact("The policy reduced emissions by 12%", "claim-1"),
            ),
            source_sheet_refs=(artifact_ref("curator-source-sheet"),),
            claim_fact_map_refs=(artifact_ref("claim-fact-map"),),
            blinded_brief_refs=(artifact_ref("blinded-brief"),),
        )


def test_gate_rejects_replayed_accepted_artifact_generation() -> None:
    """Two accepted payloads cannot reuse one immutable artifact generation."""
    artifact = accepted_artifact("The policy reduced emissions by 12%", "claim-1")
    with pytest.raises(ValueError, match="accepted artifact refs"):
        CitationIntegrityValidator().validate(
            source_sheets=(source_sheet(),),
            fact_maps=(claim_fact_map(),),
            artifacts=(artifact, artifact),
            source_sheet_refs=(artifact_ref("curator-source-sheet"),),
            claim_fact_map_refs=(artifact_ref("claim-fact-map"),),
            blinded_brief_refs=(artifact_ref("blinded-brief"),),
        )
