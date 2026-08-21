"""CLI and report integration for authenticated blind benchmark state."""

from __future__ import annotations

import json
from pathlib import Path

from blind_signing_helpers import enroll_controller
from test_blind_registry_security import write_case
from test_blind_workflow import _score_for, valid_recommendation
from typer.testing import CliRunner

from envresearch.benchmarks.blind_report import evaluate_blind_catalog
from envresearch.benchmarks.blind_scoring import ReleaseEvaluator
from envresearch.benchmarks.blind_workflow import BlindEvaluationController
from envresearch.cli import app
from envresearch.models.benchmark_evaluation import PosthocComparison
from envresearch.models.principal import PrincipalKind

CLI = CliRunner()


def _reviewed_case(
    tmp_path: Path, *, case_id: str = "pilot-001"
) -> tuple[Path, Path, BlindEvaluationController]:
    case_root = write_case(tmp_path / "case", case_id=case_id)
    run_root = tmp_path / "run"
    controller = BlindEvaluationController.from_case(case_root, run_root)
    enroll_controller(controller)
    controller.replay_calibration()
    controller.accept_recommendation(valid_recommendation(controller))
    controller.issue_expert_orders()
    controller.accept_expert_score(1, _score_for(controller, 1))
    controller.accept_expert_score(2, _score_for(controller, 2))
    adjudicator = controller._human(PrincipalKind.ADJUDICATOR, 1)
    controller.artifacts.publish_posthoc(
        controller.case_id,
        PosthocComparison(
            recommendation_ref=controller.artifacts.ref(
                controller.case_id, "recommendation"
            ),
            realized_method_profile_ref="difference-in-differences-profile-v1",
            comparison={"classification": "defensible-alternative"},
            analyst_principal=adjudicator.principal_id,
        ),
        adjudicator,
    )
    return case_root, run_root, controller


def test_blind_validate_emits_machine_readable_result(tmp_path: Path) -> None:
    case_root = write_case(tmp_path / "case")

    result = CLI.invoke(app, ["benchmark", "blind-validate", str(case_root), "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "case_id": "pilot-001",
        "leakage_verdict": "pass",
        "tier": 1,
        "valid": True,
    }


def test_blind_validate_maps_descriptor_failure_to_stable_error(
    tmp_path: Path,
) -> None:
    result = CLI.invoke(
        app, ["benchmark", "blind-validate", str(tmp_path / "missing"), "--json"]
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "BLIND_CASE_INVALID"


def test_blind_status_reports_current_partial_graph_without_running_workers(
    tmp_path: Path,
) -> None:
    case_root = write_case(tmp_path / "case")
    run_root = tmp_path / "run"
    controller = BlindEvaluationController.from_case(case_root, run_root)
    enroll_controller(controller)
    controller.replay_calibration()

    result = CLI.invoke(app, ["benchmark", "blind-status", str(run_root), "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "cases": [
            {
                "adjudication_completed": False,
                "adjudication_required": False,
                "case_id": "pilot-001",
                "completed_nodes": [
                    "curate-source",
                    "mask-brief",
                    "validate-leakage",
                ],
                "current_lineage": False,
                "gate_failures": [
                    "method recommendation is required",
                    "citation integrity report is required",
                    "expert score 1 is required",
                    "expert score 2 is required",
                    "post-hoc comparison is required",
                ],
                "stale_nodes": [],
                "third_score_locked": False,
                "unresolved": True,
            }
        ]
    }


def test_blind_status_reports_missing_expert_review_gates(tmp_path: Path) -> None:
    case_root = write_case(tmp_path / "case")
    run_root = tmp_path / "run"
    controller = BlindEvaluationController.from_case(case_root, run_root)
    enroll_controller(controller)
    controller.replay_calibration()
    controller.accept_recommendation(valid_recommendation(controller))
    controller.issue_expert_orders()

    result = CLI.invoke(app, ["benchmark", "blind-status", str(run_root), "--json"])

    case = json.loads(result.stdout)["cases"][0]
    assert result.exit_code == 0
    assert case["unresolved"] is True
    assert case["gate_failures"] == [
        "expert score 1 is required",
        "expert score 2 is required",
        "post-hoc comparison is required",
    ]


def test_blind_status_reports_triggered_adjudication_gate(tmp_path: Path) -> None:
    case_root = write_case(tmp_path / "case")
    run_root = tmp_path / "run"
    controller = BlindEvaluationController.from_case(case_root, run_root)
    enroll_controller(controller)
    controller.replay_calibration()
    controller.accept_recommendation(valid_recommendation(controller))
    controller.issue_expert_orders()
    controller.accept_expert_score(1, _score_for(controller, 1))
    controller.accept_expert_score(2, _score_for(controller, 2, conflict=True))

    result = CLI.invoke(app, ["benchmark", "blind-status", str(run_root), "--json"])

    case = json.loads(result.stdout)["cases"][0]
    assert result.exit_code == 0
    assert case["adjudication_required"] is True
    assert case["unresolved"] is True
    assert "blind third score is required" in case["gate_failures"]


def test_blind_status_marks_missing_dag_node_unresolved(tmp_path: Path) -> None:
    _case_root, run_root, controller = _reviewed_case(tmp_path)
    leakage = run_root / controller.artifacts.paths(controller.case_id).leakage_report
    leakage.unlink()

    result = CLI.invoke(app, ["benchmark", "blind-status", str(run_root), "--json"])

    assert result.exit_code == 0, result.output
    case = json.loads(result.stdout)["cases"][0]
    assert case["current_lineage"] is False
    assert case["unresolved"] is True
    assert "passing leakage report is required" in case["gate_failures"]


def test_blind_evaluate_emits_exact_refs_reasons_and_release_gates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    case_root, run_root, controller = _reviewed_case(tmp_path)
    called = False
    original = ReleaseEvaluator.evaluate

    def tracked(self, cases, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal called
        called = True
        return original(self, cases, **kwargs)

    monkeypatch.setattr(ReleaseEvaluator, "evaluate", tracked)

    report = evaluate_blind_catalog(case_root, run_root)
    result = CLI.invoke(app, ["benchmark", "blind-evaluate", str(run_root), "--json"])

    assert report.total_cases == 1
    assert report.calibration_ready is True
    assert report.released is False
    assert called is True
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["total_cases"] == 1
    assert body["calibration_ready"] is True
    assert body["released"] is False
    assert body["unresolved_cases"] == []
    assert "requires at least 16 held-out cases" in body["gate_failures"]
    assert (
        "Pilot8 calibration cases are never release eligible" in body["gate_failures"]
    )
    case = body["cases"][0]
    assert case["case_id"] == "pilot-001"
    assert case["recommendation_ref"] == controller.artifacts.ref(
        controller.case_id, "recommendation"
    ).model_dump(mode="json")
    assert [item["rationale"] for item in case["expert_scores"][0]["scores"]] == [
        "Supported."
    ] * 5
    assert case["unresolved"] is False


def test_blind_evaluate_requires_both_authenticated_expert_reviews(
    tmp_path: Path,
) -> None:
    case_root = write_case(tmp_path / "case")
    run_root = tmp_path / "run"
    controller = BlindEvaluationController.from_case(case_root, run_root)
    enroll_controller(controller)
    controller.replay_calibration()
    controller.accept_recommendation(valid_recommendation(controller))
    controller.issue_expert_orders()

    result = CLI.invoke(app, ["benchmark", "blind-evaluate", str(run_root), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "BLIND_REVIEW_REQUIRED"


def test_blind_evaluate_rejects_missing_citation_report(tmp_path: Path) -> None:
    _case_root, run_root, controller = _reviewed_case(tmp_path)
    citation = run_root / controller.artifacts.paths(controller.case_id).citation_report
    citation.unlink()

    result = CLI.invoke(app, ["benchmark", "blind-evaluate", str(run_root), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == ("CITATION_INTEGRITY_FAILED")


def test_blind_evaluate_rejects_incomplete_current_lineage(tmp_path: Path) -> None:
    _case_root, run_root, controller = _reviewed_case(tmp_path)
    posthoc = (
        run_root / controller.artifacts.paths(controller.case_id).posthoc_comparison
    )
    posthoc.unlink()

    result = CLI.invoke(app, ["benchmark", "blind-evaluate", str(run_root), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "BLIND_LINEAGE_INVALID"


def test_case_id_prefix_cannot_promote_pilot_enrollment_to_held_out(
    tmp_path: Path,
) -> None:
    _case_root, run_root, _controller = _reviewed_case(tmp_path, case_id="heldout-001")

    result = CLI.invoke(app, ["benchmark", "blind-evaluate", str(run_root), "--json"])

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["released"] is False
    assert body["cases"][0]["cohort"] == "pilot"


def test_blind_evaluate_human_output_is_compact(tmp_path: Path) -> None:
    _case_root, run_root, _controller = _reviewed_case(tmp_path)

    result = CLI.invoke(app, ["benchmark", "blind-evaluate", str(run_root)])

    assert result.exit_code == 0, result.output
    assert "Blind benchmark release readiness" in result.stdout
    assert "pilot-001" in result.stdout
    assert "BLOCKED" in result.stdout
