"""Tests for deterministic benchmark output comparators."""

import json
from pathlib import Path

import pytest

from envresearch.benchmarks.compare import ComparisonStatus, compare_output
from envresearch.models.benchmark import ExpectedOutput


def _spec(
    comparator: str,
    *,
    path: str = "result",
    expected_path: str = "result",
    absolute_tolerance: float = 0.0,
    relative_tolerance: float = 0.0,
) -> ExpectedOutput:
    return ExpectedOutput(
        path=path,
        comparator=comparator,
        expected_path=expected_path,
        absolute_tolerance=absolute_tolerance,
        relative_tolerance=relative_tolerance,
    )


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    actual = tmp_path / "actual"
    expected = tmp_path / "expected"
    actual.mkdir()
    expected.mkdir()
    return actual, expected


def test_exact_comparison_matches_identical_file_hashes(tmp_path: Path) -> None:
    """Exact comparisons use byte-level SHA-256 equality."""
    actual, expected = _roots(tmp_path)
    (actual / "result.txt").write_bytes(b"same bytes\n")
    (expected / "expected").mkdir()
    (expected / "expected" / "result.txt").write_bytes(b"same bytes\n")

    result = compare_output(
        actual,
        expected,
        _spec("exact", path="result.txt", expected_path="expected/result.txt"),
    )

    assert result.status is ComparisonStatus.MATCHED
    assert result.differences == ()


def test_exact_comparison_reports_hashes_for_different_files(tmp_path: Path) -> None:
    """Exact mismatches retain both content hashes as structured evidence."""
    actual, expected = _roots(tmp_path)
    (actual / "result.txt").write_text("actual", encoding="utf-8")
    (expected / "result.txt").write_text("expected", encoding="utf-8")

    result = compare_output(
        actual, expected, _spec("exact", path="result.txt", expected_path="result.txt")
    )

    assert result.status is ComparisonStatus.MISMATCHED
    assert result.total_differences == 1
    assert result.differences[0].location == "$"
    assert result.differences[0].expected == result.expected_sha256
    assert result.differences[0].actual == result.actual_sha256


def test_comparison_reports_missing_actual_and_missing_expected_files(
    tmp_path: Path,
) -> None:
    """Absent actual output and absent fixture output are distinguishable."""
    actual, expected = _roots(tmp_path)
    (expected / "result.txt").write_text("expected", encoding="utf-8")

    missing_actual = compare_output(
        actual, expected, _spec("exact", path="result.txt", expected_path="result.txt")
    )
    (actual / "result.txt").write_text("actual", encoding="utf-8")
    (expected / "result.txt").unlink()
    missing_expected = compare_output(
        actual, expected, _spec("exact", path="result.txt", expected_path="result.txt")
    )

    assert missing_actual.status is ComparisonStatus.MISSING
    assert missing_actual.differences[0].location == "$"
    assert missing_expected.status is ComparisonStatus.UNEXPECTED
    assert missing_expected.differences[0].location == "$"


def test_json_numeric_comparison_honors_absolute_tolerance(tmp_path: Path) -> None:
    actual, expected = _roots(tmp_path)
    (actual / "result.json").write_text('{"estimate": 1.0004}', encoding="utf-8")
    (expected / "result.json").write_text('{"estimate": 1.0}', encoding="utf-8")

    result = compare_output(
        actual,
        expected,
        _spec(
            "json_numeric",
            path="result.json",
            expected_path="result.json",
            absolute_tolerance=0.001,
        ),
    )

    assert result.status is ComparisonStatus.MATCHED


def test_json_numeric_reports_unexpected_keys_at_nested_jsonpath(
    tmp_path: Path,
) -> None:
    """Extra nested object members must identify their JSONPath precisely."""
    actual, expected = _roots(tmp_path)
    (actual / "result.json").write_text(
        '{"model": {"score": 1, "unexpected": 2}}', encoding="utf-8"
    )
    (expected / "result.json").write_text('{"model": {"score": 1}}', encoding="utf-8")

    result = compare_output(
        actual,
        expected,
        _spec("json_numeric", path="result.json", expected_path="result.json"),
    )

    assert result.status is ComparisonStatus.MISMATCHED
    assert result.differences[0].location == "$.model.unexpected"
    assert result.differences[0].expected is None
    assert result.differences[0].actual == 2


def test_json_numeric_uses_expected_value_in_relative_tolerance_formula(
    tmp_path: Path,
) -> None:
    """Tolerance is atol + rtol * abs(expected), not a symmetric formula."""
    actual, expected = _roots(tmp_path)
    (actual / "result.json").write_text('{"estimate": 111}', encoding="utf-8")
    (expected / "result.json").write_text('{"estimate": 100}', encoding="utf-8")

    result = compare_output(
        actual,
        expected,
        _spec(
            "json_numeric",
            path="result.json",
            expected_path="result.json",
            absolute_tolerance=0.5,
            relative_tolerance=0.1,
        ),
    )

    assert result.status is ComparisonStatus.MISMATCHED
    assert result.differences[0].location == "$.estimate"
    assert result.differences[0].tolerance == 10.5


def test_json_numeric_does_not_treat_booleans_as_numbers(tmp_path: Path) -> None:
    """Python bool is an int subclass but JSON booleans require exact equality."""
    actual, expected = _roots(tmp_path)
    (actual / "result.json").write_text('{"flag": true}', encoding="utf-8")
    (expected / "result.json").write_text('{"flag": 1}', encoding="utf-8")

    result = compare_output(
        actual,
        expected,
        _spec("json_numeric", path="result.json", expected_path="result.json"),
    )

    assert result.status is ComparisonStatus.MISMATCHED
    assert result.differences[0].location == "$.flag"


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_json_numeric_rejects_nonfinite_values_as_parse_errors(
    tmp_path: Path, constant: str
) -> None:
    """NaN and infinity are invalid JSON and never silently compare equal."""
    actual, expected = _roots(tmp_path)
    (actual / "result.json").write_text(f'{{"score": {constant}}}', encoding="utf-8")
    (expected / "result.json").write_text('{"score": 1}', encoding="utf-8")

    result = compare_output(
        actual,
        expected,
        _spec("json_numeric", path="result.json", expected_path="result.json"),
    )

    assert result.status is ComparisonStatus.ERROR
    assert result.differences[0].location == "$"


def test_json_numeric_reports_malformed_json(tmp_path: Path) -> None:
    actual, expected = _roots(tmp_path)
    (actual / "result.json").write_text('{"score":', encoding="utf-8")
    (expected / "result.json").write_text('{"score": 1}', encoding="utf-8")

    result = compare_output(
        actual,
        expected,
        _spec("json_numeric", path="result.json", expected_path="result.json"),
    )

    assert result.status is ComparisonStatus.ERROR
    assert result.differences[0].location == "$"


def test_json_numeric_contains_recursion_errors_from_valid_deep_json(
    tmp_path: Path,
) -> None:
    """Host recursion limits must not escape the structured comparator API."""
    actual, expected = _roots(tmp_path)
    deep_json = '{"child":' * 1_500 + "0" + "}" * 1_500
    (actual / "result.json").write_text(deep_json, encoding="utf-8")
    (expected / "result.json").write_text(deep_json, encoding="utf-8")

    result = compare_output(
        actual,
        expected,
        _spec("json_numeric", path="result.json", expected_path="result.json"),
    )

    assert result.status is ComparisonStatus.ERROR
    assert result.differences[0].location == "$"


def test_json_numeric_contains_relative_tolerance_overflow(tmp_path: Path) -> None:
    """Huge valid JSON integers must not leak arithmetic OverflowError."""
    actual, expected = _roots(tmp_path)
    huge_integer = "1" + "0" * 400
    payload = f'{{"estimate": {huge_integer}}}'
    (actual / "result.json").write_text(payload, encoding="utf-8")
    (expected / "result.json").write_text(payload, encoding="utf-8")

    result = compare_output(
        actual,
        expected,
        _spec(
            "json_numeric",
            path="result.json",
            expected_path="result.json",
            relative_tolerance=0.1,
        ),
    )

    assert result.status is ComparisonStatus.ERROR
    assert result.differences[0].location == "$"


def test_csv_numeric_reports_header_row_and_column_differences(tmp_path: Path) -> None:
    """CSV diagnostics name headers, row counts, and individual cell coordinates."""
    actual, expected = _roots(tmp_path)
    (actual / "result.csv").write_text(
        "name,value,extra\na,1,x\nb,3,y\n", encoding="utf-8"
    )
    (expected / "result.csv").write_text("name,value\na,1\n", encoding="utf-8")

    result = compare_output(
        actual,
        expected,
        _spec("csv_numeric", path="result.csv", expected_path="result.csv"),
    )

    assert result.status is ComparisonStatus.MISMATCHED
    assert [difference.location for difference in result.differences] == [
        "row=0,column=extra",
        "rows",
    ]


def test_csv_numeric_honors_tolerance_and_compares_text_exactly(tmp_path: Path) -> None:
    actual, expected = _roots(tmp_path)
    (actual / "result.csv").write_text("name,value\nA,1.004\n", encoding="utf-8")
    (expected / "result.csv").write_text("name,value\na,1.0\n", encoding="utf-8")

    result = compare_output(
        actual,
        expected,
        _spec(
            "csv_numeric",
            path="result.csv",
            expected_path="result.csv",
            absolute_tolerance=0.01,
        ),
    )

    assert result.status is ComparisonStatus.MISMATCHED
    assert result.differences[0].location == "row=1,column=name"
    assert result.differences[0].tolerance is None


def test_csv_numeric_rejects_duplicate_headers_and_malformed_csv(
    tmp_path: Path,
) -> None:
    actual, expected = _roots(tmp_path)
    (actual / "result.csv").write_text("score,score\n1,2\n", encoding="utf-8")
    (expected / "result.csv").write_text("score\n1\n", encoding="utf-8")

    duplicate_headers = compare_output(
        actual,
        expected,
        _spec("csv_numeric", path="result.csv", expected_path="result.csv"),
    )
    (actual / "result.csv").write_text('score\n"unterminated\n', encoding="utf-8")
    malformed_csv = compare_output(
        actual,
        expected,
        _spec("csv_numeric", path="result.csv", expected_path="result.csv"),
    )

    assert duplicate_headers.status is ComparisonStatus.ERROR
    assert duplicate_headers.differences[0].location == "row=0,column=score"
    assert malformed_csv.status is ComparisonStatus.ERROR
    assert malformed_csv.differences[0].location == "$"


def test_comparison_enforces_safe_paths_even_for_constructed_models(
    tmp_path: Path,
) -> None:
    """Comparator resolution remains safe if a caller bypasses model validation."""
    actual, expected = _roots(tmp_path)
    unsafe_spec = ExpectedOutput.model_construct(
        path=Path("../outside.txt"),
        comparator="exact",
        expected_path=Path("result.txt"),
        absolute_tolerance=0.0,
        relative_tolerance=0.0,
    )

    result = compare_output(actual, expected, unsafe_spec)

    assert result.status is ComparisonStatus.ERROR
    assert result.differences[0].location == "$"


def test_comparison_rejects_constructed_unsupported_comparator(
    tmp_path: Path,
) -> None:
    """Invalid models must not silently take the CSV comparator path."""
    actual, expected = _roots(tmp_path)
    (actual / "result.txt").write_text("same", encoding="utf-8")
    (expected / "result.txt").write_text("same", encoding="utf-8")
    unsupported_spec = ExpectedOutput.model_construct(
        path=Path("result.txt"),
        comparator="unsupported",
        expected_path=Path("result.txt"),
        absolute_tolerance=0.0,
        relative_tolerance=0.0,
    )

    result = compare_output(actual, expected, unsupported_spec)

    assert result.status is ComparisonStatus.ERROR
    assert result.differences[0].message == "unsupported comparator: unsupported"


def test_comparison_bounds_reported_differences_but_keeps_total_count(
    tmp_path: Path,
) -> None:
    """Large artifacts remain diagnosable without producing unbounded runner data."""
    actual, expected = _roots(tmp_path)
    actual_payload = {f"key_{index:04}": index for index in range(1_000)}
    expected_payload = {f"key_{index:04}": -index - 1 for index in range(1_000)}
    (actual / "result.json").write_text(json.dumps(actual_payload), encoding="utf-8")
    (expected / "result.json").write_text(
        json.dumps(expected_payload), encoding="utf-8"
    )

    result = compare_output(
        actual,
        expected,
        _spec("json_numeric", path="result.json", expected_path="result.json"),
    )

    assert result.status is ComparisonStatus.MISMATCHED
    assert len(result.differences) == 100
    assert result.total_differences == 1_000
