"""Independent support reconstruction from authenticated local CSV bytes."""

from __future__ import annotations

import json
from pathlib import Path

from envresearch.econometrics._causal_support import support_matches_snapshot
from envresearch.econometrics.analysis_specs import ANALYSIS_SPEC_ADAPTER
from envresearch.econometrics.causal_contracts import Iv2slsSpec, PanelFeSpec, RddSpec
from envresearch.econometrics.causal_models import RddSupport, RegressionSupport

FIXTURES = Path(__file__).parents[1] / "fixtures" / "econometrics"


def _spec(path: Path, method_id: str) -> PanelFeSpec | Iv2slsSpec | RddSpec:
    method: dict[str, object]
    if method_id == "panel-fe":
        method = {
            "schema_version": "econometrics.panel-fe.v1",
            "columns": {
                "unit": "unit",
                "time": "year",
                "outcome": "emissions",
                "regressors": ["policy"],
                "fixed_effects": ["unit", "year"],
            },
        }
    elif method_id == "iv-2sls":
        method = {
            "schema_version": "econometrics.iv-2sls.v1",
            "columns": {
                "outcome": "emissions",
                "endogenous": ["price"],
                "instruments": ["wind"],
                "controls": ["income"],
                "fixed_effects": ["unit", "year"],
            },
            "weak_instrument_f_threshold": 10.0,
        }
    else:
        method = {
            "schema_version": "econometrics.rdd-local-linear.v1",
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
    spec = ANALYSIS_SPEC_ADAPTER.validate_json(
        json.dumps(
            {
                **method,
                "method_id": method_id,
                "data_path": str(path),
                "inference": {
                    "confidence_level": 0.95,
                    "cluster_column": None
                    if method_id == "rdd-local-linear"
                    else "unit",
                },
                "budget": {
                    "inactivity_seconds": 60,
                    "max_output_bytes": 1_000_000,
                    "max_workspace_bytes": 10_000_000,
                },
            }
        )
    )
    assert isinstance(spec, (PanelFeSpec, Iv2slsSpec, RddSpec))
    return spec


def test_panel_support_is_reconstructed_from_snapshot_bytes() -> None:
    path = FIXTURES / "panel_fe.csv"
    support = RegressionSupport(observations=12, clusters=4, units=4, time_periods=3)

    spec = _spec(path, "panel-fe")
    assert support_matches_snapshot(path.read_bytes(), spec, support)
    assert not support_matches_snapshot(
        path.read_bytes(), spec, support.model_copy(update={"units": 5})
    )


def test_iv_cluster_support_is_reconstructed_from_snapshot_bytes() -> None:
    path = FIXTURES / "iv_2sls.csv"
    support = RegressionSupport(observations=32, clusters=8)

    spec = _spec(path, "iv-2sls")
    assert support_matches_snapshot(path.read_bytes(), spec, support)
    assert not support_matches_snapshot(
        path.read_bytes(), spec, support.model_copy(update={"clusters": 7})
    )


def test_every_rdd_window_count_is_reconstructed_from_snapshot_bytes() -> None:
    path = FIXTURES / "rdd_local_linear.csv"
    support = RddSupport(
        observations=19,
        left_observations=9,
        right_observations=10,
        left_unique_running=9,
        right_unique_running=10,
        donut_left_observations=9,
        donut_right_observations=9,
    )

    spec = _spec(path, "rdd-local-linear")
    assert support_matches_snapshot(path.read_bytes(), spec, support)
    assert not support_matches_snapshot(
        path.read_bytes(),
        spec,
        support.model_copy(update={"right_unique_running": 9}),
    )
