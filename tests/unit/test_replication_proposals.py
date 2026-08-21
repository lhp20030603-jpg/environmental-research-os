"""Strict distinction between dry metadata and executable intake authority."""

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from envresearch.replication import Tier2DryProposal, load_replication_proposal


def _dry_payload() -> dict[str, object]:
    return {
        "schema_version": "tier2-dry-proposal-v1",
        "proposal_kind": "dry",
        "package_id": "jel-did-2026",
        "admission_status": "proposed",
        "target_work": {
            "title": "Difference-in-Differences Designs: A Practitioner's Guide",
            "authors": [
                "Andrew Baker",
                "Brantly Callaway",
                "Scott Cunningham",
                "Andrew Goodman-Bacon",
                "Pedro H. C. Sant'Anna",
            ],
            "journal": "Journal of Economic Literature",
            "publication_year": 2026,
            "volume": 64,
            "issue": 2,
            "pages": "498-557",
            "doi": "10.1257/jel.20251650",
            "article_url": "https://www.aeaweb.org/articles?id=10.1257/jel.20251650",
            "package_landing_url": "https://github.com/pedrohcgs/JEL-DiD",
        },
        "runtime_requirement": {"language": "R", "profile_id": "r-did-v1"},
        "metadata_verified_on": "2026-08-10",
        "metadata_source_urls": [
            "https://www.aeaweb.org/articles?id=10.1257/jel.20251650"
        ],
        "unresolved_blockers": [
            {"code": "archive-direct-locator-unapproved", "detail": "Unapproved."},
            {"code": "archive-sha256-unobserved", "detail": "Unobserved."},
            {"code": "license-scope-unverified", "detail": "Unverified."},
            {"code": "self-contained-status-unverified", "detail": "Unverified."},
            {"code": "pinned-runtime-image-unapproved", "detail": "Unapproved."},
        ],
    }


def _write(path: Path, payload: object) -> Path:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_dry_proposal_contract_cannot_claim_executable_authority(
    tmp_path: Path,
) -> None:
    proposal = _write(tmp_path / "dry.yaml", _dry_payload())

    loaded = load_replication_proposal(proposal)

    assert isinstance(loaded, Tier2DryProposal)
    assert loaded.admission_status == "proposed"
    for forbidden in ("approved_locator", "archive_sha256", "image_digest"):
        forged = {**_dry_payload(), forbidden: "not-authoritative"}
        _write(tmp_path / "forged.yaml", forged)
        with pytest.raises(ValueError, match="proposal"):
            load_replication_proposal(tmp_path / "forged.yaml")


def test_checked_in_jel_proposal_is_dry_public_metadata_only() -> None:
    path = Path("benchmarks/replication/proposals/jel-did-2026.yaml")

    proposal = load_replication_proposal(path)

    assert isinstance(proposal, Tier2DryProposal)
    assert proposal.target_work.doi == "10.1257/jel.20251650"
    assert (proposal.target_work.volume, proposal.target_work.issue) == (64, 2)
    assert proposal.target_work.pages == "498-557"
    assert proposal.runtime_requirement.language == "R"
    assert {item.code for item in proposal.unresolved_blockers} >= {
        "archive-direct-locator-unapproved",
        "archive-sha256-unobserved",
        "license-scope-unverified",
        "self-contained-status-unverified",
        "pinned-runtime-image-unapproved",
    }
    raw = path.read_text(encoding="utf-8")
    for forbidden in (
        "approved_locator:",
        "archive_sha256:",
        "image_digest:",
        "license_name:",
        "self_contained:",
    ):
        assert forbidden not in raw
