"""No-follow and protected-control security for blind run reporting."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from blind_signing_helpers import enroll_controller
from test_blind_registry_security import write_case
from test_blind_scoring_lifecycle import _complete_adjudication, _controller
from test_blind_workflow import _score_for
from typer.testing import CliRunner

from envresearch.benchmarks import blind_run, design_files
from envresearch.benchmarks.blind_workflow import BlindEvaluationController
from envresearch.benchmarks.design_files import PinnedFixtureRoot
from envresearch.cli import app
from envresearch.models.benchmark_evaluation import PosthocComparison
from envresearch.models.principal import PrincipalKind

CLI = CliRunner()


def _calibrated_run(tmp_path: Path) -> Path:
    case_root = write_case(tmp_path / "case")
    run_root = tmp_path / "run"
    controller = BlindEvaluationController.from_case(case_root, run_root)
    enroll_controller(controller)
    controller.replay_calibration()
    return run_root


def _reviewed_run(tmp_path: Path, *, conflict: bool) -> Path:
    controller = _controller(tmp_path)
    if conflict:
        _complete_adjudication(controller)
    else:
        controller.accept_expert_score(1, _score_for(controller, 1))
        controller.accept_expert_score(2, _score_for(controller, 2))
    analyst = controller._human(PrincipalKind.ADJUDICATOR, 1)
    controller.artifacts.publish_posthoc(
        controller.case_id,
        PosthocComparison(
            recommendation_ref=controller.artifacts.ref(
                controller.case_id, "recommendation"
            ),
            realized_method_profile_ref="difference-in-differences-profile-v1",
            comparison={"classification": "defensible-alternative"},
            analyst_principal=analyst.principal_id,
        ),
        analyst,
    )
    return controller.run_root


def test_status_does_not_recreate_missing_authenticated_control(tmp_path: Path) -> None:
    run_root = _calibrated_run(tmp_path)
    control = run_root / "control/queues/recommender/pilot-001"
    (control / "orders").rename(control / "orders-away")

    result = CLI.invoke(app, ["benchmark", "blind-status", str(run_root), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "BLIND_LINEAGE_INVALID"
    assert not (control / "orders").exists()


def test_status_does_not_launder_unsafe_control_permissions(tmp_path: Path) -> None:
    run_root = _calibrated_run(tmp_path)
    capability = (
        run_root / "control/queues/recommender/pilot-001/principals/gate.capability"
    )
    capability.chmod(0o644)

    result = CLI.invoke(app, ["benchmark", "blind-status", str(run_root), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "BLIND_LINEAGE_INVALID"


def test_status_does_not_launder_foreign_control_owner(tmp_path: Path) -> None:
    if os.geteuid() != 0:
        pytest.skip("changing a control file to a foreign owner requires root")
    run_root = _calibrated_run(tmp_path)
    capability = (
        run_root / "control/queues/recommender/pilot-001/principals/gate.capability"
    )
    capability.chown(1, -1)

    result = CLI.invoke(app, ["benchmark", "blind-status", str(run_root), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "BLIND_LINEAGE_INVALID"


def test_status_rejects_control_inode_replacement_between_stat_and_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = _calibrated_run(tmp_path)
    original = design_files.read_regular_with_identity_at
    swapped = False

    def replace_then_read(  # type: ignore[no-untyped-def]
        parent_fd, name, **kwargs
    ):
        nonlocal swapped
        if name == "gate.capability" and not swapped:
            swapped = True
            os.rename(
                name,
                f"{name}.away",
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent_fd,
            )
            try:
                os.write(descriptor, b"attacker-capability")
            finally:
                os.close(descriptor)
        return original(parent_fd, name, **kwargs)

    monkeypatch.setattr(
        design_files, "read_regular_with_identity_at", replace_then_read
    )

    result = CLI.invoke(app, ["benchmark", "blind-status", str(run_root), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "BLIND_LINEAGE_INVALID"


@pytest.mark.parametrize(
    ("conflict", "control"),
    (
        (False, "control/queues/expert/pilot-001/1"),
        (True, "control/queues/adjudicator/pilot-001"),
    ),
)
def test_evaluate_rejects_unsafe_human_queue_directory_mode(
    tmp_path: Path, conflict: bool, control: str
) -> None:
    run_root = _reviewed_run(tmp_path, conflict=conflict)
    (run_root / control).chmod(0o755)

    result = CLI.invoke(app, ["benchmark", "blind-evaluate", str(run_root), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "BLIND_LINEAGE_INVALID"


@pytest.mark.parametrize(
    ("conflict", "control_file"),
    (
        (False, "control/queues/expert/pilot-001/1/queue.key"),
        (True, "control/queues/adjudicator/pilot-001/queue.key"),
        (
            False,
            (
                "control/queues/recommender/pilot-001/principals/benchmark/"
                "pilot-001/expert-1.capability"
            ),
        ),
        (
            True,
            (
                "control/queues/recommender/pilot-001/principals/benchmark/"
                "pilot-001/adjudicator-1.capability"
            ),
        ),
    ),
)
def test_evaluate_does_not_launder_human_queue_or_capability_owner(
    tmp_path: Path, conflict: bool, control_file: str
) -> None:
    if os.geteuid() != 0:
        pytest.skip("changing a control file to a foreign owner requires root")
    run_root = _reviewed_run(tmp_path, conflict=conflict)
    (run_root / control_file).chown(1, -1)

    result = CLI.invoke(app, ["benchmark", "blind-evaluate", str(run_root), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "BLIND_LINEAGE_INVALID"


@pytest.mark.parametrize(
    ("conflict", "control_file"),
    (
        (False, "control/queues/expert/pilot-001/1/queue.key"),
        (True, "control/queues/adjudicator/pilot-001/queue.key"),
    ),
)
def test_evaluate_rejects_human_queue_replacement_between_stat_and_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    conflict: bool,
    control_file: str,
) -> None:
    run_root = _reviewed_run(tmp_path, conflict=conflict)
    target = run_root / control_file
    target_parent = target.parent.stat()
    original = design_files.read_regular_with_identity_at
    swapped = False

    def replace_then_read(  # type: ignore[no-untyped-def]
        parent_fd, name, **kwargs
    ):
        nonlocal swapped
        if (
            name == target.name
            and not swapped
            and os.path.samestat(os.fstat(parent_fd), target_parent)
        ):
            swapped = True
            os.rename(
                name,
                f"{name}.away",
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent_fd,
            )
            try:
                os.write(descriptor, b"attacker-key")
            finally:
                os.close(descriptor)
        return original(parent_fd, name, **kwargs)

    monkeypatch.setattr(
        design_files, "read_regular_with_identity_at", replace_then_read
    )

    result = CLI.invoke(app, ["benchmark", "blind-evaluate", str(run_root), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "BLIND_LINEAGE_INVALID"


def test_status_rejects_direct_run_root_alias(tmp_path: Path) -> None:
    run_root = _calibrated_run(tmp_path)
    alias = tmp_path / "run-alias"
    alias.symlink_to(run_root, target_is_directory=True)

    result = CLI.invoke(app, ["benchmark", "blind-status", str(alias), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "BLIND_LINEAGE_INVALID"


def test_status_rejects_nested_run_alias(tmp_path: Path) -> None:
    case_root = write_case(tmp_path / "case")
    catalog_run = tmp_path / "runs"
    run_root = catalog_run / "pilot-001"
    controller = BlindEvaluationController.from_case(case_root, run_root)
    enroll_controller(controller)
    controller.replay_calibration()
    (catalog_run / "alias").symlink_to(run_root, target_is_directory=True)

    result = CLI.invoke(app, ["benchmark", "blind-status", str(catalog_run), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "BLIND_LINEAGE_INVALID"


def test_status_reads_pinned_run_when_lexical_root_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = _calibrated_run(tmp_path)
    original = PinnedFixtureRoot.snapshot_to
    swapped = False

    def replace_then_snapshot(
        self: PinnedFixtureRoot, destination: Path, **kwargs: bool
    ) -> None:
        nonlocal swapped
        if self.root == run_root and not swapped:
            swapped = True
            backup = tmp_path / "original-run"
            self.root.rename(backup)
            shutil.copytree(backup, self.root)
            artifact = (
                self.root / "artifacts/blind-benchmarks/pilot-001/leakage-report.yaml"
            )
            artifact.write_text("forged", encoding="utf-8")
        original(self, destination, **kwargs)

    monkeypatch.setattr(PinnedFixtureRoot, "snapshot_to", replace_then_snapshot)

    result = CLI.invoke(app, ["benchmark", "blind-status", str(run_root), "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["cases"][0]["case_id"] == "pilot-001"


def test_status_rejects_artifact_replacement_during_queue_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = _calibrated_run(tmp_path)
    original = blind_run.FilesystemWorkerQueue.__init__

    def replace_artifacts(self, root, **kwargs):  # type: ignore[no-untyped-def]
        snapshot = Path(root).parents[2]
        artifact = snapshot / "artifacts/blind-benchmarks/pilot-001"
        backup = snapshot / "artifacts/blind-benchmarks/pilot-001-away"
        artifact.rename(backup)
        shutil.copytree(backup, artifact)
        original(self, root, **kwargs)

    monkeypatch.setattr(blind_run.FilesystemWorkerQueue, "__init__", replace_artifacts)

    result = CLI.invoke(app, ["benchmark", "blind-status", str(run_root), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "BLIND_LINEAGE_INVALID"
