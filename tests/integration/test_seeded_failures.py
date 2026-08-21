"""End-to-end checks for deterministic, seeded failure contracts."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from envresearch.benchmarks.runner import BenchmarkRunner
from envresearch.cli import app
from envresearch.models.enums import FindingSeverity


def finding_codes(result_stdout: str) -> set[str]:
    """Return the stable finding codes emitted by a JSON CLI response."""
    payload = json.loads(result_stdout)
    return {str(item["code"]) for item in payload["findings"]}


def test_seeded_schema_failure_is_detected(
    invalid_schema_manifest_path: Path,
) -> None:
    result = CliRunner().invoke(
        app,
        ["benchmark", "validate", str(invalid_schema_manifest_path), "--json"],
    )

    assert result.exit_code == 2
    assert "SCHEMA_INVALID" in finding_codes(result.stdout)


def test_seeded_command_failure_is_detected(
    command_failure_benchmark: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    manifest_path, case_root = command_failure_benchmark
    report = BenchmarkRunner.default().run_manifest(
        manifest_path,
        case_root,
        tmp_path / "command-failure-run",
    )

    assert "COMMAND_FAILED" in {finding.code for finding in report.findings}


def test_seeded_scientific_metadata_failure_is_detected(
    missing_doi_manifest_path: Path,
) -> None:
    result = CliRunner().invoke(
        app,
        ["benchmark", "validate", str(missing_doi_manifest_path), "--json"],
    )

    assert result.exit_code == 2
    assert "PUBLIC_METADATA_MISSING" in finding_codes(result.stdout)


def test_seeded_integrity_failure_is_critical(
    hash_mismatch_benchmark: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    manifest_path, case_root = hash_mismatch_benchmark
    report = BenchmarkRunner.default().run_manifest(
        manifest_path,
        case_root,
        tmp_path / "integrity-failure-run",
    )
    findings = [
        finding
        for finding in report.findings
        if finding.code == "SOURCE_HASH_MISMATCH"
    ]

    assert findings
    assert findings[0].severity is FindingSeverity.CRITICAL
