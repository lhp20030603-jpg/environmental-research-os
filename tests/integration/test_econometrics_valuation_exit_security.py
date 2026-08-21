"""Adversarial boundary tests for Valuation Core exit fixtures."""

import json
import shutil
from pathlib import Path

import pytest
from test_econometrics_valuation_exit import _OfflineService, offline_refs
from typer.testing import CliRunner

from envresearch.cli import app
from envresearch.econometrics.exit_evaluator import ValuationExitEvaluator
from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.valuation_exit_corpus import freeze_valuation_exit_corpus
from envresearch.econometrics.valuation_exit_models import (
    ValuationExitAnalysisBinding,
    ValuationExitCaseInput,
    ValuationExitExpectationCatalog,
    ValuationExitManifest,
    ValuationExitRun,
)
from envresearch.econometrics.valuation_exit_runner import (
    ValuationExitRunner,
    ValuationRegistryAnalysisExecutor,
)
from envresearch.models.artifact import ArtifactRef


def test_valuation_corpus_rejects_a_symlinked_root(tmp_path: Path) -> None:
    """Replacing checked corpus authority with a symlink must be rejected."""
    root = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    link = tmp_path / "valuation-core-link"
    link.symlink_to(root, target_is_directory=True)
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())

    with pytest.raises(ValueError, match="non-symlink"):
        freeze_valuation_exit_corpus(link.absolute(), runner, evaluator)


def test_valuation_status_cli_reads_only_the_exact_current_report(
    tmp_path: Path, monkeypatch
) -> None:
    """Replacing the exact report reference or writing during status must fail."""
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    refs = offline_refs(tmp_path, runner, evaluator)
    service = _OfflineService()
    run_ref = ValuationExitRunner(
        runner,
        ValuationRegistryAnalysisExecutor(runner, service),  # type: ignore[arg-type]
    ).run(refs.manifest_ref)
    ValuationExitEvaluator(runner, evaluator, service).evaluate(  # type: ignore[arg-type]
        run_ref, refs.catalog_ref
    )
    report_ref = evaluator.current("valuation-report-valuation-core-local")
    assert report_ref is not None
    reference_path = tmp_path / "report-reference.json"
    reference_path.write_text(report_ref.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(
        "envresearch.econometrics.cli._service_for", lambda *args, **kwargs: service
    )
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    result = CliRunner().invoke(
        app,
        [
            "econometrics",
            "valuation-exit-status",
            str(reference_path),
            "--runner-root",
            str(runner.root),
            "--evaluator-root",
            str(evaluator.root),
            "--analysis-root",
            str(tmp_path / "analysis"),
            "--json",
        ],
    )
    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert result.exit_code == 0
    assert json.loads(result.stdout)["report"]["status"] == "passed"
    assert after == before


def test_valuation_cli_separates_exact_run_from_evaluation(
    tmp_path: Path, monkeypatch
) -> None:
    """Combining run/evaluate or accepting non-JSON output must fail this test."""
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    refs = offline_refs(tmp_path, runner, evaluator)
    manifest_path = tmp_path / "manifest.json"
    catalog_path = tmp_path / "catalog.json"
    manifest_path.write_text(refs.manifest_ref.model_dump_json(), encoding="utf-8")
    catalog_path.write_text(refs.catalog_ref.model_dump_json(), encoding="utf-8")
    service = _OfflineService()
    monkeypatch.setattr(
        "envresearch.econometrics.cli._service_for", lambda *args, **kwargs: service
    )
    options = [
        "--runner-root",
        str(runner.root),
        "--evaluator-root",
        str(evaluator.root),
        "--analysis-root",
        str(tmp_path / "analysis"),
    ]
    runner_result = CliRunner().invoke(
        app,
        [
            "econometrics",
            "valuation-exit-run",
            str(manifest_path),
            *options,
            "--r-executable",
            "/usr/bin/false",
            "--r-sha256",
            "0" * 64,
            "--json",
        ],
    )
    assert runner_result.exit_code == 0
    run_path = tmp_path / "run.json"
    run_path.write_text(
        json.dumps(json.loads(runner_result.stdout)["run_reference"]),
        encoding="utf-8",
    )
    evaluation = CliRunner().invoke(
        app,
        [
            "econometrics",
            "valuation-exit-evaluate",
            str(run_path),
            str(catalog_path),
            *options,
            "--json",
        ],
    )
    assert evaluation.exit_code == 0
    assert json.loads(evaluation.stdout)["report"]["status"] == "passed"


@pytest.mark.parametrize(
    "alias",
    [("runner", "evaluator"), ("runner", "analysis"), ("evaluator", "analysis")],
)
def test_valuation_status_rejects_every_root_alias_before_opening_state(
    tmp_path: Path, alias: tuple[str, str]
) -> None:
    """Removing a pairwise root check would permit an overlapping status read."""
    roots = {
        name: (tmp_path / name).resolve()
        for name in ("runner", "evaluator", "analysis")
    }
    roots[alias[1]] = roots[alias[0]]
    reference = ArtifactRef(
        artifact_id="report", artifact_version=1, content_hash="0" * 64
    )
    reference_path = tmp_path / "report.json"
    reference_path.write_text(reference.model_dump_json(), encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "econometrics",
            "valuation-exit-status",
            str(reference_path),
            "--runner-root",
            str(roots["runner"]),
            "--evaluator-root",
            str(roots["evaluator"]),
            "--analysis-root",
            str(roots["analysis"]),
            "--json",
        ],
    )

    assert result.exit_code == 2
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "VALUATION_EXIT_STATUS_INVALID"
    assert "overlap" in error["message"]


@pytest.mark.parametrize(
    ("relative", "target"),
    (
        ("runner/green-hedonic.yaml", "runner/green-travel-cost.yaml"),
        ("runner/data/hedonic.csv", "runner/data/travel-cost.csv"),
        ("evaluator/expectations.json", "evaluator/failures.json"),
    ),
)
def test_valuation_corpus_rejects_symlinked_component(
    tmp_path: Path, relative: str, target: str
) -> None:
    """Replacing a checked descriptor, data, or expectation component must fail."""
    source = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    copied = tmp_path / "corpus"
    shutil.copytree(source, copied)
    path = copied / relative
    path.unlink()
    path.symlink_to(copied / target)

    with pytest.raises(ValueError, match="non-symlink"):
        freeze_valuation_exit_corpus(
            copied.resolve(),
            ExitRegistry((tmp_path / "runner").resolve()),
            ExitRegistry((tmp_path / "evaluator").resolve()),
        )


def test_valuation_freeze_keeps_data_bytes_and_expectations_out_of_runner(
    tmp_path: Path,
) -> None:
    """Source mutation must not alter frozen bytes or expose evaluator authority."""
    source = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    copied = tmp_path / "corpus"
    shutil.copytree(source, copied)
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    refs = freeze_valuation_exit_corpus(copied.resolve(), runner, evaluator)
    manifest = runner.load(refs.manifest_ref, ValuationExitManifest)
    case = next(item for item in manifest.cases if item.case_id == "green-hedonic")
    before = runner.load_bytes(case.data_ref)
    (copied / "runner/data/hedonic.csv").write_bytes(before + b"\nchanged")
    runner_bytes = b"".join(path.read_bytes() for path in runner.root.rglob("*.json"))

    assert runner.load_bytes(case.data_ref) == before
    assert refs.catalog_ref.artifact_id.encode() not in runner_bytes
    assert refs.catalog_ref.content_hash.encode() not in runner_bytes
    for forbidden in (
        b"expected_code",
        b"comparisons",
        b"HEDONIC_SENSITIVITY_EXCEEDED",
        b"TRAVEL_COST_DISPERSION_EXCEEDED",
        b"CV_MONOTONICITY_FAILED",
        b"DCE_CHOICE_SET_INVALID",
    ):
        assert forbidden not in runner_bytes


def test_valuation_evaluator_rejects_partial_run_and_stale_binding(
    tmp_path: Path,
) -> None:
    """Evaluation requires all current receipts and exact non-revised bindings."""
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    root = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    refs = freeze_valuation_exit_corpus(root.resolve(), runner, evaluator)
    partial = ValuationExitRun(
        schema_version="econometrics.valuation-exit-run.v1",
        manifest_ref=refs.manifest_ref,
        receipts=(),
    )
    partial_ref = runner.publish("valuation-run-valuation-core-local", partial)
    service = _OfflineService()
    protected = ValuationExitEvaluator(runner, evaluator, service)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="current generation"):
        protected.evaluate(partial_ref, refs.catalog_ref)
    runner.set_current("valuation-run-valuation-core-local", partial_ref)
    with pytest.raises(ValueError, match="incomplete"):
        protected.evaluate(partial_ref, refs.catalog_ref)

    run_ref = ValuationExitRunner(
        runner,
        ValuationRegistryAnalysisExecutor(runner, service),  # type: ignore[arg-type]
    ).run(refs.manifest_ref)
    binding_ref = runner.current("valuation-analysis-green-hedonic")
    assert binding_ref is not None
    binding = runner.load(binding_ref, ValuationExitAnalysisBinding)
    stale = binding.model_copy(update={"data_sha256": "0" * 64})
    stale_ref = runner.publish("valuation-analysis-ref-green-hedonic", stale, version=2)
    runner.set_current("valuation-analysis-green-hedonic", stale_ref)

    with pytest.raises(ValueError, match="stale or revised"):
        protected.evaluate(run_ref, refs.catalog_ref)


def test_valuation_executor_rejects_stale_case_and_data_revision(
    tmp_path: Path,
) -> None:
    """A revised case/data pair cannot reuse the original authenticated binding."""
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    root = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    refs = freeze_valuation_exit_corpus(root.resolve(), runner, evaluator)
    manifest = runner.load(refs.manifest_ref, ValuationExitManifest)
    case = next(item for item in manifest.cases if item.case_id == "green-hedonic")
    service = _OfflineService()
    executor = ValuationRegistryAnalysisExecutor(runner, service)  # type: ignore[arg-type]
    executor.execute(case)
    payload = runner.load(case.case_ref, ValuationExitCaseInput)
    revised_data = runner.publish_bytes(
        "valuation-data-green-hedonic-revised", b"changed"
    )
    revised_payload = payload.model_copy(
        update={
            "data_ref": revised_data,
            "spec": payload.spec.model_copy(
                update={
                    "data_path": runner.materialize_data(revised_data, suffix=".csv")
                }
            ),
        }
    )
    revised_ref = runner.publish(
        "valuation-case-green-hedonic-revised", revised_payload
    )
    revised_case = case.model_copy(
        update={"case_ref": revised_ref, "data_ref": revised_data}
    )

    with pytest.raises(ValueError, match="generation is stale"):
        executor.execute(revised_case)


def test_valuation_same_run_restart_rejects_forged_binding_data_hash(
    tmp_path: Path,
) -> None:
    """Restart must reject a current binding whose only revised field is data hash."""
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    root = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    refs = freeze_valuation_exit_corpus(root.resolve(), runner, evaluator)
    service = _OfflineService()
    executor = ValuationRegistryAnalysisExecutor(runner, service)  # type: ignore[arg-type]
    exit_runner = ValuationExitRunner(runner, executor)
    exit_runner.run(refs.manifest_ref)
    binding_ref = runner.current("valuation-analysis-green-hedonic")
    assert binding_ref is not None
    binding = runner.load(binding_ref, ValuationExitAnalysisBinding)
    forged = binding.model_copy(update={"data_sha256": "0" * 64})
    forged_ref = runner.publish(
        "valuation-analysis-ref-green-hedonic", forged, version=2
    )
    runner.set_current("valuation-analysis-green-hedonic", forged_ref)

    with pytest.raises(ValueError, match="stale or revised"):
        exit_runner.run(refs.manifest_ref)


def test_valuation_status_rejects_forged_current_report(tmp_path: Path) -> None:
    """A caller-published report cannot become authoritative by changing current."""
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    root = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    refs = freeze_valuation_exit_corpus(root.resolve(), runner, evaluator)
    service = _OfflineService()
    run_ref = ValuationExitRunner(
        runner,
        ValuationRegistryAnalysisExecutor(runner, service),  # type: ignore[arg-type]
    ).run(refs.manifest_ref)
    protected = ValuationExitEvaluator(runner, evaluator, service)  # type: ignore[arg-type]
    report = protected.evaluate(run_ref, refs.catalog_ref)
    forged = evaluator.publish(
        "caller-valuation-report",
        report.model_copy(
            update={
                "catalog_ref": ArtifactRef(
                    artifact_id="forged-catalog",
                    artifact_version=1,
                    content_hash="f" * 64,
                )
            }
        ),
    )
    evaluator.set_current("valuation-report-valuation-core-local", forged)

    with pytest.raises(ValueError, match="authority"):
        protected.status(forged)


def test_valuation_evaluator_never_discovers_a_latest_catalog(tmp_path: Path) -> None:
    """Replacing an explicit catalog with a later protected object must be rejected."""
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    root = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    refs = freeze_valuation_exit_corpus(root.resolve(), runner, evaluator)
    service = _OfflineService()
    run_ref = ValuationExitRunner(
        runner,
        ValuationRegistryAnalysisExecutor(runner, service),  # type: ignore[arg-type]
    ).run(refs.manifest_ref)
    later = evaluator.publish(
        "valuation-catalog-later",
        evaluator.load(refs.catalog_ref, ValuationExitExpectationCatalog),
    )

    with pytest.raises(ValueError, match="exact manifest"):
        ValuationExitEvaluator(runner, evaluator, service).evaluate(  # type: ignore[arg-type]
            run_ref, later
        )
