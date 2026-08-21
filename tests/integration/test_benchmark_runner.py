"""End-to-end benchmark replay orchestration tests."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from envresearch.benchmarks.registry import BenchmarkRegistry
from envresearch.benchmarks.report import write_run_report
from envresearch.benchmarks.runner import BenchmarkRunner
from envresearch.models.enums import FindingSeverity, WorkflowStatus
from envresearch.models.run import RunReport


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(
    manifest_root: Path,
    case_root: Path,
    *,
    commands: list[dict[str, object]],
    outputs: list[dict[str, object]],
    source_hash: str | None = None,
) -> Path:
    raw = case_root / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    source = raw / "source.txt"
    if not source.exists():
        source.write_text("source-data", encoding="utf-8")
    payload = {
        "id": "runner-case",
        "title": "Runner case",
        "method_family": "integration",
        "topic": "orchestration",
        "public": False,
        "source_url": "https://example.org/source",
        "source_version": "v1",
        "source_archive": "raw/source.txt",
        "source_sha256": source_hash or _sha256(source),
        "commands": commands,
        "expected_outputs": outputs,
    }
    manifest_root.mkdir(parents=True, exist_ok=True)
    path = manifest_root / "benchmark.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    return path


def _python(script: str, *, timeout: int = 5) -> dict[str, object]:
    return {"argv": ["python", "-c", script], "timeout_seconds": timeout}


def _exact(path: str = "result.txt", expected: str = "expected.txt") -> dict[str, object]:
    return {"path": path, "comparator": "exact", "expected_path": expected}


def test_runner_matches_manifest_root_baseline_and_preserves_case_raw(
    tmp_path: Path,
) -> None:
    case_root = tmp_path / "case"
    manifest_root = tmp_path / "catalog" / "case"
    manifest_root.mkdir(parents=True)
    (manifest_root / "expected.txt").write_text("source-data", encoding="utf-8")
    manifest_path = _write_manifest(
        manifest_root,
        case_root,
        commands=[
            _python(
                "from pathlib import Path; "
                "Path('result.txt').write_text(Path('raw/source.txt').read_text())"
            )
        ],
        outputs=[_exact()],
    )
    before = _sha256(case_root / "raw" / "source.txt")

    report = BenchmarkRunner.default().run_manifest(
        manifest_path, case_root, tmp_path / "run"
    )

    assert report.status is WorkflowStatus.PASSED
    assert report.output_comparisons[0]["status"] == "matched"
    assert _sha256(case_root / "raw" / "source.txt") == before
    assert (tmp_path / "run" / "raw" / "source.txt").read_text() == "source-data"
    assert (tmp_path / "run" / "run-manifest.yaml").is_file()


def test_runner_reports_output_mismatch_as_failed(tmp_path: Path) -> None:
    case_root = tmp_path / "case"
    (tmp_path / "manifest").mkdir()
    (tmp_path / "manifest" / "expected.txt").write_text("expected")
    path = _write_manifest(
        tmp_path / "manifest",
        case_root,
        commands=[_python("from pathlib import Path; Path('result.txt').write_text('actual')")],
        outputs=[_exact()],
    )

    report = BenchmarkRunner.default().run_manifest(path, case_root, tmp_path / "run")

    assert report.status is WorkflowStatus.FAILED
    assert [finding.code for finding in report.findings] == ["OUTPUT_MISMATCH"]


@pytest.mark.parametrize(
    ("script", "timeout", "code"),
    [
        ("raise SystemExit(7)", 5, "COMMAND_FAILED"),
        ("import time; time.sleep(2)", 1, "COMMAND_TIMEOUT"),
    ],
)
def test_runner_maps_command_failures(
    tmp_path: Path, script: str, timeout: int, code: str
) -> None:
    case_root = tmp_path / "case"
    path = _write_manifest(
        tmp_path / "manifest",
        case_root,
        commands=[_python(script, timeout=timeout)],
        outputs=[],
    )

    report = BenchmarkRunner.default().run_manifest(path, case_root, tmp_path / "run")

    assert report.status is WorkflowStatus.FAILED
    assert [finding.code for finding in report.findings] == [code]


def test_runner_short_circuits_commands_after_first_failure(tmp_path: Path) -> None:
    case_root = tmp_path / "case"
    path = _write_manifest(
        tmp_path / "manifest",
        case_root,
        commands=[
            _python("from pathlib import Path; Path('order.txt').write_text('one')"),
            _python("raise SystemExit(3)"),
            _python("from pathlib import Path; Path('order.txt').write_text('three')"),
        ],
        outputs=[],
    )

    report = BenchmarkRunner.default().run_manifest(path, case_root, tmp_path / "run")

    assert report.status is WorkflowStatus.FAILED
    assert report.completed_tasks == ["command-0001"]
    assert (tmp_path / "run" / "order.txt").read_text() == "one"


@pytest.mark.parametrize(
    ("actual_setup", "expected_setup", "expected_code"),
    [
        (None, "ok", "OUTPUT_MISSING"),
        ("ok", None, "OUTPUT_UNEXPECTED"),
        ("{broken", "{}", "COMPARATOR_ERROR"),
    ],
)
def test_runner_maps_output_failures(
    tmp_path: Path,
    actual_setup: str | None,
    expected_setup: str | None,
    expected_code: str,
) -> None:
    manifest_root = tmp_path / "manifest"
    manifest_root.mkdir()
    if expected_setup is not None:
        (manifest_root / "expected.json").write_text(expected_setup)
    script = "pass"
    if actual_setup is not None:
        script = f"from pathlib import Path; Path('result.json').write_text({actual_setup!r})"
    path = _write_manifest(
        manifest_root,
        tmp_path / "case",
        commands=[_python(script)],
        outputs=[
            {
                "path": "result.json",
                "comparator": "json_numeric",
                "expected_path": "expected.json",
            }
        ],
    )

    report = BenchmarkRunner.default().run_manifest(
        path, tmp_path / "case", tmp_path / "run"
    )

    assert report.status is WorkflowStatus.FAILED
    assert [finding.code for finding in report.findings] == [expected_code]


def test_source_hash_mismatch_fails_before_copy_or_command(tmp_path: Path) -> None:
    case_root = tmp_path / "case"
    path = _write_manifest(
        tmp_path / "manifest",
        case_root,
        commands=[_python("from pathlib import Path; Path('executed').touch()")],
        outputs=[],
        source_hash="0" * 64,
    )
    raw_hash = _sha256(case_root / "raw" / "source.txt")

    report = BenchmarkRunner.default().run_manifest(path, case_root, tmp_path / "run")

    assert report.status is WorkflowStatus.FAILED
    assert [finding.code for finding in report.findings] == ["SOURCE_HASH_MISMATCH"]
    assert not (tmp_path / "run" / "executed").exists()
    assert not (tmp_path / "run" / "raw").exists()
    assert _sha256(case_root / "raw" / "source.txt") == raw_hash


def test_run_root_cannot_mutate_the_case_raw_tree(tmp_path: Path) -> None:
    case_root = tmp_path / "case"
    path = _write_manifest(
        tmp_path / "manifest",
        case_root,
        commands=[_python("pass")],
        outputs=[],
    )
    unsafe_run_root = case_root / "raw" / "derived-run"

    with pytest.raises(ValueError, match="immutable raw"):
        BenchmarkRunner.default().run_manifest(path, case_root, unsafe_run_root)

    assert not unsafe_run_root.exists()


def test_warning_only_run_passes_and_report_times_are_canonical_utc(
    tmp_path: Path,
) -> None:
    case_root = tmp_path / "case"
    path = _write_manifest(
        tmp_path / "manifest",
        case_root,
        commands=[_python("pass")],
        outputs=[],
    )

    manifest = BenchmarkRegistry.discover(path.parent)["runner-case"]
    report = BenchmarkRunner.default().run(manifest, case_root, tmp_path / "run")
    persisted = json.loads((tmp_path / "run" / "run-report.json").read_text())

    assert report.status is WorkflowStatus.PASSED
    assert [finding.severity for finding in report.findings] == [FindingSeverity.WARNING]
    assert report.started_at.utcoffset() == UTC.utcoffset(report.started_at)
    assert report.finished_at is not None
    assert report.finished_at.utcoffset() == UTC.utcoffset(report.finished_at)
    assert persisted["started_at"].endswith("Z")
    assert persisted["finished_at"].endswith("Z")


def test_invalid_manifest_is_rejected_before_run_initialization(tmp_path: Path) -> None:
    case_root = tmp_path / "case"
    path = _write_manifest(
        tmp_path / "manifest",
        case_root,
        commands=[_python("pass")],
        outputs=[],
    )
    payload = yaml.safe_load(path.read_text())
    del payload["title"]
    path.write_text(yaml.safe_dump(payload))

    with pytest.raises(ValueError, match="invalid benchmark manifest"):
        BenchmarkRunner.default().run_manifest(path, case_root, tmp_path / "run")

    assert not (tmp_path / "run").exists()


def test_findings_keep_manifest_order_and_deterministic_ids(tmp_path: Path) -> None:
    manifest_root = tmp_path / "manifest"
    manifest_root.mkdir()
    (manifest_root / "expected-a.txt").write_text("a")
    (manifest_root / "expected-b.txt").write_text("b")
    path = _write_manifest(
        manifest_root,
        tmp_path / "case",
        commands=[_python("pass")],
        outputs=[_exact("missing-a.txt", "expected-a.txt"), _exact("missing-b.txt", "expected-b.txt")],
    )

    first = BenchmarkRunner.default().run_manifest(path, tmp_path / "case", tmp_path / "one")
    second = BenchmarkRunner.default().run_manifest(path, tmp_path / "case", tmp_path / "two")

    assert [finding.code for finding in first.findings] == ["OUTPUT_MISSING"] * 2
    assert [finding.id for finding in first.findings] == [finding.id for finding in second.findings]
    assert [finding.id for finding in first.findings] == [
        "runner-case.finding.0001",
        "runner-case.finding.0002",
    ]


def test_report_write_is_atomic_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "run-report.json"
    original = RunReport(
        run_id="run",
        benchmark_id="case",
        status=WorkflowStatus.PASSED,
        started_at=datetime(2026, 8, 4, tzinfo=UTC),
        finished_at=datetime(2026, 8, 4, 1, tzinfo=UTC),
    )
    write_run_report(original, path)
    before = path.read_bytes()
    replacement = original.model_copy(update={"status": WorkflowStatus.FAILED})
    real_replace = os.replace

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated report replacement failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        write_run_report(replacement, path)
    assert path.read_bytes() == before
    assert list(tmp_path.glob(".run-report.json.*")) == []

    monkeypatch.setattr(os, "replace", real_replace)
    write_run_report(replacement, path)
    assert RunReport.model_validate_json(path.read_text()).status is WorkflowStatus.FAILED


def test_repeated_run_root_is_rejected_without_reexecution(tmp_path: Path) -> None:
    case_root = tmp_path / "case"
    path = _write_manifest(
        tmp_path / "manifest",
        case_root,
        commands=[
            _python(
                "from pathlib import Path; p=Path('calls'); "
                "p.write_text(p.read_text() + 'x' if p.exists() else 'x')"
            )
        ],
        outputs=[],
    )
    runner = BenchmarkRunner.default()
    runner.run_manifest(path, case_root, tmp_path / "run")

    with pytest.raises(ValueError, match="run root is not empty"):
        runner.run_manifest(path, case_root, tmp_path / "run")

    assert (tmp_path / "run" / "calls").read_text() == "x"
