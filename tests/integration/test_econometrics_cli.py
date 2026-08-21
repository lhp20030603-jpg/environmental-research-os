"""Method-neutral CLI acceptance tests for trusted local econometrics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from envresearch.cli import app


@pytest.fixture
def spec_path(local_service, tmp_path: Path) -> Path:
    """Write one strict local-analysis spec for CLI parsing."""
    path = tmp_path / "analysis.yaml"
    path.write_text(
        yaml.safe_dump(local_service.spec.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    return path


def test_validate_is_read_only_json(local_service, spec_path, monkeypatch) -> None:
    """Validation emits shape metadata without execution or store writes."""
    monkeypatch.setattr(
        "envresearch.econometrics.cli._service_for",
        lambda *args, **kwargs: local_service.service,
    )

    result = CliRunner().invoke(
        app, ["econometrics", "validate", str(spec_path), "--json"]
    )
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert payload["valid"] is True
    assert payload["row_count"] == 4
    assert local_service.backend.calls == 0
    assert not local_service.store.root.exists()


def test_run_and_exact_reference_status(
    local_service, spec_path, tmp_path, monkeypatch
) -> None:
    """Run emits one reusable reference consumed verbatim by read-only status."""
    monkeypatch.setattr(
        "envresearch.econometrics.cli._service_for",
        lambda *args, **kwargs: local_service.service,
    )
    runner = CliRunner()
    run = runner.invoke(
        app,
        [
            "econometrics",
            "run",
            str(spec_path),
            "--run-root",
            str(tmp_path / "run"),
            "--r-executable",
            "/usr/bin/false",
            "--r-sha256",
            "0" * 64,
            "--json",
        ],
    )
    payload = json.loads(run.stdout)
    reference_path = tmp_path / "reference.json"
    reference_path.write_text(json.dumps(payload["reference"]), encoding="utf-8")
    status = runner.invoke(
        app,
        [
            "econometrics",
            "status",
            str(reference_path),
            "--run-root",
            str(tmp_path / "run"),
            "--json",
        ],
    )

    assert run.exit_code == 0
    assert status.exit_code == 0
    assert json.loads(status.stdout)["report"]["status"] == "passed"
    assert local_service.backend.calls == 1


def test_malformed_spec_exits_two(tmp_path: Path) -> None:
    """Malformed YAML is an authority error, not an execution exception."""
    path = tmp_path / "bad.yaml"
    path.write_text("[not: a: mapping", encoding="utf-8")

    result = CliRunner().invoke(app, ["econometrics", "validate", str(path), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "LOCAL_SPEC_INVALID"


def test_typed_execution_exception_exits_one(
    local_service, spec_path, tmp_path, monkeypatch
) -> None:
    """A durable non-green analysis is machine-readable and exits one."""
    local_service.backend.failure_code = "R_PACKAGE_UNAVAILABLE"
    monkeypatch.setattr(
        "envresearch.econometrics.cli._service_for",
        lambda *args, **kwargs: local_service.service,
    )

    result = CliRunner().invoke(
        app,
        [
            "econometrics",
            "run",
            str(spec_path),
            "--run-root",
            str(tmp_path / "run"),
            "--r-executable",
            "/usr/bin/false",
            "--r-sha256",
            "0" * 64,
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["report"]["code"] == "R_PACKAGE_UNAVAILABLE"


def test_status_rejects_malformed_reference(tmp_path: Path) -> None:
    """Status never scans for a latest report when exact authority is invalid."""
    path = tmp_path / "reference.json"
    path.write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "econometrics",
            "status",
            str(path),
            "--run-root",
            str(tmp_path / "run"),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "ANALYSIS_REFERENCE_INVALID"


def test_duplicate_yaml_authority_is_rejected(spec_path: Path) -> None:
    """Reviewed YAML cannot silently replace an earlier authority field."""
    text = spec_path.read_text(encoding="utf-8")
    spec_path.write_text(text + "comparison_group: not-yet-treated\n", encoding="utf-8")

    result = CliRunner().invoke(
        app, ["econometrics", "validate", str(spec_path), "--json"]
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "LOCAL_SPEC_INVALID"


def test_malformed_runtime_authority_fails_before_snapshot(
    local_service, spec_path, tmp_path
) -> None:
    """Invalid runtime authority is exit two and creates no analysis store."""
    run_root = tmp_path / "run"
    result = CliRunner().invoke(
        app,
        [
            "econometrics",
            "run",
            str(spec_path),
            "--run-root",
            str(run_root),
            "--r-executable",
            "/usr/bin/false",
            "--r-sha256",
            "not-a-sha",
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "R_RUNTIME_AUTHORITY_INVALID"
    assert not run_root.exists()


def test_status_missing_root_is_read_only(tmp_path: Path) -> None:
    """A missing status root remains absent after the read-only command."""
    reference = tmp_path / "reference.json"
    reference.write_text(
        json.dumps(
            {
                "analysis_id": "local-did-" + "a" * 24,
                "generation": 1,
                "relative_path": (
                    "analyses/local-did-"
                    + "a" * 24
                    + "/history/generation-1-"
                    + "b" * 64
                    + ".json"
                ),
                "sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    run_root = tmp_path / "missing"

    result = CliRunner().invoke(
        app,
        [
            "econometrics",
            "status",
            str(reference),
            "--run-root",
            str(run_root),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert not run_root.exists()
