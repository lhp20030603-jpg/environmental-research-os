"""Filesystem boundary regressions for the Tier-2 operator CLI."""

import json
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from replication_service_fixtures import ServiceCase, admission
from typer.testing import CliRunner

import envresearch.replication.cli as replication_cli
from envresearch.cli import app
from envresearch.models.artifact import ArtifactRef


def test_external_approval_rejects_filesystem_root_before_opening_service(
    service_case: ServiceCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = tmp_path / "intake.yaml"
    proposal.write_text(
        yaml.safe_dump(service_case.proposal.model_dump(mode="json")),
        encoding="utf-8",
    )

    def forbidden_service(run_root: Path) -> object:
        raise AssertionError(f"unsafe service root opened: {run_root}")

    monkeypatch.setattr(replication_cli, "_service_for_root", forbidden_service)
    result = CliRunner().invoke(
        app,
        [
            "replication",
            "approve-external",
            str(proposal),
            "--run-root",
            str(Path("/")),
            "--approver-id",
            "research-owner",
            "--rationale",
            "Reviewed exact package authority.",
            "--approved-locator",
            "https://example.org/tiny-did.tar.gz",
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "REPLICATION_ROOT_INVALID"


def test_run_rejects_well_formed_but_absent_approval_before_opening_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = ArtifactRef(
        artifact_id="approved-tier2-intake",
        artifact_version=1,
        content_hash="a" * 64,
    )
    authority = tmp_path / "absent-approval.json"
    authority.write_text(reference.model_dump_json(), encoding="utf-8")

    def forbidden_service(run_root: Path) -> object:
        raise AssertionError(f"absent authority opened service: {run_root}")

    monkeypatch.setattr(replication_cli, "_service_for_root", forbidden_service)
    result = CliRunner().invoke(
        app,
        [
            "replication",
            "run",
            "--run-root",
            str(tmp_path),
            "--approved-ref",
            str(authority),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == ("EXTERNAL_ADMISSION_REQUIRED")
    assert not (tmp_path / "artifacts").exists()


def test_run_maps_unwritable_attempt_store_to_nondurable_application_error(
    service_case: ServiceCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal_ref = service_case.service.intake.record_proposal(service_case.proposal)
    approved_ref = service_case.service.approve_external_admission(
        proposal_ref, admission()
    )
    authority = tmp_path / "approved-ref.json"
    authority.write_text(approved_ref.model_dump_json(), encoding="utf-8")
    attempts = tmp_path / "artifacts/replication/attempts"
    attempts.mkdir(parents=True)
    attempts.chmod(0o500)
    monkeypatch.setattr(
        replication_cli, "_service_for_root", lambda run_root: service_case.service
    )

    try:
        result = CliRunner().invoke(
            app,
            [
                "replication",
                "run",
                "--run-root",
                str(tmp_path),
                "--approved-ref",
                str(authority),
                "--json",
            ],
        )
    finally:
        attempts.chmod(0o700)

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "REPLICATION_RUN_INVALID"
    assert tuple(attempts.rglob("*")) == ()
