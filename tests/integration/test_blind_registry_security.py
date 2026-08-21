"""Security and provenance tests for descriptor-pinned blind cases."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from envresearch.benchmarks.blind_registry import BlindBenchmarkRegistry

SHA256 = "a" * 64
PASSAGE_SHA256 = "b" * 64


def _reference(artifact_id: str, content: bytes) -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "artifact_version": 1,
        "content_hash": hashlib.sha256(content).hexdigest(),
    }


def _source_sheet(
    case_id: str = "pilot-001",
    method_family: str = "difference-in-differences",
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "method_family": method_family,
        "zotero_item_key": "2PPVRAL8",
        "zotero_attachment_key": "7S8T9UVW",
        "doi": "10.1000/example.policy",
        "title": "A policy evaluation",
        "authors": ["Author One"],
        "source_content_hash": SHA256,
        "source_generation": 1,
        "institutional_context": ["Eligible facilities report annual emissions."],
        "restricted_terms": [
            {"term": "example policy", "rationale": "Source title wording"}
        ],
        "distinctive_phrase_hashes": [PASSAGE_SHA256],
        "claims": [
            {
                "claim_id": "claim-001",
                "normalized_claim": "The policy applies to eligible facilities.",
                "source_item_key": "2PPVRAL8",
                "source_attachment_key": "7S8T9UVW",
                "source_content_hash": SHA256,
                "locator": {"page": 12, "section": "Eligibility", "paragraph": 2},
                "supporting_passage_hash": PASSAGE_SHA256,
                "status": "claim_verified",
                "extractor_principal": "extractor-1",
                "verifier_principal": "verifier-1",
                "verified_at": datetime(2026, 8, 9, 8, 0, tzinfo=UTC),
            }
        ],
    }


def _brief(
    source_ref: dict[str, object], case_id: str = "pilot-001"
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "source_sheet_ref": source_ref,
        "policy_setting": "A facility eligibility rule changed.",
        "population": "Eligible facilities",
        "unit": "Facility-year",
        "treatment_or_exposure": "Eligibility after the rule change",
        "timing": "Annual observations before and after the change",
        "candidate_outcomes": ["Reported emissions"],
        "data_structures": ["Facility-level panel"],
        "available_variables": ["Eligibility status"],
        "institutional_rules": ["Eligibility follows a published threshold"],
        "constraints": ["No randomized assignment"],
        "facts": [
            {
                "fact_id": "fact-001",
                "statement": "Eligibility is observable for each facility-year.",
                "fact_kind": "constraint",
            }
        ],
        "masker_principal": "masker-1",
    }


def write_case(
    root: Path,
    *,
    tier: object = 1,
    case_id: str = "pilot-001",
    method_family: str = "difference-in-differences",
) -> Path:
    """Create one complete inert blind case beneath ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    source_path = root / "curator-source-sheet.yaml"
    source_path.write_text(
        yaml.safe_dump(_source_sheet(case_id, method_family)), encoding="utf-8"
    )
    source = source_path.read_bytes()
    source_ref = _reference("curator-source-sheet", source)

    brief_path = root / "blinded-brief.yaml"
    brief_path.write_text(yaml.safe_dump(_brief(source_ref, case_id)), encoding="utf-8")
    brief = brief_path.read_bytes()
    brief_ref = _reference("blinded-brief", brief)

    claim_map = {
        "case_id": case_id,
        "source_sheet_ref": source_ref,
        "blinded_brief_ref": brief_ref,
        "entries": [{"claim_id": "claim-001", "fact_id": "fact-001"}],
        "mapper_principal": "masker-1",
    }
    (root / "claim-fact-map.yaml").write_text(
        yaml.safe_dump(claim_map), encoding="utf-8"
    )
    manifest = {
        "id": case_id,
        "version": "1.0",
        "tier": tier,
        "method_family": method_family,
        "license": "CC0-1.0",
        "source_sheet": "curator-source-sheet.yaml",
        "claim_fact_map": "claim-fact-map.yaml",
        "blinded_brief": "blinded-brief.yaml",
        "rubric_version": "blind-method-v1",
        "executes_replication_package": False,
    }
    (root / "benchmark.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root


@pytest.mark.parametrize("tier", (0, 2, True, "1"))
def test_blind_manifest_requires_exact_tier_one(tmp_path: Path, tier: object) -> None:
    """Tier coercion cannot admit non-Tier-1 blind cases."""
    write_case(tmp_path, tier=tier)

    with pytest.raises(ValueError, match="Tier 1"):
        BlindBenchmarkRegistry.discover(tmp_path)


@pytest.mark.parametrize("executes", (True, 0, 1, "false", None))
def test_blind_manifest_requires_exact_false_execution_flag(
    tmp_path: Path, executes: object
) -> None:
    """Only a literal false wire value can keep a blind case inert in V0.2."""
    case = write_case(tmp_path)
    manifest_path = case / "benchmark.yaml"
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    payload["executes_replication_package"] = executes
    manifest_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="replication package execution"):
        BlindBenchmarkRegistry.discover(case)


def test_registry_rejects_symlinked_source_sheet(tmp_path: Path) -> None:
    """Pinned discovery never follows a source sheet alias."""
    case = write_case(tmp_path)
    source = case / "curator-source-sheet.yaml"
    outside = tmp_path / "outside.yaml"
    outside.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink|regular"):
        BlindBenchmarkRegistry.discover(case)


def test_registry_rejects_hard_linked_brief(tmp_path: Path) -> None:
    """A multi-link blind input cannot acquire an externally mutable alias."""
    case = write_case(tmp_path)
    brief = case / "blinded-brief.yaml"
    outside = tmp_path / "outside.yaml"
    os.link(brief, outside)

    with pytest.raises(ValueError, match="link count|regular"):
        BlindBenchmarkRegistry.discover(case)


def test_load_case_returns_content_derived_refs(tmp_path: Path) -> None:
    """Consumers receive the canonical references parsed artifact links require."""
    case = write_case(tmp_path)
    manifest = BlindBenchmarkRegistry.discover(case)["pilot-001"]

    loaded = BlindBenchmarkRegistry.load_case(manifest)

    assert (
        loaded.source_ref.content_hash
        == hashlib.sha256((case / "curator-source-sheet.yaml").read_bytes()).hexdigest()
    )
    assert (
        loaded.brief_ref.content_hash
        == hashlib.sha256((case / "blinded-brief.yaml").read_bytes()).hexdigest()
    )
    assert loaded.claim_fact_map.source_sheet_ref == loaded.source_ref
    assert loaded.claim_fact_map.blinded_brief_ref == loaded.brief_ref


def test_load_case_rejects_cross_case_artifact_references(tmp_path: Path) -> None:
    """Case artifacts must bind to the exact content loaded from this descriptor."""
    case = write_case(tmp_path)
    manifest = BlindBenchmarkRegistry.discover(case)["pilot-001"]
    map_path = case / "claim-fact-map.yaml"
    payload = yaml.safe_load(map_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    payload["source_sheet_ref"]["content_hash"] = "c" * 64
    map_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="source_sheet_ref"):
        BlindBenchmarkRegistry.load_case(manifest)
