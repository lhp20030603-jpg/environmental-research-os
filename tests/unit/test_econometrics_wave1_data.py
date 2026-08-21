"""CSV shape gates for remaining Wave-1 methods."""

import csv
import json
from pathlib import Path

import pytest
from test_econometrics_wave1_contracts import payloads

from envresearch.econometrics._causal_csv import validate_rows
from envresearch.econometrics.analysis_specs import ANALYSIS_SPEC_ADAPTER
from envresearch.econometrics.data_snapshot import LocalDataInvalid


def _spec(path: Path, index: int) -> object:
    return ANALYSIS_SPEC_ADAPTER.validate_json(json.dumps(payloads(path)[index]))


FIXTURES = Path(__file__).parents[1] / "fixtures" / "econometrics"


@pytest.mark.parametrize(
    ("index", "filename"),
    (
        (0, "rct_itt.csv"),
        (1, "synthetic_control.csv"),
        (2, "environmental_measurement.csv"),
        (3, "meta_analysis.csv"),
    ),
)
def test_owned_wave1_csv_fixtures_pass(index: int, filename: str) -> None:
    path = FIXTURES / filename
    with path.open(newline="", encoding="utf-8") as handle:
        table = tuple(tuple(row) for row in csv.reader(handle))
    validate_rows(table[0], table[1:], _spec(path, index))  # type: ignore[arg-type]


def test_rct_requires_binary_assignment_and_unique_units(tmp_path: Path) -> None:
    spec = _spec(tmp_path / "rct.csv", 0)
    header = ("unit", "assigned", "outcome", "baseline")
    with pytest.raises(LocalDataInvalid):
        validate_rows(header, (("a", "2", "1", "0"),), spec)  # type: ignore[arg-type]


@pytest.mark.parametrize("assignment", ("0", "1"))
def test_rct_requires_both_assignment_arms(tmp_path: Path, assignment: str) -> None:
    spec = _spec(tmp_path / "rct.csv", 0)
    header = ("unit", "assigned", "outcome", "baseline")
    rows = (("a", assignment, "1", "0"), ("b", assignment, "2", "1"))
    with pytest.raises(LocalDataInvalid, match="both treatment and control"):
        validate_rows(header, rows, spec)  # type: ignore[arg-type]


def test_scm_requires_balanced_treated_and_donor_panel(tmp_path: Path) -> None:
    spec = _spec(tmp_path / "scm.csv", 1)
    header = ("unit", "year", "emissions", "income")
    rows = (
        ("treated", "2009", "10", "1"),
        ("treated", "2010", "9", "1"),
        ("donor-a", "2009", "8", "1"),
        ("donor-a", "2010", "8", "1"),
        ("donor-b", "2009", "7", "1"),
    )
    with pytest.raises(LocalDataInvalid, match="balanced"):
        validate_rows(header, rows, spec)  # type: ignore[arg-type]


def test_rct_rejects_duplicate_units_and_nonfinite_baseline(tmp_path: Path) -> None:
    spec = _spec(tmp_path / "rct.csv", 0)
    header = ("unit", "assigned", "outcome", "baseline")
    with pytest.raises(LocalDataInvalid, match="unique"):
        validate_rows(
            header,
            (("a", "0", "1", "0"), ("a", "1", "2", "1")),
            spec,  # type: ignore[arg-type]
        )
    with pytest.raises(LocalDataInvalid, match="finite"):
        validate_rows(
            header,
            (("a", "0", "1", "nan"), ("b", "1", "2", "1")),
            spec,  # type: ignore[arg-type]
        )


def test_scm_rejects_duplicate_keys_and_missing_support(tmp_path: Path) -> None:
    spec = _spec(tmp_path / "scm.csv", 1)
    header = ("unit", "year", "emissions", "income")
    duplicate = (
        ("treated", "2009", "10", "1"),
        ("treated", "2009", "9", "1"),
    )
    with pytest.raises(LocalDataInvalid, match="unique"):
        validate_rows(header, duplicate, spec)  # type: ignore[arg-type]
    donors_only = (
        ("a", "2009", "8", "1"),
        ("a", "2010", "8", "1"),
        ("b", "2009", "7", "1"),
        ("b", "2010", "7", "1"),
        ("c", "2009", "6", "1"),
        ("c", "2010", "6", "1"),
    )
    with pytest.raises(LocalDataInvalid, match="treated"):
        validate_rows(header, donors_only, spec)  # type: ignore[arg-type]


def test_measurement_rejects_duplicate_keys_and_nonfinite_values(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path / "m.csv", 2)
    header = ("monitor", "date", "pm25", "unit", "flag")
    with pytest.raises(LocalDataInvalid, match="unique"):
        validate_rows(
            header,
            (
                ("a", "2020-01-01", "1", "ug/m3", ""),
                ("a", "2020-01-01", "2", "ug/m3", ""),
            ),
            spec,  # type: ignore[arg-type]
        )
    with pytest.raises(LocalDataInvalid, match="finite"):
        validate_rows(
            header,
            (("a", "2020-01-01", "inf", "ug/m3", ""),),
            spec,  # type: ignore[arg-type]
        )


def test_meta_rejects_duplicate_studies_and_nonfinite_effects(tmp_path: Path) -> None:
    spec = _spec(tmp_path / "meta.csv", 3)
    header = ("study", "effect", "var")
    with pytest.raises(LocalDataInvalid, match="unique"):
        validate_rows(
            header,
            (("a", "1", "1"), ("a", "2", "1")),
            spec,  # type: ignore[arg-type]
        )
    with pytest.raises(LocalDataInvalid, match="positive"):
        validate_rows(
            header,
            (("a", "1", "0"), ("b", "2", "1"), ("c", "3", "1")),
            spec,  # type: ignore[arg-type]
        )
    with pytest.raises(LocalDataInvalid, match="three studies"):
        validate_rows(
            header,
            (("a", "1", "1"), ("b", "2", "1")),
            spec,  # type: ignore[arg-type]
        )
    with pytest.raises(LocalDataInvalid, match="finite"):
        validate_rows(
            header,
            (("a", "nan", "1"), ("b", "2", "1")),
            spec,  # type: ignore[arg-type]
        )


def test_measurement_requires_declared_units_and_unique_keys(tmp_path: Path) -> None:
    spec = _spec(tmp_path / "m.csv", 2)
    header = ("monitor", "date", "pm25", "unit", "flag")
    with pytest.raises(LocalDataInvalid):
        validate_rows(header, (("a", "2020-01-01", "1", "ppm", ""),), spec)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("rows", "code"),
    (
        (
            (
                ("a", "2020-01-01", "600", "ug/m3", ""),
                ("b", "2020-01-01", "1", "ug/m3", ""),
            ),
            "MEASUREMENT_RANGE_INVALID",
        ),
        (
            (
                ("a", "2020-01-01", "1", "ug/m3", "invented"),
                ("b", "2020-01-01", "2", "ug/m3", ""),
            ),
            "MEASUREMENT_DETECTION_FLAG_INVALID",
        ),
        (
            (
                ("a", "2020-01-01", "", "ug/m3", "valid"),
                ("b", "2020-01-01", "2", "ug/m3", ""),
            ),
            "MEASUREMENT_DETECTION_FLAG_INVALID",
        ),
        (
            (
                ("a", "2020-01-01", "1", "ug/m3", "below-detection"),
                ("b", "2020-01-01", "2", "ug/m3", ""),
            ),
            "MEASUREMENT_DETECTION_FLAG_INVALID",
        ),
        (
            (
                ("a", "2020-01-01", "", "ug/m3", "below-detection"),
                ("b", "2020-01-01", "", "ug/m3", "below-detection"),
            ),
            "MEASUREMENT_MISSINGNESS_EXCEEDED",
        ),
    ),
)
def test_measurement_threshold_failures_have_stable_codes(
    tmp_path: Path, rows: tuple[tuple[str, ...], ...], code: str
) -> None:
    spec = _spec(tmp_path / "m.csv", 2)
    header = ("monitor", "date", "pm25", "unit", "flag")
    with pytest.raises(LocalDataInvalid) as captured:
        validate_rows(header, rows, spec)  # type: ignore[arg-type]
    assert captured.value.code == code


def test_rct_attrition_failure_has_stable_code(tmp_path: Path) -> None:
    payload = payloads(tmp_path / "rct.csv")[0]
    payload["max_attrition_rate"] = 0.1
    spec = ANALYSIS_SPEC_ADAPTER.validate_json(json.dumps(payload))
    header = ("unit", "assigned", "outcome", "baseline")
    rows = (
        ("a", "0", "", "0"),
        ("b", "0", "1", "1"),
        ("c", "1", "2", "0"),
        ("d", "1", "3", "1"),
    )
    with pytest.raises(LocalDataInvalid) as captured:
        validate_rows(header, rows, spec)  # type: ignore[arg-type]
    assert captured.value.code == "RCT_ATTRITION_EXCEEDED"


def test_rct_rejects_multivariate_deterministic_assignment_leakage(
    tmp_path: Path,
) -> None:
    payload = payloads(tmp_path / "rct.csv")[0]
    columns = payload["columns"]
    assert isinstance(columns, dict)
    columns["baseline_covariates"] = ["x1", "x2"]
    payload["balance_smd_threshold"] = 1.0
    spec = ANALYSIS_SPEC_ADAPTER.validate_json(json.dumps(payload))
    header = ("unit", "assigned", "outcome", "x1", "x2")
    rows = tuple(
        (f"u{index}", str(x1 ^ x2), str(10 + index), str(x1), str(x2))
        for index, (x1, x2) in enumerate(((0, 0), (0, 1), (1, 0), (1, 1)) * 3)
    )
    with pytest.raises(LocalDataInvalid) as captured:
        validate_rows(header, rows, spec)  # type: ignore[arg-type]
    assert captured.value.code == "RCT_ASSIGNMENT_LEAKAGE"


def test_meta_requires_positive_variance_and_unique_studies(tmp_path: Path) -> None:
    spec = _spec(tmp_path / "meta.csv", 3)
    header = ("study", "effect", "var")
    with pytest.raises(LocalDataInvalid):
        validate_rows(header, (("a", "1", "0"),), spec)  # type: ignore[arg-type]
