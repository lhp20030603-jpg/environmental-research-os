"""Corpus-wide acceptance checks for the inert Pilot-8 blind calibration set."""

from __future__ import annotations

import json
from pathlib import Path

from blind_signing_helpers import enroll_controller

from envresearch.benchmarks.blind_registry import (
    BlindBenchmarkRegistry,
    LoadedBlindCase,
)
from envresearch.benchmarks.blind_release import ReleaseEvaluator
from envresearch.benchmarks.blind_workflow import BlindEvaluationController
from envresearch.benchmarks.leakage import LeakageScanner
from envresearch.models.benchmark_claims import ClaimVerificationStatus

PILOT_ROOT = Path("benchmarks/blind/pilot")
EXPECTED_CASES = {
    "pilot-case-001": ("rct", "2PPVRAL8"),
    "pilot-case-002": ("did_event_study", "R8ZTRJHE"),
    "pilot-case-003": ("rdd", "SUIXHCJJ"),
    "pilot-case-004": ("iv", "AL5UGD9H"),
    "pilot-case-005": ("synthetic_control", "JFVA32UJ"),
    "pilot-case-006": ("hedonic", "5IMPC5SG"),
    "pilot-case-007": ("measurement", "7G7FGMIR"),
    "pilot-case-008": (
        "systematic_review_meta_analysis",
        "UNTRYVQ3",
    ),
}
CASE_DIRECTORIES = {
    "pilot-case-001": "pilot-rct-energy-feedback",
    "pilot-case-002": "pilot-did-carbon-market",
    "pilot-case-003": "pilot-rdd-hazard-remediation",
    "pilot-case-004": "pilot-iv-air-pollution",
    "pilot-case-005": "pilot-scm-carbon-policy",
    "pilot-case-006": "pilot-hedonic-air-quality",
    "pilot-case-007": "pilot-measurement-emissions",
    "pilot-case-008": "pilot-meta-carbon-pricing",
}
EXPECTED_FACT_KINDS = {
    "institution",
    "timing",
    "unit",
    "outcome",
    "data",
    "constraint",
}
FORBIDDEN_PUBLIC_METHOD_LABELS = {
    "randomized controlled trial",
    "difference in differences",
    "event study",
    "regression discontinuity",
    "instrumental variable",
    "synthetic control",
    "hedonic",
    "meta analysis",
}


def _normalized_public_text(loaded: LoadedBlindCase) -> str:
    brief = loaded.blinded_brief.model_dump(mode="json")
    brief.pop("source_sheet_ref")
    return " ".join(
        json.dumps(brief, ensure_ascii=False)
        .casefold()
        .replace("-", " ")
        .replace("_", " ")
        .split()
    )


def test_pilot8_has_one_valid_leakage_free_case_per_family() -> None:
    """A missing, duplicate, invalid, or identity-leaking case blocks Pilot-8."""
    cases = BlindBenchmarkRegistry.discover(PILOT_ROOT)

    assert set(cases) == set(EXPECTED_CASES)
    assert {manifest.method_family for manifest in cases.values()} == {
        family for family, _ in EXPECTED_CASES.values()
    }
    for case_id, manifest in cases.items():
        loaded = BlindBenchmarkRegistry.load_case(manifest)
        expected_family, expected_item_key = EXPECTED_CASES[case_id]
        assert manifest.method_family == expected_family
        assert loaded.source_sheet.zotero_item_key == expected_item_key
        report = LeakageScanner().scan(
            loaded.source_sheet,
            loaded.blinded_brief,
            loaded.source_ref,
            loaded.brief_ref,
            "pilot-validator",
        )
        assert report.verdict == "pass", (case_id, report.findings)


def test_pilot8_claims_are_independently_verified_and_bijectively_mapped() -> None:
    """Unverified evidence or ambiguous claim-to-fact lineage blocks calibration."""
    cases = BlindBenchmarkRegistry.discover(PILOT_ROOT)

    for case_id, manifest in cases.items():
        loaded = BlindBenchmarkRegistry.load_case(manifest)
        claims = loaded.source_sheet.claims
        facts = loaded.blinded_brief.facts
        entries = loaded.claim_fact_map.entries
        assert len(claims) >= 6, case_id
        assert {fact.fact_kind for fact in facts} == EXPECTED_FACT_KINDS, case_id
        assert all(
            claim.status is ClaimVerificationStatus.CLAIM_VERIFIED
            and claim.extractor_principal != claim.verifier_principal
            and claim.source_item_key == loaded.source_sheet.zotero_item_key
            and claim.source_attachment_key == loaded.source_sheet.zotero_attachment_key
            and claim.source_content_hash == loaded.source_sheet.source_content_hash
            and len(claim.supporting_passage_hash) == 64
            for claim in claims
        ), case_id
        assert {entry.claim_id for entry in entries} == {
            claim.claim_id for claim in claims
        }, case_id
        assert {entry.fact_id for entry in entries} == {
            fact.fact_id for fact in facts
        }, case_id
        assert len(entries) == len(claims) == len(facts), case_id


def test_pilot8_public_briefs_omit_source_and_method_identifiers() -> None:
    """Public briefs must preserve design facts without revealing source identity."""
    cases = BlindBenchmarkRegistry.discover(PILOT_ROOT)

    for case_id, manifest in cases.items():
        loaded = BlindBenchmarkRegistry.load_case(manifest)
        public_text = _normalized_public_text(loaded)
        source = loaded.source_sheet
        source_identifiers = {
            source.zotero_item_key.casefold(),
            source.zotero_attachment_key.casefold(),
            source.doi.casefold(),
            source.title.casefold(),
            *(author.casefold() for author in source.authors),
        }
        assert not [value for value in source_identifiers if value in public_text], (
            case_id
        )
        assert not [
            label for label in FORBIDDEN_PUBLIC_METHOD_LABELS if label in public_text
        ], case_id


def test_pilot8_actual_recommender_handoff_uses_only_opaque_routing(
    tmp_path: Path,
) -> None:
    """Real worker bytes and paths must not expose curator-facing family labels."""
    forbidden_routes = tuple(CASE_DIRECTORIES.values())

    for index, (case_id, directory) in enumerate(CASE_DIRECTORIES.items(), start=1):
        run_root = tmp_path / "runs" / f"slot-{index}"
        controller = BlindEvaluationController.from_case(
            PILOT_ROOT / directory, run_root
        )
        enroll_controller(controller)
        controller.replay_calibration()
        public_brief = controller._public_brief()
        order = controller.queue.read_order("recommend-method")
        visible = "\n".join(
            (
                json.dumps(public_brief, sort_keys=True),
                order.model_dump_json(),
                str(controller.recommender_workspace),
                str(controller.recommender_workspace.relative_to(run_root)),
                str(controller.queue.root),
                str(controller.queue.control_root),
                *(
                    path.read_text(encoding="utf-8")
                    for path in sorted(controller.recommender_workspace.iterdir())
                ),
            )
        ).casefold()

        assert public_brief["case_id"] == case_id
        assert controller.recommender_workspace.name == case_id
        assert controller.queue.root.name == case_id
        assert not [marker for marker in forbidden_routes if marker in visible]

        restarted = BlindEvaluationController.from_case(
            PILOT_ROOT / directory, run_root
        )
        assert restarted.case_id == case_id
        assert restarted.recommender_workspace == controller.recommender_workspace
        assert restarted.queue.root == controller.queue.root


def test_pilot8_contains_no_replication_or_data_payloads() -> None:
    """The Tier-1 corpus contains descriptors only, never data or source payloads."""
    forbidden = {".dta", ".csv", ".parquet", ".rds", ".zip", ".pdf"}

    assert not [
        path
        for path in PILOT_ROOT.rglob("*")
        if path.is_file() and path.suffix.lower() in forbidden
    ]


def test_pilot8_evidence_is_calibration_only_and_release_stays_pending() -> None:
    """A complete Pilot-8 corpus cannot count as held-out release evidence."""
    cases = BlindBenchmarkRegistry.discover(PILOT_ROOT)
    readiness = ReleaseEvaluator().evaluate(())

    assert len(cases) == 8
    assert readiness.released is False
    assert readiness.held_out_cases == 0
    assert readiness.passed_cases == 0
    assert "requires at least 16 held-out cases" in readiness.blockers
    assert "requires at least 14 passed cases" in readiness.blockers
