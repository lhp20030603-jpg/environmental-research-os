"""Adversarial checks for blinded-brief source leakage."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from envresearch.benchmarks.leakage import LeakageScanner, _walk_strings
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_blinding import BlindedBrief, BlindedFact
from envresearch.models.benchmark_claims import (
    ClaimVerificationStatus,
    CuratorSourceSheet,
    RestrictedTerm,
    SourceLocator,
    VerifiedClaim,
)

SHA256 = "a" * 64
UTC_TIME = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)
DISTINCTIVE_PHRASE = "alpha beta gamma delta epsilon zeta eta theta"


def artifact_ref(artifact_id: str) -> ArtifactRef:
    """Return one immutable artifact reference."""
    return ArtifactRef(artifact_id=artifact_id, artifact_version=1, content_hash=SHA256)


def source_sheet(*, doi: str = "10.1000/example.policy") -> CuratorSourceSheet:
    """Return a source sheet with configured leakage triggers only."""
    claim = VerifiedClaim(
        claim_id="claim-001",
        normalized_claim="The policy applies to eligible facilities.",
        source_item_key="2PPVRAL8",
        source_attachment_key="7S8T9UVW",
        source_content_hash=SHA256,
        locator=SourceLocator(page=12),
        supporting_passage_hash="b" * 64,
        status=ClaimVerificationStatus.CLAIM_VERIFIED,
        extractor_principal="extractor-1",
        verifier_principal="verifier-1",
        verified_at=UTC_TIME,
    )
    return CuratorSourceSheet(
        case_id="pilot-001",
        method_family="difference-in-differences",
        zotero_item_key="2PPVRAL8",
        zotero_attachment_key="7S8T9UVW",
        doi=doi,
        title="A policy evaluation",
        authors=("Author One",),
        source_content_hash=SHA256,
        source_generation=1,
        institutional_context=("Eligible facilities report annual emissions.",),
        restricted_terms=(
            RestrictedTerm(term="Allcott Rogers home energy report", rationale="id"),
            RestrictedTerm(term="Opower household panel", rationale="data"),
            RestrictedTerm(term="Use regression discontinuity", rationale="method"),
            RestrictedTerm(
                term="The effect was statistically significant", rationale="result"
            ),
        ),
        distinctive_phrase_hashes=(
            hashlib.sha256(DISTINCTIVE_PHRASE.encode()).hexdigest(),
        ),
        claims=(claim,),
    )


def blinded_brief() -> BlindedBrief:
    """Return an otherwise source-blind method-selection brief."""
    return BlindedBrief(
        case_id="pilot-001",
        source_sheet_ref=artifact_ref("curator-source-sheet"),
        policy_setting="An emissions reporting requirement.",
        population="Eligible regulated facilities.",
        unit="Facility-year",
        treatment_or_exposure="Requirement adoption date.",
        timing="Annual observations before and after adoption.",
        candidate_outcomes=("Reported emissions",),
        data_structures=("Facility-level annual panel",),
        available_variables=("Reported emissions", "adoption year"),
        institutional_rules=("Eligibility follows a capacity threshold.",),
        constraints=("No randomized assignment is available.",),
        facts=(
            BlindedFact(
                fact_id="fact-001",
                statement="Facilities report annual emissions after eligibility.",
                fact_kind="institution",
            ),
        ),
        masker_principal="masker-1",
    )


@pytest.mark.parametrize(
    ("mutation", "category"),
    (
        ({"policy_setting": "Allcott Rogers home energy report"}, "identity"),
        ({"data_structures": ("Opower household panel",)}, "dataset"),
        ({"institutional_rules": ("Use regression discontinuity",)}, "method"),
        (
            {"constraints": ("The effect was statistically significant",)},
            "result",
        ),
    ),
)
def test_scanner_rejects_restricted_terms(
    mutation: dict[str, object], category: str
) -> None:
    """Configured source-specific terms reject every matching brief field."""
    report = LeakageScanner().scan(
        source_sheet(),
        blinded_brief().model_copy(update=mutation),
        artifact_ref("source"),
        artifact_ref("brief"),
        "validator-1",
    )

    assert report.verdict == "rejected"
    assert category in {finding.category.value for finding in report.findings}


def test_scanner_normalizes_unicode_case_and_punctuation() -> None:
    """Equivalent Unicode and punctuation cannot conceal a configured term."""
    report = LeakageScanner().scan(
        source_sheet(),
        blinded_brief().model_copy(
            update={
                "policy_setting": "ＡＬＬＣＯＴＴ—ＲＯＧＥＲＳ  home, energy report"
            }
        ),
        artifact_ref("source"),
        artifact_ref("brief"),
        "validator-1",
    )

    assert report.verdict == "rejected"
    assert any(finding.category.value == "identity" for finding in report.findings)


@pytest.mark.parametrize(
    "doi_value",
    ("doi:10.1000/example.policy", "https%3A%2F%2Fdoi.org%2F10.1000%2Fexample.policy"),
)
def test_scanner_rejects_doi_prefixes_and_encodings(doi_value: str) -> None:
    """DOI presentation and URL encoding both expose source provenance."""
    report = LeakageScanner().scan(
        source_sheet(),
        blinded_brief().model_copy(update={"constraints": (doi_value,)}),
        artifact_ref("source"),
        artifact_ref("brief"),
        "validator-1",
    )

    assert report.verdict == "rejected"
    assert any(finding.category.value == "citation" for finding in report.findings)


def test_scanner_rejects_doi_with_ordinary_trailing_punctuation() -> None:
    """A sentence terminator cannot conceal a DOI source identifier."""
    report = LeakageScanner().scan(
        source_sheet(),
        blinded_brief().model_copy(
            update={"constraints": ("doi:10.1000/example.policy.",)}
        ),
        artifact_ref("source"),
        artifact_ref("brief"),
        "validator-1",
    )

    assert report.verdict == "rejected"
    assert any(finding.category.value == "citation" for finding in report.findings)


def test_scanner_matches_source_doi_with_valid_terminal_punctuation() -> None:
    """A valid terminal DOI character survives presentation punctuation trimming."""
    report = LeakageScanner().scan(
        source_sheet(doi="10.1000/example."),
        blinded_brief().model_copy(update={"constraints": ("doi:10.1000/example..",)}),
        artifact_ref("source"),
        artifact_ref("brief"),
        "validator-1",
    )

    assert report.verdict == "rejected"
    assert any(finding.category.value == "citation" for finding in report.findings)


@pytest.mark.parametrize("key", ("title", "authors", "zotero_item_key"))
def test_scanner_rejects_hidden_provenance_serialized_keys(key: str) -> None:
    """Curator-only metadata keys are rejected even before their values are read."""
    findings = LeakageScanner()._hidden_metadata_findings(
        tuple(_walk_strings({key: "opaque"}))
    )

    assert len(findings) == 1
    assert findings[0].category.value == "hidden_metadata"
    assert findings[0].locator == f"/{key}"


def test_scanner_rejects_matching_normalized_eight_token_phrase_hash() -> None:
    """An exact normalized eight-token fingerprint rejects paraphrase formatting."""
    report = LeakageScanner().scan(
        source_sheet(),
        blinded_brief().model_copy(
            update={"constraints": ("Alpha, beta gamma delta epsilon zeta eta theta",)}
        ),
        artifact_ref("source"),
        artifact_ref("brief"),
        "validator-1",
    )

    assert report.verdict == "rejected"
    assert any(finding.category.value == "phrase" for finding in report.findings)


def test_scanner_preserves_unconfigured_institutional_method_fact() -> None:
    """Method prerequisites remain available unless a source rule restricts them."""
    report = LeakageScanner().scan(
        source_sheet(),
        blinded_brief().model_copy(
            update={"institutional_rules": ("Treatment changes at a threshold.",)}
        ),
        artifact_ref("source"),
        artifact_ref("brief"),
        "validator-1",
    )

    assert report.verdict == "pass"
    assert report.findings == ()
    assert len(report.scanner_config_sha256) == 64


def test_scanner_does_not_treat_authenticated_lineage_hash_as_public_prose() -> None:
    """Opaque exact refs are authenticated separately and cannot create random leaks."""
    source = source_sheet().model_copy(
        update={
            "restricted_terms": (RestrictedTerm(term="149", rationale="source result"),)
        }
    )
    source_ref = ArtifactRef(
        artifact_id="curator-source-sheet",
        artifact_version=1,
        content_hash="0" * 20 + "149" + "a" * 41,
    )
    brief = blinded_brief().model_copy(update={"source_sheet_ref": source_ref})

    report = LeakageScanner().scan(
        source,
        brief,
        source_ref,
        artifact_ref("brief"),
        "validator-1",
    )

    assert report.verdict == "pass"
    assert report.findings == ()


def test_scanner_uses_the_attested_canonical_public_projection() -> None:
    """Nonpublic principals cannot alter verdicts and projection changes are attested."""
    source = source_sheet().model_copy(
        update={
            "restricted_terms": (
                RestrictedTerm(term="masker-1", rationale="private principal"),
            )
        }
    )

    report = LeakageScanner().scan(
        source,
        blinded_brief(),
        artifact_ref("source"),
        artifact_ref("brief"),
        "validator-1",
    )

    assert report.verdict == "pass"
    assert report.scanner_version == "blind-leakage-v2"
    assert LeakageScanner.scanner_config_sha256 == (
        "434bfea0a81d85ce29479928ca76d1f32a836f1ea718499921a9fff15fe1c65c"
    )
