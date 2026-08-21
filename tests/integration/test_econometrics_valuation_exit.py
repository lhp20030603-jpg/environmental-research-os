"""Offline execution coverage for the compact Valuation Core exit."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from envresearch.econometrics.exit_evaluator import ValuationExitEvaluator
from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.report import LocalAnalysisReference, OutputEvidence
from envresearch.econometrics.service import EvidenceTampered
from envresearch.econometrics.valuation_exit_corpus import freeze_valuation_exit_corpus
from envresearch.econometrics.valuation_exit_models import ValuationExitManifest
from envresearch.econometrics.valuation_exit_runner import (
    ValuationExitRunner,
    ValuationRegistryAnalysisExecutor,
)


class _Executor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, case):
        self.calls.append(case.case_id)
        return LocalAnalysisReference(
            analysis_id=case.case_id,
            generation=1,
            relative_path=Path("analyses")
            / case.case_id
            / "history"
            / ("generation-1-" + "0" * 64 + ".json"),
            sha256="0" * 64,
        )

    def verify(self, case, reference) -> None:
        assert reference.analysis_id == case.case_id


def test_valuation_runner_resumes_the_exact_nine_case_run(tmp_path: Path) -> None:
    """Changing resume to re-execute completed receipts must fail this test."""
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    root = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    refs = freeze_valuation_exit_corpus(root.resolve(), runner, evaluator)
    executor = _Executor()
    exit_runner = ValuationExitRunner(runner, executor)

    first = exit_runner.run(refs.manifest_ref)
    second = exit_runner.run(refs.manifest_ref)

    assert first == second
    assert len(executor.calls) == 9


class _Files:
    def __init__(self) -> None:
        self.bytes: dict[Path, bytes] = {}

    def read(self, path: Path) -> bytes:
        return self.bytes[path]

    def write(self, path: Path, data: bytes) -> None:
        self.bytes[path] = data


class _OfflineService:
    def __init__(self, snapshot_mode: str = "exact") -> None:
        self.files = _Files()
        self.index = 0
        self.snapshot_mode = snapshot_mode
        self.codes = (
            "CV_MONOTONICITY_FAILED",
            "DCE_CHOICE_SET_INVALID",
            "HEDONIC_SENSITIVITY_EXCEEDED",
            "TRAVEL_COST_DISPERSION_EXCEEDED",
            None,
            None,
            None,
            None,
            None,
        )
        self.references: dict[str, LocalAnalysisReference] = {}
        self.data_hashes: dict[str, str] = {}

    def run_exact(
        self, spec, data: bytes, expected_sha256: str
    ) -> LocalAnalysisReference:
        del spec, data
        analysis_id = f"valuation-{self.index}"
        self.index += 1
        reference = LocalAnalysisReference(
            analysis_id=analysis_id,
            generation=1,
            relative_path=Path("analyses")
            / analysis_id
            / "history"
            / ("generation-1-" + "1" * 64 + ".json"),
            sha256="1" * 64,
        )
        self.references[analysis_id] = reference
        self.data_hashes[analysis_id] = expected_sha256
        return reference

    def status(self, reference: LocalAnalysisReference):
        index = int(reference.analysis_id.rsplit("-", 1)[1])
        if index == 8:
            path = Path("analyses") / reference.analysis_id / "evidence/marker.bin"
            if self.files.bytes.get(path) == b"tampered":
                raise EvidenceTampered("mutated output")
        code = self.codes[index]
        if code is not None:
            return SimpleNamespace(
                status="exception",
                code=code,
                outputs=(),
                snapshot=self._snapshot(reference),
            )
        path = Path("analyses") / reference.analysis_id / "evidence/marker.bin"
        self.files.bytes.setdefault(path, b"")
        return SimpleNamespace(
            status="passed",
            code=None,
            outputs=(
                OutputEvidence(
                    name="marker.bin",
                    relative_path=path,
                    sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                    size_bytes=0,
                ),
            ),
            snapshot=self._snapshot(reference),
        )

    def _snapshot(self, reference: LocalAnalysisReference):
        if self.snapshot_mode == "missing":
            return None
        digest = self.data_hashes[reference.analysis_id]
        return SimpleNamespace(
            sha256="0" * 64 if self.snapshot_mode == "mismatch" else digest
        )


def offline_refs(tmp_path: Path, runner: ExitRegistry, evaluator: ExitRegistry):
    """Freeze a test-only marker catalog without weakening checked real evidence."""
    source = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    root = tmp_path / "offline-corpus"
    shutil.copytree(source, root)
    marker = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    cases = [
        {
            "case_id": case_id,
            "role": "green",
            "comparisons": [
                {
                    "comparison_type": "exact",
                    "output_name": "marker.bin",
                    "expected": marker,
                }
            ],
        }
        for case_id in ("green-hedonic", "green-travel-cost", "green-cv", "green-dce")
    ]
    (root / "evaluator/expectations.json").write_text(
        json.dumps({"cases": cases}), encoding="utf-8"
    )
    return freeze_valuation_exit_corpus(root.resolve(), runner, evaluator)


def test_valuation_evaluator_requires_all_nine_authenticated_outcomes(
    tmp_path: Path,
) -> None:
    """Dropping a receipt or accepting a tampered output must fail evaluation."""
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    refs = offline_refs(tmp_path, runner, evaluator)
    service = _OfflineService()
    executor = ValuationRegistryAnalysisExecutor(runner, service)  # type: ignore[arg-type]
    run_ref = ValuationExitRunner(runner, executor).run(refs.manifest_ref)

    report = ValuationExitEvaluator(runner, evaluator, service).evaluate(  # type: ignore[arg-type]
        run_ref, refs.catalog_ref
    )

    assert report.status == "passed" and len(report.outcomes) == 9
    runner_bytes = b"".join(path.read_bytes() for path in runner.root.rglob("*.json"))
    assert b"HEDONIC_SENSITIVITY_EXCEEDED" not in runner_bytes


@pytest.mark.parametrize("snapshot_mode", ["missing", "mismatch"])
def test_valuation_executor_rejects_non_authoritative_snapshot(
    tmp_path: Path, snapshot_mode: str
) -> None:
    """Binding an analysis without its exact input snapshot must be impossible."""
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    root = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    refs = freeze_valuation_exit_corpus(root.resolve(), runner, evaluator)
    manifest = runner.load(refs.manifest_ref, ValuationExitManifest)
    service = _OfflineService(snapshot_mode)
    executor = ValuationRegistryAnalysisExecutor(runner, service)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="snapshot"):
        executor.execute(
            next(case for case in manifest.cases if case.case_id == "green-hedonic")
        )


@pytest.mark.parametrize("snapshot_mode", ["missing", "mismatch"])
def test_valuation_evaluator_marks_non_authoritative_snapshot_unresolved(
    tmp_path: Path, snapshot_mode: str
) -> None:
    """Evaluation must not pass when a bound report loses snapshot authority."""
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    root = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    refs = freeze_valuation_exit_corpus(root.resolve(), runner, evaluator)
    service = _OfflineService()
    run_ref = ValuationExitRunner(
        runner,
        ValuationRegistryAnalysisExecutor(runner, service),  # type: ignore[arg-type]
    ).run(refs.manifest_ref)
    service.snapshot_mode = snapshot_mode

    report = ValuationExitEvaluator(runner, evaluator, service).evaluate(  # type: ignore[arg-type]
        run_ref, refs.catalog_ref
    )

    assert report.status == "failed"
