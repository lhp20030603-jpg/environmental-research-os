"""Blind-only claim-integrity policy and mutation tests."""

from __future__ import annotations

import hashlib

import pytest
from test_claim_integrity import artifact_ref, claim_fact_map, source_sheet, validate

from envresearch.models.benchmark_claims import ClaimUsage
from envresearch.models.benchmark_evaluation import AcceptedArtifactClaims


def recommendation_artifact(
    *,
    fact_id: str = "fact-1",
    claim_id: str = "claim-1",
    extra_payload: dict[str, object] | None = None,
    extra_usages: tuple[ClaimUsage, ...] = (),
) -> AcceptedArtifactClaims:
    """Return one blind recommendation with exact structured fact linkage."""
    payload: dict[str, object] = {
        "fact_refs": [fact_id],
        "estimand_interpretation": "Estimate the intervention effect.",
        **(extra_payload or {}),
    }
    usage = ClaimUsage(
        claim_id=claim_id,
        statement_sha256=hashlib.sha256(fact_id.encode()).hexdigest(),
        json_pointer="/fact_refs/0",
    )
    return AcceptedArtifactClaims(
        artifact_ref=artifact_ref("method-recommendation"),
        payload=payload,
        usages=(usage, *extra_usages),
    )


@pytest.mark.parametrize(
    ("artifact_id", "payload"),
    (
        ("analysis-plan", {"arbitrary": {"content_hash": "Smith (2020) reports 25%."}}),
        (
            "analysis-plan",
            {"nested": {"recommender_principal": "Smith (2020) reports 25%."}},
        ),
        ("analysis-plan", {"method_profile_registry_sha256": "1" + "a" * 63}),
        ("method-recommendation", {"blinded_brief_ref": {"content_hash": "A" * 64}}),
        ("method-recommendation", {"method_profile_registry_sha256": "not-a-digest"}),
        (
            "method-recommendation",
            {"method_candidates": {"estimand_ref": "Smith (2020)"}},
        ),
        (
            "method-recommendation",
            {"recommender_principal": "Smith (2020) reports 25%."},
        ),
    ),
)
def test_structural_names_only_bypass_claims_in_canonical_recommendation_fields(
    artifact_id: str, payload: dict[str, object]
) -> None:
    """Moving or mutating a structural value must restore claim requirements."""
    artifact = (
        recommendation_artifact(extra_payload=payload)
        if artifact_id == "method-recommendation"
        else AcceptedArtifactClaims(
            artifact_ref=artifact_ref(artifact_id), payload=payload, usages=()
        )
    )

    result = validate(
        source_sheets=(source_sheet(),),
        fact_maps=(claim_fact_map(),),
        artifacts=(artifact,),
    )

    assert result.passed is False
    expected = (
        "BLIND_SOURCE_PROSE_FORBIDDEN"
        if artifact_id == "method-recommendation"
        else "SOURCE_STATEMENT_UNBOUND"
    )
    assert [finding.code for finding in result.findings] == [expected]


def test_canonical_recommendation_structure_needs_no_extra_claim_usage() -> None:
    """Typed identifiers and digests add no usages beyond the exact fact leaf."""
    artifact = recommendation_artifact(
        extra_payload={
            "blinded_brief_ref": {"content_hash": "1" + "a" * 63},
            "leakage_report_ref": {"content_hash": "2" + "b" * 63},
            "method_profile_registry_sha256": "3" + "c" * 63,
            "method_candidates": {"estimand_ref": "estimand-001"},
            "recommender_principal": "principal-pilot-001-recommender",
        },
    )

    result = validate(
        source_sheets=(source_sheet(),),
        fact_maps=(claim_fact_map(),),
        artifacts=(artifact,),
    )

    assert result.passed is True
    assert result.findings == ()


def test_blind_gate_accepts_only_exact_mapped_fact_reference_leaf() -> None:
    """Removing exact fact-to-claim validation must reject a blind recommendation."""
    result = validate(
        source_sheets=(source_sheet(),),
        fact_maps=(claim_fact_map(),),
        artifacts=(recommendation_artifact(),),
    )

    assert result.passed is True
    assert result.findings == ()
    assert result.validator_version == "blind-claim-integrity-v2"


def test_blind_gate_rejects_source_claim_prose_even_when_bound_to_valid_fact() -> None:
    """A valid claim usage must not turn arbitrary quantitative prose into support."""
    statement = "This intervention increased mortality by 999 percent. fact-1"
    prose_usage = ClaimUsage(
        claim_id="claim-1",
        statement_sha256=hashlib.sha256(statement.encode()).hexdigest(),
        json_pointer="/estimand_interpretation",
    )
    result = validate(
        source_sheets=(source_sheet(),),
        fact_maps=(claim_fact_map(),),
        artifacts=(
            recommendation_artifact(
                extra_payload={"estimand_interpretation": statement},
                extra_usages=(prose_usage,),
            ),
        ),
    )

    assert result.passed is False
    assert {finding.code for finding in result.findings} == {
        "BLIND_FACT_IN_PROSE",
        "BLIND_SOURCE_PROSE_FORBIDDEN",
        "CLAIM_USAGE_UNBOUND",
    }


def test_blind_gate_rejects_mapping_that_mimics_fact_refs_array_pointer() -> None:
    """Replacing the fact_refs array with a mapping must invalidate the report."""
    artifact = recommendation_artifact().model_copy(
        update={
            "payload": {
                "fact_refs": {"0": "fact-1"},
                "estimand_interpretation": "Estimate the intervention effect.",
            }
        }
    )

    result = validate(
        source_sheets=(source_sheet(),),
        fact_maps=(claim_fact_map(),),
        artifacts=(artifact,),
    )

    assert result.passed is False
    assert "BLIND_FACT_REFS_INVALID" in {finding.code for finding in result.findings}


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("missing-usage", "SOURCE_STATEMENT_UNBOUND"),
        ("duplicate-fact", "BLIND_FACT_REFS_INVALID"),
        ("unmapped-fact", "CLAIM_NOT_CURRENT_VERIFIED"),
        ("claim-substitution", "CLAIM_NOT_CURRENT_VERIFIED"),
        ("forged-hash", "CLAIM_USAGE_UNBOUND"),
        ("wrong-pointer", "CLAIM_USAGE_UNBOUND"),
        ("wrong-artifact", "CLAIM_USAGE_UNBOUND"),
    ),
)
def test_blind_gate_rejects_invalid_fact_linkage_mutations(
    mutation: str, expected_code: str
) -> None:
    """Each mutation must break the mechanical fact-to-current-claim contract."""
    artifact = recommendation_artifact()
    if mutation == "missing-usage":
        artifact = artifact.model_copy(update={"usages": ()})
    elif mutation == "duplicate-fact":
        second = artifact.usages[0].model_copy(update={"json_pointer": "/fact_refs/1"})
        artifact = artifact.model_copy(
            update={
                "payload": {
                    "fact_refs": ["fact-1", "fact-1"],
                    "estimand_interpretation": "Estimate the intervention effect.",
                },
                "usages": (*artifact.usages, second),
            }
        )
    elif mutation == "unmapped-fact":
        artifact = recommendation_artifact(fact_id="fact-999")
    elif mutation == "claim-substitution":
        artifact = recommendation_artifact(claim_id="claim-999")
    elif mutation == "forged-hash":
        artifact = artifact.model_copy(
            update={
                "usages": (
                    artifact.usages[0].model_copy(
                        update={"statement_sha256": "c" * 64}
                    ),
                )
            }
        )
    elif mutation == "wrong-pointer":
        artifact = artifact.model_copy(
            update={
                "usages": (
                    artifact.usages[0].model_copy(
                        update={"json_pointer": "/estimand_interpretation"}
                    ),
                )
            }
        )
    elif mutation == "wrong-artifact":
        artifact = artifact.model_copy(
            update={"artifact_ref": artifact_ref("analysis-plan")}
        )

    result = validate(
        source_sheets=(source_sheet(),),
        fact_maps=(claim_fact_map(),),
        artifacts=(artifact,),
    )

    assert result.passed is False
    assert expected_code in {finding.code for finding in result.findings}


@pytest.mark.parametrize(
    "statement",
    (
        "999",
        "The measured response was 999 percent larger.",
        "Author One (2024) reports an effect.",
        "doi:10.1000/example.policy",
        "fact-1 motivates this choice.",
    ),
)
def test_blind_gate_forbids_source_markers_and_fact_ids_in_prose(
    statement: str,
) -> None:
    """Blind analytical prose cannot acquire evidence semantics from a usage."""
    artifact = recommendation_artifact(
        extra_payload={"estimand_interpretation": statement}
    )

    result = validate(
        source_sheets=(source_sheet(),),
        fact_maps=(claim_fact_map(),),
        artifacts=(artifact,),
    )

    assert result.passed is False
    assert {finding.code for finding in result.findings} & {
        "BLIND_FACT_IN_PROSE",
        "BLIND_SOURCE_PROSE_FORBIDDEN",
    }
