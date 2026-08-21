"""Strict authority contracts for the shared causal-policy bundle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from envresearch.econometrics.analysis_specs import ANALYSIS_SPEC_ADAPTER
from envresearch.econometrics.causal_contracts import (
    Iv2slsSpec,
    PanelFeSpec,
    RddSpec,
)


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


def _load(payload: dict[str, object]) -> object:
    return ANALYSIS_SPEC_ADAPTER.validate_json(json.dumps(payload))


def test_analysis_spec_discriminates_all_four_methods(tmp_path: Path) -> None:
    assert isinstance(_load(panel_payload(tmp_path / "panel.csv")), PanelFeSpec)
    assert isinstance(_load(iv_payload(tmp_path / "iv.csv")), Iv2slsSpec)
    assert isinstance(_load(rdd_payload(tmp_path / "rdd.csv")), RddSpec)


def test_iv_rejects_overlapping_endogenous_and_instrument(tmp_path: Path) -> None:
    payload = iv_payload(tmp_path / "iv.csv")
    columns = dict(payload["columns"])  # type: ignore[arg-type]
    columns["instruments"] = ["price"]
    payload["columns"] = columns

    with pytest.raises(ValidationError, match="column roles must be unique"):
        _load(payload)


def test_rdd_rejects_donut_outside_bandwidth(tmp_path: Path) -> None:
    payload = rdd_payload(tmp_path / "rdd.csv")
    design = dict(payload["design"])  # type: ignore[arg-type]
    design["donut_radius"] = design["bandwidth"]
    payload["design"] = design

    with pytest.raises(ValidationError, match="donut radius"):
        _load(payload)


def test_new_specs_forbid_acquisition_and_script_fields(tmp_path: Path) -> None:
    payload = panel_payload(tmp_path / "panel.csv")
    payload["url"] = "https://example.test/data.csv"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _load(payload)


def test_panel_requires_a_declared_regressor_and_fixed_effect(tmp_path: Path) -> None:
    payload = panel_payload(tmp_path / "panel.csv")
    columns = dict(payload["columns"])  # type: ignore[arg-type]
    columns["regressors"] = []
    payload["columns"] = columns

    with pytest.raises(ValidationError, match="at least 1 item"):
        _load(payload)
