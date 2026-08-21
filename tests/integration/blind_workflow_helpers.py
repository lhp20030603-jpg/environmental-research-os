"""Shared enrolled-controller fixtures for blind workflow tests."""

from __future__ import annotations

import json
from pathlib import Path

from blind_artifact_helpers import expert_sheet, recommendation
from blind_signing_helpers import enroll_controller, signed_candidate
from test_blind_registry_security import write_case

from envresearch.benchmarks.blind_workflow import BlindEvaluationController
from envresearch.models.benchmark_evaluation import (
    ExpertDimension,
    ExpertScoreSheet,
)
from envresearch.models.principal import PrincipalKind


def ready_for_recommendation(tmp_path: Path) -> BlindEvaluationController:
    controller = BlindEvaluationController.from_case(
        write_case(tmp_path / "case"), tmp_path / "run"
    )
    enroll_controller(controller)
    controller.replay_calibration()
    return controller


def valid_recommendation(controller: BlindEvaluationController):  # type: ignore[no-untyped-def]
    order = controller.queue.read_order("recommend-method")
    assignment = order.principal_assignment
    assert assignment is not None
    registry = json.loads(
        (controller.recommender_workspace / "method-profiles.json").read_text(
            encoding="utf-8"
        )
    )
    return recommendation(
        order.input_artifacts[0], order.input_artifacts[1], assignment.principal_id
    ).model_copy(update={"method_profile_registry_sha256": registry["registry_sha256"]})


def ready_for_expert_scoring(tmp_path: Path) -> BlindEvaluationController:
    controller = ready_for_recommendation(tmp_path)
    controller.accept_recommendation(valid_recommendation(controller))
    return controller


def score_for(
    controller: BlindEvaluationController, slot: int, *, conflict: bool = False
) -> object:
    score = raw_score_for(controller, slot, conflict=conflict)
    order = controller.expert_queues[slot].read_order(f"expert-score-{slot}")
    return signed_candidate(controller, order, score, PrincipalKind.EXPERT, slot)


def raw_score_for(
    controller: BlindEvaluationController, slot: int, *, conflict: bool = False
) -> ExpertScoreSheet:
    order = controller.expert_queues[slot].read_order(f"expert-score-{slot}")
    assignment = order.principal_assignment
    assert assignment is not None
    score = expert_sheet(order.input_artifacts[1], assignment.principal_id)
    if not conflict:
        return score
    scores = tuple(
        item.model_copy(
            update={
                "score": 1
                if item.dimension is ExpertDimension.IDENTIFICATION_FIT
                else item.score
            }
        )
        for item in score.scores
    )
    return score.model_copy(update={"scores": scores, "verdict": "fail"})
