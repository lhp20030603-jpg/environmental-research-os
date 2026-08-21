"""Finite numeric-tolerance validation and comparator defense in depth."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from envresearch.benchmarks.compare import ComparisonStatus, compare_output
from envresearch.models.benchmark import ExpectedOutput


@pytest.mark.parametrize("field", ["absolute_tolerance", "relative_tolerance"])
@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "neg-inf"]
)
def test_expected_output_rejects_nonfinite_tolerance(
    field: str, value: float
) -> None:
    """Non-finite tolerances can make arbitrary numeric differences pass."""
    with pytest.raises(ValidationError, match="finite"):
        ExpectedOutput(
            path=Path("result.json"),
            comparator="json_numeric",
            expected_path=Path("expected.json"),
            **{field: value},
        )


def test_comparator_rejects_forged_nonfinite_tolerance(tmp_path: Path) -> None:
    """Direct comparator callers must not bypass the validated model boundary."""
    actual = tmp_path / "actual"
    expected = tmp_path / "expected"
    actual.mkdir()
    expected.mkdir()
    (actual / "result.json").write_text('{"value": 999}', encoding="utf-8")
    (expected / "result.json").write_text('{"value": 1}', encoding="utf-8")
    forged = ExpectedOutput.model_construct(
        path=Path("result.json"),
        comparator="json_numeric",
        expected_path=Path("result.json"),
        absolute_tolerance=float("inf"),
        relative_tolerance=0.0,
    )

    result = compare_output(actual, expected, forged)

    assert result.status is ComparisonStatus.ERROR
    assert "finite" in (result.differences[0].message or "")


def test_finite_tolerance_formula_overflow_cannot_approve_difference(
    tmp_path: Path,
) -> None:
    """Finite manifest fields must not overflow into an approving infinity."""
    actual = tmp_path / "actual"
    expected = tmp_path / "expected"
    actual.mkdir()
    expected.mkdir()
    (actual / "result.json").write_text('{"value": -1e308}', encoding="utf-8")
    (expected / "result.json").write_text('{"value": 1e308}', encoding="utf-8")
    spec = ExpectedOutput(
        path=Path("result.json"),
        comparator="json_numeric",
        expected_path=Path("result.json"),
        absolute_tolerance=1e308,
        relative_tolerance=1.0,
    )

    result = compare_output(actual, expected, spec)

    assert result.status is ComparisonStatus.ERROR
    assert "overflow" in (result.differences[0].message or "")
