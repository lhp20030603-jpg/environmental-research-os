"""Deterministic comparators for benchmark output artifacts."""

import csv
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TypeAlias, TypeGuard, cast

from envresearch.models.benchmark import ExpectedOutput
from envresearch.storage.hashing import sha256_file
from envresearch.storage.paths import safe_join

JsonValue: TypeAlias = (
    dict[str, "JsonValue"] | list["JsonValue"] | str | int | float | bool | None
)
_MAX_REPORTED_DIFFERENCES = 100


class _ComparisonParseError(ValueError):
    """A parse failure with a precise, user-facing artifact location."""

    def __init__(self, location: str, message: str) -> None:
        super().__init__(message)
        self.location = location


class ComparisonStatus(str, Enum):
    """Terminal status of one declared benchmark output comparison."""

    MATCHED = "matched"
    MISMATCHED = "mismatched"
    MISSING = "missing"
    UNEXPECTED = "unexpected"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ComparisonDifference:
    """One deterministic location-level difference between two artifacts."""

    location: str
    expected: object | None
    actual: object | None
    tolerance: float | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    """Structured outcome consumable by a benchmark replay runner."""

    status: ComparisonStatus
    differences: tuple[ComparisonDifference, ...] = ()
    total_differences: int = 0
    actual_sha256: str | None = None
    expected_sha256: str | None = None


@dataclass(slots=True)
class _DifferenceCollector:
    """Count every difference while retaining bounded deterministic detail."""

    details: list[ComparisonDifference] = field(default_factory=list)
    total: int = 0

    def add(
        self,
        location: str,
        expected: object | None,
        actual: object | None,
        *,
        tolerance: float | None = None,
        message: str | None = None,
    ) -> None:
        self.total += 1
        if len(self.details) < _MAX_REPORTED_DIFFERENCES:
            self.details.append(
                ComparisonDifference(
                    location,
                    expected,
                    actual,
                    tolerance=tolerance,
                    message=message,
                )
            )


def compare_output(
    actual_root: Path, expected_root: Path, spec: ExpectedOutput
) -> ComparisonResult:
    """Compare one actual output against its manifest-root expected artifact."""
    tolerances = (spec.absolute_tolerance, spec.relative_tolerance)
    if any(not math.isfinite(value) or value < 0 for value in tolerances):
        return _error_result("comparison tolerances must be finite and non-negative")
    try:
        actual_path = safe_join(actual_root, spec.path)
        expected_path = safe_join(expected_root, spec.expected_path)
    except ValueError as error:
        return _error_result(str(error))

    if not actual_path.is_file():
        return _single_difference_result(
            ComparisonStatus.MISSING,
            ComparisonDifference(
                "$", "expected output", None, message="actual output missing"
            ),
        )
    if not expected_path.is_file():
        return _single_difference_result(
            ComparisonStatus.UNEXPECTED,
            ComparisonDifference(
                "$", None, "actual output", message="expected output missing"
            ),
        )

    try:
        if spec.comparator == "exact":
            return _compare_exact(actual_path, expected_path)
        if spec.comparator == "json_numeric":
            return _compare_json_numeric(actual_path, expected_path, spec)
        if spec.comparator == "csv_numeric":
            return _compare_csv_numeric(actual_path, expected_path, spec)
        return _error_result(f"unsupported comparator: {spec.comparator}")
    except _ComparisonParseError as error:
        return _error_result(str(error), location=error.location)
    except (OSError, ValueError, csv.Error, OverflowError, RecursionError) as error:
        return _error_result(str(error))


def _compare_exact(actual_path: Path, expected_path: Path) -> ComparisonResult:
    actual_sha256 = sha256_file(actual_path)
    expected_sha256 = sha256_file(expected_path)
    if actual_sha256 == expected_sha256:
        return ComparisonResult(
            status=ComparisonStatus.MATCHED,
            actual_sha256=actual_sha256,
            expected_sha256=expected_sha256,
        )
    return _single_difference_result(
        ComparisonStatus.MISMATCHED,
        ComparisonDifference("$", expected_sha256, actual_sha256),
        actual_sha256=actual_sha256,
        expected_sha256=expected_sha256,
    )


def _compare_json_numeric(
    actual_path: Path, expected_path: Path, spec: ExpectedOutput
) -> ComparisonResult:
    actual = _read_json(actual_path)
    expected = _read_json(expected_path)
    collector = _DifferenceCollector()
    _compare_json_value("$", expected, actual, spec, collector)
    return _differences_result(collector)


def _read_json(path: Path) -> JsonValue:
    with path.open(encoding="utf-8") as file:
        value = json.load(
            file,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_nonfinite_json_constant,
        )
    return cast(JsonValue, value)


def _unique_json_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_json_constant(value: str) -> JsonValue:
    raise ValueError(f"non-finite JSON constant is not permitted: {value}")


def _compare_json_value(
    location: str,
    expected: JsonValue,
    actual: JsonValue,
    spec: ExpectedOutput,
    collector: _DifferenceCollector,
) -> None:
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            key_location = _json_key_location(location, key)
            if key not in expected:
                collector.add(key_location, None, actual[key])
            elif key not in actual:
                collector.add(key_location, expected[key], None)
            else:
                _compare_json_value(
                    key_location, expected[key], actual[key], spec, collector
                )
        return
    if isinstance(expected, list) and isinstance(actual, list):
        shared_length = min(len(expected), len(actual))
        for index in range(shared_length):
            _compare_json_value(
                f"{location}[{index}]",
                expected[index],
                actual[index],
                spec,
                collector,
            )
        for index in range(shared_length, len(expected)):
            collector.add(f"{location}[{index}]", expected[index], None)
        for index in range(shared_length, len(actual)):
            collector.add(f"{location}[{index}]", None, actual[index])
        return
    if _is_number(expected) and _is_number(actual):
        tolerance = _numeric_tolerance(spec, expected)
        if not _numbers_match(expected, actual, tolerance):
            collector.add(location, expected, actual, tolerance=tolerance)
        return
    if type(expected) is not type(actual) or expected != actual:
        collector.add(location, expected, actual)


def _json_key_location(parent: str, key: str) -> str:
    if key.isidentifier():
        return f"{parent}.{key}"
    return f"{parent}[{json.dumps(key, ensure_ascii=False)}]"


def _compare_csv_numeric(
    actual_path: Path, expected_path: Path, spec: ExpectedOutput
) -> ComparisonResult:
    actual_headers, actual_rows = _read_csv(actual_path)
    expected_headers, expected_rows = _read_csv(expected_path)
    collector = _DifferenceCollector()

    for index, expected_header in enumerate(expected_headers):
        actual_header = actual_headers[index] if index < len(actual_headers) else None
        if expected_header != actual_header:
            collector.add(
                f"row=0,column={expected_header}", expected_header, actual_header
            )
    for header in actual_headers[len(expected_headers) :]:
        collector.add(f"row=0,column={header}", None, header)

    if len(expected_rows) != len(actual_rows):
        collector.add("rows", len(expected_rows), len(actual_rows))

    actual_columns = set(actual_headers)
    shared_rows = min(len(expected_rows), len(actual_rows))
    for row_index in range(shared_rows):
        for column_index, header in enumerate(expected_headers):
            if header not in actual_columns:
                continue
            actual_column_index = actual_headers.index(header)
            expected_value = expected_rows[row_index][column_index]
            actual_value = actual_rows[row_index][actual_column_index]
            mismatched, tolerance = _compare_csv_value(
                expected_value, actual_value, spec
            )
            if mismatched:
                collector.add(
                    f"row={row_index + 1},column={header}",
                    expected_value,
                    actual_value,
                    tolerance=tolerance,
                )
    return _differences_result(collector)


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.reader(file, strict=True))
    if not rows:
        raise ValueError("CSV has no header row")
    headers = rows[0]
    for header in headers:
        if headers.count(header) > 1:
            raise _ComparisonParseError(
                f"row=0,column={header}", f"duplicate CSV header: {header}"
            )
    for row_number, row in enumerate(rows[1:], start=1):
        if len(row) != len(headers):
            raise ValueError(
                f"CSV row {row_number} has {len(row)} cells; expected {len(headers)}"
            )
    return headers, rows[1:]


def _compare_csv_value(
    expected: str, actual: str, spec: ExpectedOutput
) -> tuple[bool, float | None]:
    expected_number = _parse_float(expected)
    actual_number = _parse_float(actual)
    if expected_number is not None and actual_number is not None:
        tolerance = _numeric_tolerance(spec, expected_number)
        if _numbers_match(expected_number, actual_number, tolerance):
            return False, None
        return True, tolerance
    if expected == actual:
        return False, None
    return True, None


def _numeric_tolerance(spec: ExpectedOutput, expected: float) -> float:
    tolerance = spec.absolute_tolerance + spec.relative_tolerance * abs(expected)
    if not math.isfinite(tolerance):
        raise OverflowError("numeric tolerance overflow")
    return tolerance


def _parse_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _is_number(value: JsonValue) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numbers_match(expected: float, actual: float, tolerance: float) -> bool:
    if math.isnan(expected) or math.isnan(actual):
        return False
    if math.isinf(expected) or math.isinf(actual):
        return expected == actual
    return abs(actual - expected) <= tolerance


def _differences_result(collector: _DifferenceCollector) -> ComparisonResult:
    if not collector.details:
        return ComparisonResult(status=ComparisonStatus.MATCHED)
    return ComparisonResult(
        status=ComparisonStatus.MISMATCHED,
        differences=tuple(collector.details),
        total_differences=collector.total,
    )


def _single_difference_result(
    status: ComparisonStatus,
    difference: ComparisonDifference,
    *,
    actual_sha256: str | None = None,
    expected_sha256: str | None = None,
) -> ComparisonResult:
    return ComparisonResult(
        status=status,
        differences=(difference,),
        total_differences=1,
        actual_sha256=actual_sha256,
        expected_sha256=expected_sha256,
    )


def _error_result(message: str, *, location: str = "$") -> ComparisonResult:
    return _single_difference_result(
        ComparisonStatus.ERROR,
        ComparisonDifference(location, None, None, message=message),
    )
