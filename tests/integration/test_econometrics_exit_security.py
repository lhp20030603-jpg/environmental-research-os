"""Evaluator separation, comparison, and integrity rejection."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_econometrics_exit_runner import GREEN, _analysis, _manifest
from typer.testing import CliRunner

from envresearch.cli import app
from envresearch.econometrics.exit_evaluator import V03ExitEvaluator, _matches
from envresearch.econometrics.exit_models import (
    CaseExpectation,
    ExitAnalysisBinding,
    ExitCaseReceipt,
    ExitExpectationCatalog,
    ExpectedComparison,
    V03ExitRun,
)
from envresearch.econometrics.exit_registry import ExitRegistry, validate_separate_roots
from envresearch.econometrics.report import OutputEvidence
from envresearch.econometrics.service import EvidenceTampered
from envresearch.models.artifact import ArtifactRef


class _Files:
    def __init__(self, payloads: dict[Path, bytes]) -> None:
        self.payloads = payloads

    def read(self, path: Path) -> bytes:
        return self.payloads[path]


class _Service:
    def __init__(self, reports: dict[str, object], payloads: dict[Path, bytes]) -> None:
        self.reports = reports
        self.files = _Files(payloads)

    def status(self, reference):
        value = self.reports[reference.analysis_id]
        if isinstance(value, Exception):
            raise value
        return value


def _matrix(runner: ExitRegistry, evaluator: ExitRegistry):
    outputs: dict[Path, bytes] = {}
    reports: dict[str, object] = {}
    receipts = []
    expectations = []
    for index, family in enumerate(GREEN):
        case_id = f"green-{family}"
        reference = _analysis(case_id, index)
        receipts.append(
            ExitCaseReceipt(case_id=case_id, role="green", analysis_ref=reference)
        )
        data = f"metric-{family}".encode()
        path = Path("analyses") / case_id / "evidence" / "metric.bin"
        outputs[path] = data
        evidence = OutputEvidence(
            name="metric.bin",
            relative_path=path,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )
        reports[case_id] = SimpleNamespace(
            status="passed", outputs=(evidence,), snapshot=None
        )
        expectations.append(
            CaseExpectation(
                case_id=case_id,
                role="green",
                comparisons=(
                    ExpectedComparison(
                        comparison_type="exact",
                        output_name="metric.bin",
                        expected=hashlib.sha256(data).hexdigest(),
                    ),
                ),
            )
        )
    for offset, family in enumerate(GREEN[:-1], 8):
        case_id = f"fail-{family}"
        reference = _analysis(case_id, offset)
        receipts.append(
            ExitCaseReceipt(
                case_id=case_id, role="assumption-failure", analysis_ref=reference
            )
        )
        code = f"EXPECTED_{offset}"
        reports[case_id] = SimpleNamespace(
            status="exception", code=code, outputs=(), snapshot=None
        )
        expectations.append(
            CaseExpectation(
                case_id=case_id, role="assumption-failure", expected_code=code
            )
        )
    case_id = "integrity-rct"
    reference = _analysis(case_id, 15)
    receipts.append(
        ExitCaseReceipt(
            case_id=case_id, role="integrity-failure", analysis_ref=reference
        )
    )
    reports[case_id] = EvidenceTampered("mutated")
    expectations.append(
        CaseExpectation(
            case_id=case_id, role="integrity-failure", expected_code="EVIDENCE_TAMPERED"
        )
    )
    data_refs = tuple(
        runner.publish_bytes(f"data-{index:02d}", f"data-{index:02d}".encode())
        for index in range(16)
    )
    catalog = ExitExpectationCatalog(
        schema_version="econometrics.v03-exit-expectations.v1",
        manifest_id="wave1",
        cases=tuple(expectations),
    )
    catalog_ref = evaluator.publish("protected-catalog", catalog)
    manifest_ref = runner.publish("manifest-wave1", _manifest(catalog_ref, data_refs))
    run = V03ExitRun(
        schema_version="econometrics.v03-exit-run.v1",
        manifest_ref=manifest_ref,
        receipts=tuple(receipts),
    )
    manifest = _manifest(catalog_ref, data_refs)
    cases = {item.case_id: item for item in manifest.cases}
    for receipt in receipts:
        binding = ExitAnalysisBinding(
            schema_version="econometrics.v03-exit-analysis-binding.v1",
            case_ref=cases[receipt.case_id].case_ref,
            analysis_ref=receipt.analysis_ref,
        )
        binding_ref = runner.publish(
            f"analysis-ref-{receipt.case_id}", binding, version=1
        )
        runner.set_current(f"analysis-{receipt.case_id}", binding_ref)
    run_ref = runner.publish("run-wave1", run)
    runner.set_current("run-wave1", run_ref)
    return run_ref, catalog_ref, _Service(reports, outputs)


def test_evaluator_passes_exact_blind_matrix_and_publishes_report(
    tmp_path: Path,
) -> None:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    protected = ExitRegistry((tmp_path / "protected").resolve())
    run_ref, catalog_ref, service = _matrix(runner, protected)
    evaluator = V03ExitEvaluator(runner, protected, service)  # type: ignore[arg-type]
    report = evaluator.evaluate(run_ref, catalog_ref)
    assert report.status == "passed" and len(report.outcomes) == 16
    reference = protected.current("report-wave1")
    assert reference is not None
    assert evaluator.status(reference) == report
    runner_bytes = b"".join(path.read_bytes() for path in runner.root.rglob("*.json"))
    assert b"EXPECTED_" not in runner_bytes and b"metric-rct" not in runner_bytes


def test_evaluator_rejects_output_mutation_and_root_aliases(tmp_path: Path) -> None:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    protected = ExitRegistry((tmp_path / "protected").resolve())
    run_ref, catalog_ref, service = _matrix(runner, protected)
    service.files.payloads[next(iter(service.files.payloads))] = b"tampered"
    report = V03ExitEvaluator(runner, protected, service).evaluate(run_ref, catalog_ref)  # type: ignore[arg-type]
    assert report.status == "failed"
    with pytest.raises(ValueError, match="overlap"):
        validate_separate_roots(runner.root, runner.root / "nested")


def test_status_rejects_caller_published_noncurrent_passed_report(
    tmp_path: Path,
) -> None:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    protected = ExitRegistry((tmp_path / "protected").resolve())
    run_ref, catalog_ref, service = _matrix(runner, protected)
    evaluator = V03ExitEvaluator(runner, protected, service)  # type: ignore[arg-type]
    report = evaluator.evaluate(run_ref, catalog_ref)
    bogus_catalog = ArtifactRef(
        artifact_id="bogus-catalog", artifact_version=1, content_hash="e" * 64
    )
    forged_report = report.model_copy(update={"catalog_ref": bogus_catalog})
    forged = protected.publish("caller-report", forged_report, version=2)
    protected.set_current("report-wave1", forged)
    with pytest.raises(ValueError, match="authority"):
        evaluator.status(forged)


def test_evaluator_recovers_after_current_pointer_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    protected = ExitRegistry((tmp_path / "protected").resolve())
    run_ref, catalog_ref, service = _matrix(runner, protected)
    evaluator = V03ExitEvaluator(runner, protected, service)  # type: ignore[arg-type]
    original = protected.set_current
    failures = 0

    def fail_once(subject, reference) -> None:
        nonlocal failures
        if failures == 0:
            failures += 1
            raise OSError("injected final current failure")
        original(subject, reference)

    monkeypatch.setattr(protected, "set_current", fail_once)
    with pytest.raises(OSError, match="injected"):
        evaluator.evaluate(run_ref, catalog_ref)
    report = evaluator.evaluate(run_ref, catalog_ref)
    reference = protected.current("report-wave1")
    assert reference is not None and evaluator.status(reference) == report


def test_exit_status_cli_reads_exact_current_report_without_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    protected = ExitRegistry((tmp_path / "protected").resolve())
    run_ref, catalog_ref, service = _matrix(runner, protected)
    evaluator = V03ExitEvaluator(runner, protected, service)  # type: ignore[arg-type]
    evaluator.evaluate(run_ref, catalog_ref)
    reference = protected.current("report-wave1")
    assert reference is not None
    reference_path = tmp_path / "report-reference.json"
    reference_path.write_text(reference.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(
        "envresearch.econometrics.cli._service_for", lambda *args, **kwargs: service
    )
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    result = CliRunner().invoke(
        app,
        [
            "econometrics",
            "exit-status",
            str(reference_path),
            "--runner-root",
            str(runner.root),
            "--evaluator-root",
            str(protected.root),
            "--analysis-root",
            str(tmp_path / "analysis"),
            "--json",
        ],
    )
    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert result.exit_code == 0
    assert json.loads(result.stdout)["report"]["status"] == "passed"
    assert after == before


def test_exit_run_rejects_malformed_exact_refs_before_creating_roots(
    tmp_path: Path,
) -> None:
    malformed = tmp_path / "bad-ref.json"
    malformed.write_text("{}", encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "econometrics",
            "exit-run",
            str(malformed),
            str(malformed),
            "--runner-root",
            str(tmp_path / "runner"),
            "--evaluator-root",
            str(tmp_path / "evaluator"),
            "--analysis-root",
            str(tmp_path / "analysis"),
            "--r-executable",
            "/usr/bin/false",
            "--r-sha256",
            "0" * 64,
            "--json",
        ],
    )
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "EXIT_REFERENCE_INVALID"
    assert not (tmp_path / "runner").exists()


def test_json_and_csv_comparisons_are_inclusive_and_reject_nan() -> None:
    boundary = ExpectedComparison(
        comparison_type="json",
        output_name="metric.json",
        selector="/estimate",
        expected=10.0,
        atol=0.1,
        rtol=0.01,
    )
    assert _matches(b'{"estimate":10.2}', boundary)
    assert not _matches(b'{"estimate":NaN}', boundary)
    csv_comparison = ExpectedComparison(
        comparison_type="csv",
        output_name="metric.csv",
        selector="row=ATT,column=estimate",
        expected=2.0,
        atol=0.0,
        rtol=0.0,
    )
    assert _matches(b"term,estimate\nATT,2.0\n", csv_comparison)


def test_catalog_replacement_is_rejected_before_evaluation(tmp_path: Path) -> None:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    protected = ExitRegistry((tmp_path / "protected").resolve())
    run_ref, catalog_ref, service = _matrix(runner, protected)
    relative = (
        Path("exit/objects")
        / catalog_ref.artifact_id
        / f"v1-{catalog_ref.content_hash}.json"
    )
    protected.files.write(relative, b"{}")
    with pytest.raises(ValueError):
        V03ExitEvaluator(runner, protected, service).evaluate(run_ref, catalog_ref)  # type: ignore[arg-type]


def test_evaluator_rejects_noncurrent_run_and_serializes_publication(
    tmp_path: Path,
) -> None:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    protected = ExitRegistry((tmp_path / "protected").resolve())
    run_ref, catalog_ref, service = _matrix(runner, protected)
    evaluator = V03ExitEvaluator(runner, protected, service)  # type: ignore[arg-type]
    results: list[tuple[ArtifactRef, object]] = []
    threads = [
        threading.Thread(
            target=lambda: results.append(
                evaluator.evaluate_reference(run_ref, catalog_ref)
            )
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 2 and results[0] == results[1]

    run = runner.load(run_ref, V03ExitRun)
    revised = runner.publish("run-wave1", run, version=2)
    runner.set_current("run-wave1", revised)
    with pytest.raises(ValueError, match="current generation"):
        evaluator.evaluate(run_ref, catalog_ref)
