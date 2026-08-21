"""Durability and raw-input boundary tests for benchmark orchestration."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from envresearch.benchmarks import preparation
from envresearch.benchmarks import report as report_module
from envresearch.benchmarks.runner import BenchmarkRunner
from envresearch.kernel.engine import RunEngine
from envresearch.models.enums import WorkflowStatus
from envresearch.models.run import RunReport


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(
    root: Path,
    case_root: Path,
    *,
    source: Path | None = None,
    expected: str | None = None,
    script: str = "from pathlib import Path; Path('executed').touch()",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    archive = source or case_root / "raw" / "source.txt"
    if source is None:
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text("source", encoding="utf-8")
    if expected is not None:
        (root / "expected.txt").write_text(expected, encoding="utf-8")
    payload = {
        "id": "durable-case",
        "title": "Durable case",
        "method_family": "integration",
        "topic": "finalization",
        "public": False,
        "source_url": "https://example.org/source",
        "source_version": "v1",
        "source_archive": "raw/source.txt",
        "source_sha256": _digest(archive),
        "commands": [{"argv": ["python", "-c", script], "timeout_seconds": 5}],
        "expected_outputs": (
            [
                {
                    "path": "result.txt",
                    "comparator": "exact",
                    "expected_path": "expected.txt",
                }
            ]
            if expected is not None
            else []
        ),
    }
    path = root / "benchmark.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    return path


def test_failed_final_report_publication_retries_without_rerunning_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_root = tmp_path / "case"
    manifest_path = _manifest(
        tmp_path / "manifest",
        case_root,
        expected="expected",
        script=(
            "from pathlib import Path; "
            "Path('result.txt').write_text('actual'); "
            "p=Path('calls'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"
        ),
    )
    run_root = tmp_path / "run"
    real_write = report_module.write_run_report

    def fail_final_report(report: RunReport, path: Path) -> None:
        raise OSError("simulated canonical report failure")

    monkeypatch.setattr(report_module, "write_run_report", fail_final_report)
    with pytest.raises(OSError, match="canonical report failure"):
        BenchmarkRunner.default().run_manifest(manifest_path, case_root, run_root)

    engine_report = RunReport.model_validate_json(
        (run_root / "run-report.json").read_text(encoding="utf-8")
    )
    journal = json.loads(
        (run_root / "benchmark-finalization.json").read_text(encoding="utf-8")
    )
    assert engine_report.status is WorkflowStatus.FAILED
    assert RunEngine.for_workspace(run_root).events.read_all()[-1].payload[
        "task_id"
    ] == "benchmark-finalize"
    assert journal["state"] == "pending"
    assert (run_root / "calls").read_text() == "x"

    monkeypatch.setattr(report_module, "write_run_report", real_write)
    recovered = BenchmarkRunner.default().run_manifest(
        manifest_path, case_root, run_root
    )

    assert recovered.status is WorkflowStatus.FAILED
    assert [finding.code for finding in recovered.findings] == ["OUTPUT_MISMATCH"]
    assert (run_root / "calls").read_text() == "x"
    assert json.loads(
        (run_root / "benchmark-finalization.json").read_text(encoding="utf-8")
    )["state"] == "published"


def test_raw_root_symlink_becomes_finding_without_command(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    source = external / "source.txt"
    source.write_text("source", encoding="utf-8")
    case_root = tmp_path / "case"
    case_root.mkdir()
    (case_root / "raw").symlink_to(external, target_is_directory=True)
    manifest_path = _manifest(tmp_path / "manifest", case_root, source=source)

    report = BenchmarkRunner.default().run_manifest(
        manifest_path, case_root, tmp_path / "run"
    )

    assert report.status is WorkflowStatus.FAILED
    assert [finding.code for finding in report.findings] == ["RAW_INPUT_INVALID"]
    assert not (tmp_path / "run" / "executed").exists()
    assert (tmp_path / "run" / "run-report.json").is_file()


def test_source_archive_symlink_becomes_finding_without_command(tmp_path: Path) -> None:
    case_root = tmp_path / "case"
    raw_root = case_root / "raw"
    raw_root.mkdir(parents=True)
    real_source = raw_root / "real.txt"
    real_source.write_text("source", encoding="utf-8")
    source = raw_root / "source.txt"
    source.symlink_to(real_source)
    manifest_path = _manifest(tmp_path / "manifest", case_root, source=source)

    report = BenchmarkRunner.default().run_manifest(
        manifest_path, case_root, tmp_path / "run"
    )

    assert report.status is WorkflowStatus.FAILED
    assert [finding.code for finding in report.findings] == [
        "SOURCE_ARCHIVE_INVALID"
    ]
    assert not (tmp_path / "run" / "executed").exists()
    assert (tmp_path / "run" / "run-report.json").is_file()


def test_nested_raw_symlink_becomes_finding_without_command(tmp_path: Path) -> None:
    case_root = tmp_path / "case"
    manifest_path = _manifest(tmp_path / "manifest", case_root)
    (case_root / "raw" / "nested-link").symlink_to(tmp_path)

    report = BenchmarkRunner.default().run_manifest(
        manifest_path, case_root, tmp_path / "run"
    )

    assert report.status is WorkflowStatus.FAILED
    assert [finding.code for finding in report.findings] == ["RAW_INPUT_INVALID"]
    assert not (tmp_path / "run" / "executed").exists()
    assert (tmp_path / "run" / "run-report.json").is_file()


def test_source_hash_read_error_becomes_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_root = tmp_path / "case"
    manifest_path = _manifest(tmp_path / "manifest", case_root)

    def fail_hash(path: Path) -> str:
        raise PermissionError("simulated unreadable source")

    monkeypatch.setattr(preparation, "sha256_file", fail_hash)
    report = BenchmarkRunner.default().run_manifest(
        manifest_path, case_root, tmp_path / "run"
    )

    assert report.status is WorkflowStatus.FAILED
    assert [finding.code for finding in report.findings] == [
        "SOURCE_ARCHIVE_INVALID"
    ]
    assert not (tmp_path / "run" / "executed").exists()
    assert (tmp_path / "run" / "run-report.json").is_file()


def test_raw_copy_error_becomes_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_root = tmp_path / "case"
    manifest_path = _manifest(tmp_path / "manifest", case_root)

    def fail_copy(source: Path, destination: Path) -> None:
        raise OSError("simulated copy failure")

    monkeypatch.setattr(shutil, "copytree", fail_copy)
    report = BenchmarkRunner.default().run_manifest(
        manifest_path, case_root, tmp_path / "run"
    )

    assert report.status is WorkflowStatus.FAILED
    assert [finding.code for finding in report.findings] == ["RAW_INPUT_INVALID"]
    assert not (tmp_path / "run" / "executed").exists()
    assert (tmp_path / "run" / "run-report.json").is_file()
