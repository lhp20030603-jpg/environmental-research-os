"""Enrollment is the authenticated first transition of every blind run."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from blind_artifact_helpers import CaseHarness, expert_sheet
from blind_signing_helpers import (
    enroll_controller,
    install_enrollment,
    prepare_enrollment,
)
from test_blind_cli import _reviewed_case
from test_blind_registry_security import write_case

import envresearch.benchmarks.blind_enrollment_controller as enrollment_module
from envresearch.benchmarks.blind_enrollment_marker import require_frozen_enrollment
from envresearch.benchmarks.blind_report import (
    BlindLineageInvalid,
    evaluate_blind_catalog,
)
from envresearch.benchmarks.blind_workflow import BlindEvaluationController
from envresearch.models.principal import PrincipalKind
from envresearch.research.isolated_order_publication import issue_isolated_order
from envresearch.workers import WorkerRole, WorkOrder


@pytest.mark.parametrize(
    "relative",
    (
        "artifacts/blind/pilot-001/current/source-sheet.json",
        "artifacts/blind/pilot-001/history/source-sheet/v1.json",
        "isolated/recommender/pilot-001/intruder.txt",
        "exchanges/recommender/pilot-001/work-orders/legacy.json",
        "control/queues/recommender/pilot-001/receipts/legacy.json",
        "control/queues/recommender/pilot-001/principals/legacy.capability",
        "control/queues/recommender/pilot-001/locks/foreign.filelock",
    ),
)
def test_every_run_prestate_blocks_first_enrollment(
    tmp_path: Path, relative: str
) -> None:
    run = tmp_path / "run"
    controller = BlindEvaluationController.from_case(
        write_case(tmp_path / "case"), run
    )
    path = run / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="first authenticated run transition"):
        enroll_controller(controller)


def test_interrupted_enrollment_retries_exact_signed_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = BlindEvaluationController.from_case(
        write_case(tmp_path / "case"), tmp_path / "run"
    )
    prepared = prepare_enrollment(controller)
    freeze = enrollment_module.freeze_enrollment
    calls = 0

    def interrupt(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated marker write interruption")
        return freeze(*args, **kwargs)

    monkeypatch.setattr(enrollment_module, "freeze_enrollment", interrupt)
    with pytest.raises(OSError, match="interruption"):
        install_enrollment(controller, prepared)
    install_enrollment(controller, prepared)

    assert require_frozen_enrollment(
        controller.registry, controller.case_id
    ).signed_sha256


def test_human_keys_without_signed_record_cannot_be_adopted(tmp_path: Path) -> None:
    controller = BlindEvaluationController.from_case(
        write_case(tmp_path / "case"), tmp_path / "run"
    )
    prepared = prepare_enrollment(controller)
    controller.registry.enroll_benchmark_humans(
        controller.case_id, prepared.signed.payload.participants
    )

    with pytest.raises(ValueError, match="first authenticated run transition"):
        controller.enroll_participants(prepared.signed)


def test_order_publication_reauthenticates_frozen_enrollment(tmp_path: Path) -> None:
    controller = BlindEvaluationController.from_case(
        write_case(tmp_path / "case"), tmp_path / "run"
    )
    enroll_controller(controller)
    assignment = controller._worker(PrincipalKind.RECOMMENDER)
    order = controller._order(
        "post-marker-order",
        WorkerRole.BENCHMARK_RECOMMENDER,
        (controller.loaded.brief_ref,),
        "envresearch.MethodRecommendationPayload",
        "method-recommendation.yaml",
        assignment,
    )
    marker = (
        controller.queue.control.path
        / "principals/benchmark/pilot-001/enrollment-frozen.json"
    )
    marker.unlink()

    with pytest.raises(TypeError, match="authorize"):
        issue_isolated_order(
            controller.queue,
            controller.recommender_workspace,
            order,
            (),
            authorize=lambda: None,  # type: ignore[call-arg]
        )
    with pytest.raises(ValueError, match="frozen blind enrollment is required"):
        issue_isolated_order(
            controller.queue,
            controller.recommender_workspace,
            order,
            (),
            enrollment_registry=controller.registry,
            case_id=controller.case_id,
        )
    assert not controller.queue.has_generation("post-marker-order")
    assert not controller.recommender_workspace.exists()


def test_concurrent_enrollment_freezes_one_immutable_marker(tmp_path: Path) -> None:
    controller = BlindEvaluationController.from_case(
        write_case(tmp_path / "case"), tmp_path / "run"
    )
    prepared = prepare_enrollment(controller)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            pool.map(
                controller.enroll_participants,
                (prepared.signed, prepared.signed),
            )
        )

    durable = require_frozen_enrollment(controller.registry, controller.case_id)
    assert {item.signed_sha256 for item in results} == {durable.signed_sha256}


def test_marker_tamper_blocks_restart_and_publication(tmp_path: Path) -> None:
    case = write_case(tmp_path / "case")
    run = tmp_path / "run"
    controller = BlindEvaluationController.from_case(case, run)
    enroll_controller(controller)
    marker = (
        run
        / "control/queues/recommender/pilot-001/principals/benchmark/pilot-001"
        / "enrollment-frozen.json"
    )
    document = json.loads(marker.read_text(encoding="utf-8"))
    document["mac"] = "0" * 64
    marker.write_text(
        json.dumps(document, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )

    restarted = BlindEvaluationController.from_case(case, run)
    with pytest.raises(ValueError, match="marker authentication failed"):
        restarted.replay_calibration()


def test_signed_enrollment_replacement_blocks_canonical_release(tmp_path: Path) -> None:
    case_root, run_root, controller = _reviewed_case(tmp_path)
    signed = (
        controller.queue.control.path
        / "principals/benchmark/pilot-001/signed-enrollment.json"
    )
    document = json.loads(signed.read_text(encoding="utf-8"))
    document["signed"]["payload"]["evaluation_id"] = "replacement"
    signed.write_text(
        json.dumps(document, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(BlindLineageInvalid, match="signature|enrollment"):
        evaluate_blind_catalog(case_root, run_root)


def test_signed_evidence_publication_reauthenticates_marker(tmp_path: Path) -> None:
    harness = CaseHarness(tmp_path)
    harness.through_recommendation()
    recommendation = harness.service.ref("case-rct", "recommendation")
    assignment = harness.human(PrincipalKind.EXPERT, 1)
    candidate = expert_sheet(recommendation, assignment.principal_id)
    marker = (
        harness.registry.control.path
        / "principals/benchmark/case-rct/enrollment-frozen.json"
    )
    marker.unlink()

    with pytest.raises(ValueError, match="frozen blind enrollment is required"):
        harness.signed_evidence(
            candidate,
            PrincipalKind.EXPERT,
            1,
            (recommendation,),
            "envresearch.ExpertScoreSheet",
        )
    assert not (
        tmp_path / harness.service.paths("case-rct").expert_one_evidence
    ).exists()


def test_enrolled_case_cannot_authorize_another_cases_expert_queue(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    first = BlindEvaluationController.from_case(
        write_case(tmp_path / "case-a", case_id="case-a"), run
    )
    enroll_controller(first)
    second = BlindEvaluationController.from_case(
        write_case(tmp_path / "case-b", case_id="case-b"), run
    )
    foreign_queue = second._queue("expert", f"{second.case_id}/1")
    order = WorkOrder(
        order_id="foreign-order",
        node_id="foreign-order",
        node_version="generation-1",
        role=WorkerRole.BENCHMARK_EXPERT,
        input_artifacts=(),
        expected_output_schema="envresearch.ExpertScoreSheet",
        expected_output_filenames=("expert-score.yaml",),
        policy_constraints=(),
        evidence_requirements=(),
    )

    with pytest.raises(ValueError, match="queue|assignment|case"):
        issue_isolated_order(
            foreign_queue,
            second.expert_workspaces[1],
            order,
            (),
            enrollment_registry=first.registry,
            case_id=first.case_id,
        )
    assert not foreign_queue.has_generation("foreign-order")
