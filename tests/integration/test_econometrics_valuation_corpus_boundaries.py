"""Malformed checked-corpus and exit-contract boundary coverage."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from envresearch.econometrics.exit_models import ExpectedComparison
from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.valuation_exit_corpus import freeze_valuation_exit_corpus
from envresearch.econometrics.valuation_exit_models import (
    ValuationCaseExpectation,
    ValuationExitCase,
    ValuationExitCaseInput,
    ValuationExitExpectationCatalog,
    ValuationExitManifest,
    ValuationExitReport,
)


def _corpus(tmp_path: Path) -> Path:
    source = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    target = tmp_path / "corpus"
    shutil.copytree(source, target)
    return target


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _freeze(root: Path, tmp_path: Path) -> None:
    freeze_valuation_exit_corpus(
        root.resolve(),
        ExitRegistry((tmp_path / "runner").resolve()),
        ExitRegistry((tmp_path / "evaluator").resolve()),
    )


def test_corpus_descriptor_requires_nine_unique_cases(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    path = root / "manifest.yaml"
    payload = _read(path)
    payload["cases"] = payload["cases"][:-1]  # type: ignore[index]
    _write(path, payload)

    with pytest.raises(ValidationError, match="nine unique cases"):
        _freeze(root, tmp_path)


@pytest.mark.parametrize(
    "case_file", ("/tmp/case.yaml", "runner/case.json", "../case.yaml")
)
def test_corpus_descriptor_rejects_noncanonical_case_path(
    case_file: str, tmp_path: Path
) -> None:
    root = _corpus(tmp_path)
    path = root / "manifest.yaml"
    payload = _read(path)
    payload["cases"][0]["file"] = case_file  # type: ignore[index]
    _write(path, payload)

    with pytest.raises(ValidationError, match="canonical and relative"):
        _freeze(root, tmp_path)


@pytest.mark.parametrize(
    ("data_path", "error", "message"),
    (
        (None, TypeError, "relative data path"),
        ("/tmp/data.csv", ValueError, "canonical and relative"),
        ("../data.csv", ValueError, "canonical and relative"),
        ("outside.csv", ValueError, "escapes the checked data root"),
    ),
)
def test_corpus_case_rejects_unbound_data_path(
    data_path: str | None,
    error: type[Exception],
    message: str,
    tmp_path: Path,
) -> None:
    root = _corpus(tmp_path)
    path = root / "runner/green-hedonic.yaml"
    payload = _read(path)
    payload["spec"]["data_path"] = data_path  # type: ignore[index]
    _write(path, payload)

    with pytest.raises(error, match=message):
        _freeze(root, tmp_path)


def test_corpus_descriptor_must_match_case_payload(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    path = root / "runner/green-hedonic.yaml"
    payload = _read(path)
    payload["case_id"] = "changed-id"
    _write(path, payload)

    with pytest.raises(ValueError, match="does not match"):
        _freeze(root, tmp_path)


def test_corpus_expectations_require_case_list(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    _write(root / "evaluator/expectations.json", {"cases": {}})

    with pytest.raises(TypeError, match="case list"):
        _freeze(root, tmp_path)


def test_exit_case_rejects_invalid_name_and_mismatched_method(tmp_path: Path) -> None:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    root = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    refs = freeze_valuation_exit_corpus(root.resolve(), runner, evaluator)
    manifest = runner.load(refs.manifest_ref, ValuationExitManifest)
    case = manifest.cases[0]
    case_input = runner.load(case.case_ref, ValuationExitCaseInput)

    with pytest.raises(ValidationError):
        ValuationExitCase.model_validate({**case.model_dump(), "case_id": "bad/id"})
    with pytest.raises(ValidationError, match="family does not match"):
        ValuationExitCaseInput.model_validate(
            {**case_input.model_dump(), "family": "travel-cost"}
        )


def test_exit_manifest_rejects_duplicate_references(tmp_path: Path) -> None:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    root = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    refs = freeze_valuation_exit_corpus(root.resolve(), runner, evaluator)
    manifest = runner.load(refs.manifest_ref, ValuationExitManifest)
    cases = list(manifest.cases)
    cases[1] = cases[1].model_copy(update={"case_ref": cases[0].case_ref})

    with pytest.raises(ValidationError, match="references must be unique"):
        ValuationExitManifest.model_validate(
            {**manifest.model_dump(), "cases": tuple(cases)}
        )


def test_exit_expectation_role_contracts_and_catalog_uniqueness() -> None:
    comparison = ExpectedComparison(
        comparison_type="exact", output_name="result.csv", expected="a" * 64
    )
    with pytest.raises(ValidationError, match="comparisons only"):
        ValuationCaseExpectation(
            case_id="green",
            role="green",
            expected_code="BAD",
            comparisons=(comparison,),
        )
    with pytest.raises(ValidationError, match="exact code only"):
        ValuationCaseExpectation(case_id="failure", role="scientific-failure")

    green = ValuationCaseExpectation(
        case_id="green", role="green", comparisons=(comparison,)
    )
    with pytest.raises(ValidationError, match="case ids must be unique"):
        ValuationExitExpectationCatalog(
            schema_version="econometrics.valuation-exit-expectations.v1",
            manifest_id="manifest",
            cases=(green,) * 9,
        )


def test_exit_report_rejects_status_conflict_and_duplicate_case_ids(
    tmp_path: Path,
) -> None:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    root = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    freeze_valuation_exit_corpus(root.resolve(), runner, evaluator)
    from test_econometrics_valuation_exit import _OfflineService, offline_refs

    from envresearch.econometrics.exit_evaluator import ValuationExitEvaluator
    from envresearch.econometrics.valuation_exit_runner import (
        ValuationExitRunner,
        ValuationRegistryAnalysisExecutor,
    )

    refs = offline_refs(tmp_path / "offline", runner, evaluator)
    service = _OfflineService()
    run_ref = ValuationExitRunner(
        runner,
        ValuationRegistryAnalysisExecutor(runner, service),  # type: ignore[arg-type]
    ).run(refs.manifest_ref)
    report = ValuationExitEvaluator(runner, evaluator, service).evaluate(  # type: ignore[arg-type]
        run_ref, refs.catalog_ref
    )

    with pytest.raises(ValidationError, match="status conflicts"):
        ValuationExitReport.model_validate({**report.model_dump(), "status": "failed"})
    outcomes = list(report.outcomes)
    outcomes[1] = outcomes[1].model_copy(update={"case_id": outcomes[0].case_id})
    with pytest.raises(ValidationError, match="case ids must be unique"):
        ValuationExitReport.model_validate(
            {**report.model_dump(), "outcomes": tuple(outcomes)}
        )
