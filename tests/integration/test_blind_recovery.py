"""Pilot calibration recovery and scientific-release boundary regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from blind_signing_helpers import enroll_controller
from test_blind_workflow import valid_recommendation

from envresearch.benchmarks.blind_registry import BlindBenchmarkRegistry
from envresearch.benchmarks.blind_release import ReleaseEvaluator
from envresearch.benchmarks.blind_report import (
    BlindReviewRequired,
    evaluate_blind_catalog,
    validate_blind_case,
)
from envresearch.benchmarks.blind_workflow import BlindEvaluationController
from envresearch.models.benchmark_claims import CuratorSourceSheet

PILOT_RDD = Path("benchmarks/blind/pilot/pilot-rdd-hazard-remediation")


def test_source_revision_recomputes_only_current_calibration_inputs(
    tmp_path: Path,
) -> None:
    """A source revision must revoke its order and every published descendant."""
    run_root = tmp_path / "run"
    controller = BlindEvaluationController.from_case(PILOT_RDD, run_root)
    enroll_controller(controller)
    controller.replay_calibration()
    controller.accept_recommendation(valid_recommendation(controller))
    first_order = controller.queue.read_order("recommend-method")
    first_source_ref = controller.source_ref()
    replacement = controller.loaded.source_sheet.model_copy(
        update={"source_generation": 2}
    )

    controller.revise_source(replacement, revision_id="source-r2")

    assert set(controller.status().stale_nodes) == {
        "mask-brief",
        "validate-leakage",
        "recommend-method",
        "verify-citations",
    }
    assert not controller.queue.has_generation("recommend-method")
    archived_order = controller.queue.exchange.read_file(
        Path("revisions/source-r2/worker/work-orders/recommend-method.json"),
        description="archived Pilot-8 recommendation order",
    )
    assert json.loads(archived_order)["order_hash"] == first_order.order_hash
    assert (
        run_root
        / "revisions/source-r2/isolated/recommender/pilot-case-003/work-order.json"
    ).is_file()

    recomputed = controller.recompute_ready()
    current_source = controller.artifacts.lifecycle.read_payload(
        controller.artifacts.paths(controller.case_id).source_sheet,
        CuratorSourceSheet,
    )
    replacement_order = controller.queue.read_order("recommend-method")

    assert recomputed.completed_nodes == (
        "curate-source",
        "mask-brief",
        "validate-leakage",
    )
    assert recomputed.stale_nodes == ("recommend-method", "verify-citations")
    assert recomputed.current_lineage is False
    assert current_source.source_generation == 2
    assert controller.source_ref() != first_source_ref
    assert replacement_order.node_version == "generation-2"
    assert replacement_order.input_artifacts == controller.allowed_recommendation_refs()


def test_pilot_case_without_human_scores_stays_release_pending(
    tmp_path: Path,
) -> None:
    """Descriptor validation cannot substitute for two real expert reviews."""
    validation = validate_blind_case(PILOT_RDD)
    controller = BlindEvaluationController.from_case(PILOT_RDD, tmp_path / "run")
    enroll_controller(controller)
    controller.replay_calibration()
    controller.accept_recommendation(valid_recommendation(controller))

    with pytest.raises(BlindReviewRequired, match="two expert reviews are required"):
        evaluate_blind_catalog(PILOT_RDD, tmp_path / "run")

    readiness = ReleaseEvaluator().evaluate(())
    assert validation.valid is True
    assert validation.case_id == "pilot-case-003"
    assert readiness.released is False
    assert readiness.held_out_cases == 0
    assert readiness.passed_cases == 0
    assert "requires at least 16 held-out cases" in readiness.blockers
    assert "requires at least 14 passed cases" in readiness.blockers


def test_pilot8_catalog_has_no_held_out_release_cohort() -> None:
    """Eight calibrated descriptors remain a pilot, never a held-out cohort."""
    manifests = BlindBenchmarkRegistry.discover(Path("benchmarks/blind/pilot"))
    readiness = ReleaseEvaluator().evaluate(())

    assert len(manifests) == 8
    assert all(case_id.startswith("pilot-case-") for case_id in manifests)
    assert readiness.released is False
    assert readiness.held_out_cases == 0
