"""End-to-end tests for the supported command-line interface."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from typer.testing import CliRunner

from envresearch.cli import app
from envresearch.kernel.events import EventLog
from envresearch.kernel.gates import GateRequest, GateStore
from envresearch.models.enums import WorkflowStatus
from envresearch.models.run import RunReport
from envresearch.storage.artifacts import ArtifactStore

CLI = CliRunner()


def _manifest_payload(*, script: str = "pass") -> dict[str, object]:
    return {
        "id": "cli-case",
        "title": "CLI case",
        "method_family": "fixture",
        "topic": "interface",
        "public": False,
        "source_url": "https://example.org/source",
        "commands": [{"argv": ["python", "-c", script]}],
        "expected_outputs": [],
    }


def _write_manifest(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    return path


def test_benchmark_validate_outputs_machine_readable_json(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "benchmark.yaml", _manifest_payload())

    result = CLI.invoke(app, ["benchmark", "validate", str(manifest), "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "benchmark_id": "cli-case",
        "findings": [],
        "manifest": str(manifest),
        "valid": True,
    }


def test_benchmark_validate_uses_rich_human_output(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "benchmark.yaml", _manifest_payload())

    result = CLI.invoke(app, ["benchmark", "validate", str(manifest)])

    assert result.exit_code == 0
    assert "Manifest validation" in result.stdout
    assert "VALID" in result.stdout
    assert "cli-case" in result.stdout


def test_benchmark_validate_maps_invalid_utf8_to_schema_finding(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "benchmark.yaml"
    manifest.write_bytes(b"\xff\xfe")

    result = CLI.invoke(app, ["benchmark", "validate", str(manifest), "--json"])

    body = json.loads(result.stdout)
    assert result.exit_code == 2
    assert [finding["code"] for finding in body["findings"]] == [
        "SCHEMA_INVALID"
    ]


def test_benchmark_run_maps_invalid_utf8_through_manifest_validation(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "benchmark.yaml"
    manifest.write_bytes(b"\xff\xfe")

    result = CLI.invoke(
        app,
        [
            "benchmark",
            "run",
            str(manifest),
            "--case-root",
            str(tmp_path / "case"),
            "--run-root",
            str(tmp_path / "run"),
            "--json",
        ],
    )

    body = json.loads(result.stdout)
    assert result.exit_code == 2
    assert [finding["code"] for finding in body["findings"]] == [
        "SCHEMA_INVALID"
    ]


def test_invalid_public_manifest_aggregates_stable_finding_codes(
    tmp_path: Path,
) -> None:
    payload = _manifest_payload()
    payload["public"] = True
    payload["commands"] = [
        {"argv": ["python", "-c", "pass"], "timeout_seconds": 0}
    ]
    manifest = _write_manifest(tmp_path / "benchmark.yaml", payload)

    result = CLI.invoke(app, ["benchmark", "validate", str(manifest), "--json"])

    body = json.loads(result.stdout)
    assert result.exit_code == 2
    assert body["valid"] is False
    codes = [finding["code"] for finding in body["findings"]]
    assert codes.count("PUBLIC_METADATA_MISSING") == 4
    assert codes.count("LICENSE_METADATA_MISSING") == 2
    assert "SCHEMA_INVALID" in codes
    assert {finding["evidence"][0] for finding in body["findings"]} >= {
        "field=doi",
        "field=source_version",
        "field=source_archive",
        "field=source_sha256",
        "field=license_name",
        "field=license_url",
    }


def test_benchmark_run_returns_one_for_failed_replay(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path / "manifest" / "benchmark.yaml",
        _manifest_payload(script="raise SystemExit(4)"),
    )

    result = CLI.invoke(
        app,
        [
            "benchmark",
            "run",
            str(manifest),
            "--case-root",
            str(tmp_path / "case"),
            "--run-root",
            str(tmp_path / "run"),
            "--json",
        ],
    )

    body = json.loads(result.stdout)
    assert result.exit_code == 1
    assert body["status"] == "failed"
    assert [finding["code"] for finding in body["findings"]] == ["COMMAND_FAILED"]


def test_run_status_reads_report_as_json(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    report = RunReport(
        run_id="run-1",
        benchmark_id="cli-case",
        status=WorkflowStatus.PASSED,
        started_at=datetime(2026, 8, 4, tzinfo=UTC),
        finished_at=datetime(2026, 8, 4, 0, 1, tzinfo=UTC),
    )
    (run_root / "run-report.json").write_text(
        report.model_dump_json(), encoding="utf-8"
    )

    result = CLI.invoke(app, ["run", "status", str(run_root), "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "passed"


def test_run_status_returns_two_for_unknown_directory(tmp_path: Path) -> None:
    result = CLI.invoke(
        app, ["run", "status", str(tmp_path / "unknown"), "--json"]
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "RUN_NOT_FOUND"


def test_run_status_maps_invalid_utf8_to_structured_error(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "run-report.json").write_bytes(b"\xff\xfe")

    result = CLI.invoke(app, ["run", "status", str(run_root), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "RUN_REPORT_INVALID"


def test_gate_decide_rejects_mutually_exclusive_actions(tmp_path: Path) -> None:
    result = CLI.invoke(
        app,
        [
            "gate",
            "decide",
            str(tmp_path),
            "gate-design",
            "--approve",
            "--reject",
            "--actor",
            "reviewer-b",
            "--rationale",
            "Not both.",
        ],
    )

    assert result.exit_code == 2
    assert "exactly one" in (result.stdout + result.stderr)


def test_gate_decide_persists_an_independent_approval(tmp_path: Path) -> None:
    store = GateStore(
        ArtifactStore(tmp_path), EventLog(tmp_path / "events.jsonl")
    )
    store.request(
        GateRequest(
            id="gate-design", name="Research design", requested_by="agent-a"
        )
    )

    result = CLI.invoke(
        app,
        [
            "gate",
            "decide",
            str(tmp_path),
            "gate-design",
            "--approve",
            "--actor",
            "reviewer-b",
            "--rationale",
            "Design is sound.",
            "--json",
        ],
    )

    body = json.loads(result.stdout)
    assert result.exit_code == 0
    assert body["status"] == "approved"
    assert body["decision"]["decided_by"] == "reviewer-b"
    assert "conditions" not in body["decision"]


def test_gate_decide_reads_conditions_json_object(tmp_path: Path) -> None:
    """The CLI persists conditional data gates from one UTF-8 JSON object."""
    store = GateStore(
        ArtifactStore(tmp_path), EventLog(tmp_path / "events.jsonl")
    )
    store.request(
        GateRequest(id="gate-data", name="Data access", requested_by="agent-a")
    )
    conditions_path = tmp_path / "conditions.json"
    conditions_path.write_text(
        '{"access_conditions":["IRB approval"],"max_rows":100}', encoding="utf-8"
    )

    result = CLI.invoke(
        app,
        [
            "gate",
            "decide",
            str(tmp_path),
            "gate-data",
            "--approve",
            "--actor",
            "reviewer-b",
            "--rationale",
            "Data may be used under the documented conditions.",
            "--conditions-json",
            str(conditions_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["decision"]["conditions"] == {
        "access_conditions": ["IRB approval"],
        "max_rows": 100,
    }


@pytest.mark.parametrize("contents", ["[]", "{bad", '{"score":NaN}'])
def test_gate_decide_rejects_invalid_conditions_json(tmp_path: Path, contents: str) -> None:
    """The CLI rejects conditions files that are not one JSON object."""
    conditions_path = tmp_path / "conditions.json"
    conditions_path.write_text(contents, encoding="utf-8")

    result = CLI.invoke(
        app,
        [
            "gate",
            "decide",
            str(tmp_path),
            "gate-data",
            "--approve",
            "--actor",
            "reviewer-b",
            "--rationale",
            "Looks sound.",
            "--conditions-json",
            str(conditions_path),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "GATE_DECISION_INVALID"


def test_gate_decide_rejects_missing_conditions_json_file(tmp_path: Path) -> None:
    """A missing metadata file maps to the existing structured gate error."""
    result = CLI.invoke(
        app,
        [
            "gate",
            "decide",
            str(tmp_path),
            "gate-data",
            "--approve",
            "--actor",
            "reviewer-b",
            "--rationale",
            "Looks sound.",
            "--conditions-json",
            str(tmp_path / "missing.json"),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "GATE_DECISION_INVALID"


def test_gate_decide_maps_non_object_artifact_to_structured_error(
    tmp_path: Path,
) -> None:
    gate_path = tmp_path / "gates" / "gate-design.json"
    gate_path.parent.mkdir()
    gate_path.write_text("[]", encoding="utf-8")

    result = CLI.invoke(
        app,
        [
            "gate",
            "decide",
            str(tmp_path),
            "gate-design",
            "--approve",
            "--actor",
            "reviewer-b",
            "--rationale",
            "Looks sound.",
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "GATE_DECISION_INVALID"


def test_benchmark_list_outputs_discovered_catalog(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    manifest = _manifest_payload()
    manifest["id"] = "listed-case"
    _write_manifest(catalog / "listed-case" / "benchmark.yaml", manifest)

    result = CLI.invoke(
        app, ["benchmark", "list", "--catalog", str(catalog), "--json"]
    )

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert [item["id"] for item in body["benchmarks"]] == ["listed-case"]
