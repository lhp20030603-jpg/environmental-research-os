"""Contracts for the compact Valuation Core exit matrix."""

from collections import Counter
from pathlib import Path

import pytest

from envresearch.econometrics.exit_models import ExpectedComparison
from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.valuation_exit_corpus import freeze_valuation_exit_corpus
from envresearch.econometrics.valuation_exit_models import (
    ValuationCaseExpectation,
    ValuationExitManifest,
)


def test_valuation_manifest_requires_exact_nine_case_matrix(tmp_path: Path) -> None:
    """Removing a green family or changing a role must reject the checked matrix."""
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    root = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    refs = freeze_valuation_exit_corpus(root.resolve(), runner, evaluator)
    manifest = runner.load(refs.manifest_ref, ValuationExitManifest)

    assert len(manifest.cases) == 9
    assert Counter(case.role for case in manifest.cases) == {
        "green": 4,
        "scientific-failure": 4,
        "integrity-failure": 1,
    }
    assert {case.family for case in manifest.cases if case.role == "green"} == {
        "hedonic-pricing",
        "travel-cost",
        "contingent-valuation",
        "dce-clogit",
    }


def test_valuation_manifest_rejects_swapped_family_with_preserved_coverage(
    tmp_path: Path,
) -> None:
    """Changing the fixed case-to-family binding must fail, not merely coverage."""
    manifest = _manifest(tmp_path)
    cases = list(manifest.cases)
    hedonic = next(index for index, case in enumerate(cases) if case.case_id == "green-hedonic")
    travel = next(index for index, case in enumerate(cases) if case.case_id == "green-travel-cost")
    cases[hedonic] = cases[hedonic].model_copy(update={"family": "travel-cost"})
    cases[travel] = cases[travel].model_copy(update={"family": "hedonic-pricing"})

    with pytest.raises(ValueError, match="exact nine-case matrix"):
        ValuationExitManifest.model_validate(
            {**manifest.model_dump(), "cases": tuple(cases)}
        )


def test_valuation_manifest_rejects_swapped_role_with_preserved_counts(
    tmp_path: Path,
) -> None:
    """Moving a role between fixed case IDs must be rejected even when counts remain."""
    manifest = _manifest(tmp_path)
    cases = list(manifest.cases)
    green = next(index for index, case in enumerate(cases) if case.case_id == "green-hedonic")
    failure = next(index for index, case in enumerate(cases) if case.case_id == "fail-hedonic-sensitivity")
    cases[green] = cases[green].model_copy(update={"role": "scientific-failure"})
    cases[failure] = cases[failure].model_copy(update={"role": "green"})

    with pytest.raises(ValueError, match="exact nine-case matrix"):
        ValuationExitManifest.model_validate(
            {**manifest.model_dump(), "cases": tuple(cases)}
        )


def test_valuation_expectations_reject_duplicate_output_selector() -> None:
    """Removing duplicate comparison validation would accept ambiguous evidence."""
    comparison = ExpectedComparison(
        comparison_type="exact", output_name="marker.bin", expected="0" * 64
    )

    with pytest.raises(ValueError, match="comparisons must be unique"):
        ValuationCaseExpectation(
            case_id="green-hedonic", role="green", comparisons=(comparison, comparison)
        )


def _manifest(tmp_path: Path) -> ValuationExitManifest:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    root = Path(__file__).parents[2] / "benchmarks/econometrics/valuation-core"
    refs = freeze_valuation_exit_corpus(root.resolve(), runner, evaluator)
    return runner.load(refs.manifest_ref, ValuationExitManifest)
