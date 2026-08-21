"""Frozen authorities for the remaining Wave-1 method families."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from envresearch.econometrics.analysis_specs import ANALYSIS_SPEC_ADAPTER
from envresearch.econometrics.wave1_contracts import (
    EnvironmentalMeasurementSpec,
    MetaAnalysisSpec,
    RctSpec,
    SyntheticControlSpec,
)


def _common(path: Path) -> dict[str, object]:
    return {
        "data_path": str(path),
        "budget": {
            "inactivity_seconds": 60,
            "max_output_bytes": 1_000_000,
            "max_workspace_bytes": 10_000_000,
        },
    }


def payloads(path: Path) -> tuple[dict[str, object], ...]:
    return (
        {
            **_common(path),
            "schema_version": "econometrics.rct-itt.v1",
            "method_id": "rct-itt",
            "columns": {
                "unit": "unit",
                "assignment": "assigned",
                "outcome": "outcome",
                "baseline_covariates": ["baseline"],
            },
            "inference": {"confidence_level": 0.95, "cluster_column": None},
            "max_attrition_rate": 0.1,
            "balance_smd_threshold": 0.2,
        },
        {
            **_common(path),
            "schema_version": "econometrics.synthetic-control.v1",
            "method_id": "synthetic-control",
            "columns": {
                "unit": "unit",
                "time": "year",
                "outcome": "emissions",
                "predictors": [],
            },
            "treated_unit": "treated",
            "intervention_time": 2010.0,
            "max_pre_rmspe": 1.0,
            "max_leave_one_out_change": 0.5,
        },
        {
            **_common(path),
            "schema_version": "econometrics.environmental-measurement.v1",
            "method_id": "environmental-measurement",
            "columns": {
                "monitor": "monitor",
                "timestamp": "date",
                "value": "pm25",
                "unit": "unit",
                "detection_flag": "flag",
            },
            "declared_unit": "ug/m3",
            "max_missing_rate": 0.25,
            "valid_min": 0.0,
            "valid_max": 500.0,
            "exceedance_threshold": 35.0,
        },
        {
            **_common(path),
            "schema_version": "econometrics.meta-analysis.v1",
            "method_id": "meta-analysis",
            "columns": {"study": "study", "effect": "effect", "variance": "var"},
            "confidence_level": 0.95,
            "max_leave_one_out_change": 0.5,
            "model": "fixed-and-dl-random",
        },
    )


def test_union_loads_all_remaining_wave1_specs(tmp_path: Path) -> None:
    loaded = tuple(
        ANALYSIS_SPEC_ADAPTER.validate_json(json.dumps(item))
        for item in payloads(tmp_path / "data.csv")
    )
    assert tuple(type(item) for item in loaded) == (
        RctSpec,
        SyntheticControlSpec,
        EnvironmentalMeasurementSpec,
        MetaAnalysisSpec,
    )


@pytest.mark.parametrize("index", range(4))
def test_wave1_specs_reject_relative_paths_and_unknown_fields(
    tmp_path: Path, index: int
) -> None:
    payload = payloads(tmp_path / "data.csv")[index]
    payload["data_path"] = "relative.csv"
    payload["download_url"] = "https://example.test/data"
    with pytest.raises(ValidationError):
        ANALYSIS_SPEC_ADAPTER.validate_json(json.dumps(payload))


def test_wave1_specs_reject_duplicate_roles_and_invalid_thresholds(
    tmp_path: Path,
) -> None:
    rct, scm, measurement, meta = payloads(tmp_path / "data.csv")
    rct["columns"] = {"unit": "x", "assignment": "x", "outcome": "y"}
    scm["max_pre_rmspe"] = 0.0
    measurement["valid_max"] = -1.0
    meta["confidence_level"] = 1.0
    for payload in (rct, scm, measurement, meta):
        with pytest.raises(ValidationError):
            ANALYSIS_SPEC_ADAPTER.validate_json(json.dumps(payload))


def test_v03_scm_rejects_unimplemented_predictor_adjustment(tmp_path: Path) -> None:
    payload = payloads(tmp_path / "data.csv")[1]
    columns = deepcopy(payload["columns"])
    assert isinstance(columns, dict)
    columns["predictors"] = ["income"]
    payload["columns"] = columns
    with pytest.raises(ValidationError, match="predictor-adjusted"):
        ANALYSIS_SPEC_ADAPTER.validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("index", "field"),
    (
        (0, "max_attrition_rate"),
        (0, "balance_smd_threshold"),
        (1, "max_pre_rmspe"),
        (1, "max_leave_one_out_change"),
        (3, "max_leave_one_out_change"),
    ),
)
def test_wave1_specs_reject_nonfinite_thresholds(
    tmp_path: Path, index: int, field: str
) -> None:
    payload = payloads(tmp_path / "data.csv")[index]
    payload[field] = float("inf")
    with pytest.raises(ValidationError):
        ANALYSIS_SPEC_ADAPTER.validate_json(json.dumps(payload))


def test_v03_rct_rejects_cluster_randomized_inference(tmp_path: Path) -> None:
    payload = payloads(tmp_path / "data.csv")[0]
    payload["inference"] = {
        "confidence_level": 0.95,
        "cluster_column": "school",
    }
    with pytest.raises(ValidationError, match="cluster-randomized"):
        ANALYSIS_SPEC_ADAPTER.validate_json(json.dumps(payload))


@pytest.mark.parametrize("index", range(4))
def test_wave1_specs_separately_reject_relative_paths(
    tmp_path: Path, index: int
) -> None:
    payload = payloads(tmp_path / "data.csv")[index]
    payload["data_path"] = "relative.csv"
    with pytest.raises(ValidationError, match="absolute"):
        ANALYSIS_SPEC_ADAPTER.validate_json(json.dumps(payload))


@pytest.mark.parametrize("index", range(4))
def test_wave1_specs_separately_reject_unknown_fields(
    tmp_path: Path, index: int
) -> None:
    payload = payloads(tmp_path / "data.csv")[index]
    payload["download_url"] = "https://example.test/data"
    with pytest.raises(ValidationError, match="Extra inputs"):
        ANALYSIS_SPEC_ADAPTER.validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("index", "mutation"),
    (
        (0, "no-baseline"),
        (0, "duplicate-role"),
        (0, "bad-balance"),
        (0, "cluster"),
        (1, "blank-treated"),
        (1, "infinite-time"),
        (1, "zero-prefit"),
        (1, "duplicate-predictor"),
        (2, "duplicate-role"),
        (2, "reversed-range"),
        (2, "infinite-range"),
        (2, "vacuous-missing"),
        (3, "duplicate-role"),
        (3, "invalid-confidence"),
        (3, "infinite-sensitivity"),
        (3, "wrong-model"),
    ),
)
def test_each_wave1_method_rejects_four_invalid_authorities(
    tmp_path: Path, index: int, mutation: str
) -> None:
    payload = deepcopy(payloads(tmp_path / "data.csv")[index])
    columns = payload["columns"]
    assert isinstance(columns, dict)
    if mutation == "no-baseline":
        columns["baseline_covariates"] = []
    elif mutation == "duplicate-role":
        keys = tuple(columns)
        columns[keys[1]] = columns[keys[0]]
    elif mutation == "bad-balance":
        payload["balance_smd_threshold"] = 0.0
    elif mutation == "cluster":
        payload["inference"] = {"confidence_level": 0.95, "cluster_column": "g"}
    elif mutation == "blank-treated":
        payload["treated_unit"] = " "
    elif mutation == "infinite-time":
        payload["intervention_time"] = float("inf")
    elif mutation == "zero-prefit":
        payload["max_pre_rmspe"] = 0.0
    elif mutation == "duplicate-predictor":
        columns["predictors"] = [columns["outcome"]]
    elif mutation == "reversed-range":
        payload["valid_min"], payload["valid_max"] = 2.0, 1.0
    elif mutation == "infinite-range":
        payload["valid_max"] = float("inf")
    elif mutation == "vacuous-missing":
        payload["max_missing_rate"] = 1.0
    elif mutation == "invalid-confidence":
        payload["confidence_level"] = 1.0
    elif mutation == "infinite-sensitivity":
        payload["max_leave_one_out_change"] = float("inf")
    elif mutation == "wrong-model":
        payload["model"] = "random-only"
    with pytest.raises(ValidationError):
        ANALYSIS_SPEC_ADAPTER.validate_json(json.dumps(payload))
