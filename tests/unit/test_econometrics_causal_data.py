"""Method-specific local CSV shape validation on the shared snapshot path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from envresearch.econometrics.analysis_specs import (
    ANALYSIS_SPEC_ADAPTER,
    AnalysisSpec,
)
from envresearch.econometrics.data_snapshot import LocalDataInvalid
from envresearch.econometrics.local_validation import validate_csv


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _spec(payload: dict[str, object]) -> AnalysisSpec:
    return ANALYSIS_SPEC_ADAPTER.validate_json(json.dumps(payload))


def _common(path: Path) -> dict[str, object]:
    return {
        "data_path": str(path),
        "inference": {"confidence_level": 0.95, "cluster_column": None},
        "budget": {
            "inactivity_seconds": 60,
            "max_output_bytes": 1_000_000,
            "max_workspace_bytes": 10_000_000,
        },
    }


def panel_payload(path: Path) -> dict[str, object]:
    return {
        **_common(path),
        "schema_version": "econometrics.panel-fe.v1",
        "method_id": "panel-fe",
        "columns": {
            "unit": "unit",
            "time": "year",
            "outcome": "emissions",
            "regressors": ["policy"],
            "fixed_effects": ["unit", "year"],
        },
    }


def iv_payload(path: Path) -> dict[str, object]:
    return {
        **_common(path),
        "schema_version": "econometrics.iv-2sls.v1",
        "method_id": "iv-2sls",
        "columns": {
            "outcome": "emissions",
            "endogenous": ["price"],
            "instruments": ["wind"],
            "controls": ["income"],
            "fixed_effects": ["region"],
        },
        "weak_instrument_f_threshold": 10.0,
    }


def rdd_payload(path: Path) -> dict[str, object]:
    return {
        **_common(path),
        "schema_version": "econometrics.rdd-local-linear.v1",
        "method_id": "rdd-local-linear",
        "columns": {
            "outcome": "emissions",
            "running": "score",
            "covariates": ["income"],
        },
        "design": {
            "cutoff": 0.0,
            "bandwidth": 4.0,
            "donut_radius": 0.25,
            "kernel": "triangular",
        },
    }


def test_panel_rejects_duplicate_unit_time_keys(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "panel.csv",
        "unit,year,emissions,policy\nA,1,1,0\nA,1,2,1\n",
    )

    with pytest.raises(LocalDataInvalid, match="unit-time rows"):
        validate_csv(_spec(panel_payload(path)))


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("1,,2,3", "endogenous values"),
        ("1,2,,3", "instrument values"),
        (",2,3,4", "outcome values"),
    ],
)
def test_iv_requires_finite_declared_numeric_roles(
    tmp_path: Path, replacement: str, message: str
) -> None:
    path = _write(
        tmp_path / "iv.csv",
        f"emissions,price,wind,income,region\n{replacement},north\n",
    )

    with pytest.raises(LocalDataInvalid, match=message):
        validate_csv(_spec(iv_payload(path)))


def test_rdd_requires_support_on_both_cutoff_sides(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "rdd.csv",
        "emissions,score,income\n1,0,5\n2,1,6\n3,2,7\n4,3,8\n",
    )

    with pytest.raises(LocalDataInvalid, match="both sides of the cutoff"):
        validate_csv(_spec(rdd_payload(path)))


def test_rdd_requires_each_sensitivity_window_to_have_support(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "rdd.csv",
        "emissions,score,income\n"
        "1,-4,5\n2,-3.5,6\n3,-3,7\n4,-2.5,8\n"
        "5,2.5,9\n6,3,10\n7,3.5,11\n8,4,12\n",
    )

    with pytest.raises(LocalDataInvalid, match="half-bandwidth window"):
        validate_csv(_spec(rdd_payload(path)))


def test_valid_new_method_shapes_are_read_only(tmp_path: Path) -> None:
    panel = _write(
        tmp_path / "panel.csv",
        "unit,year,emissions,policy\nA,1,1,0\nA,2,2,1\nB,1,2,0\nB,2,4,1\n",
    )
    iv = _write(
        tmp_path / "iv.csv",
        "emissions,price,wind,income,region\n1,2,3,4,north\n2,3,4,5,south\n",
    )
    rdd_rows = ["emissions,score,income"]
    rdd_rows.extend(
        f"{10 + value},{value},{20 + value}"
        for value in (item / 2 for item in range(-8, 9))
    )
    rdd = _write(tmp_path / "rdd.csv", "\n".join(rdd_rows) + "\n")
    original = {path: path.read_bytes() for path in (panel, iv, rdd)}

    validations = (
        validate_csv(_spec(panel_payload(panel))),
        validate_csv(_spec(iv_payload(iv))),
        validate_csv(_spec(rdd_payload(rdd))),
    )

    assert tuple(item.row_count for item in validations) == (4, 2, 17)
    assert all(path.read_bytes() == data for path, data in original.items())
