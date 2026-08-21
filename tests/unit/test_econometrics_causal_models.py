"""Strict typed result evidence shared by the causal-policy recipes."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from envresearch.econometrics._causal_outputs import CausalOutputInvalid, read_rows
from envresearch.econometrics.causal_models import (
    BandwidthEstimate,
    CausalPackageConfiguration,
    FirstStageDiagnostic,
    Iv2slsResult,
    RddResult,
    RddSupport,
    RegressionCoefficient,
    RegressionSupport,
)
from envresearch.econometrics.recipes import recipe_for


def _coefficient(term: str = "policy", estimate: float = 1.0) -> dict[str, object]:
    return {
        "term": term,
        "estimate": estimate,
        "std_error": 0.2,
        "conf_low": estimate - 0.4,
        "conf_high": estimate + 0.4,
    }


def _configuration(method_id: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "method_id": method_id,
        "r_version": "R version 4.4.3",
        "fixest_version": "0.14.0",
        "confidence_level": 0.95,
        "cluster_column": None,
        "fixed_effects": ("unit", "year") if method_id != "rdd-local-linear" else (),
        "estimator_label": {
            "panel-fe": "fixest::feols-panel-fe",
            "iv-2sls": "fixest::feols-2sls",
            "rdd-local-linear": "sharp-local-linear",
        }[method_id],
        "cutoff": None,
        "bandwidth": None,
        "kernel": None,
        "donut_radius": None,
    }
    if method_id == "rdd-local-linear":
        payload.update(
            cutoff=0.0,
            bandwidth=4.0,
            kernel="triangular",
            donut_radius=0.25,
        )
    return payload


def _iv_result(first_stage_f: float, threshold: float) -> dict[str, object]:
    return {
        "method_id": "iv-2sls",
        "structural": (_coefficient("price"),),
        "first_stage": (
            {
                "endogenous": "price",
                "instruments": ("wind",),
                "f_statistic": first_stage_f,
                "threshold": threshold,
            },
        ),
        "reduced_form": (_coefficient("wind"),),
        "overidentification": None,
        "support": {"observations": 100, "clusters": None},
        "configuration": _configuration("iv-2sls"),
        "figure_sha256": "a" * 64,
    }


def _rdd_result(multipliers: tuple[float, ...]) -> dict[str, object]:
    return {
        "method_id": "rdd-local-linear",
        "main": _coefficient("cutoff"),
        "bandwidth_sensitivity": tuple(
            {"multiplier": value, "coefficient": _coefficient("cutoff", value)}
            for value in multipliers
        ),
        "donut": _coefficient("cutoff", 0.9),
        "covariate_continuity": (_coefficient("income", 0.0),),
        "support": {
            "observations": 100,
            "left_observations": 50,
            "right_observations": 50,
            "left_unique_running": 20,
            "right_unique_running": 20,
            "donut_left_observations": 40,
            "donut_right_observations": 40,
        },
        "configuration": _configuration("rdd-local-linear"),
        "figure_sha256": "b" * 64,
        "inference_limitation": (
            "local-linear conventional inference; rdrobust RBC not included"
        ),
    }


def test_regression_coefficient_rejects_nonfinite_values() -> None:
    with pytest.raises(ValidationError, match="finite"):
        RegressionCoefficient.model_validate(_coefficient(estimate=float("nan")))


def test_iv_result_cannot_pass_below_declared_first_stage_threshold() -> None:
    with pytest.raises(ValidationError, match="weak instrument"):
        Iv2slsResult.model_validate(_iv_result(first_stage_f=4.2, threshold=10.0))


def test_iv_result_requires_one_first_stage_per_endogenous_term() -> None:
    payload = _iv_result(first_stage_f=20.0, threshold=10.0)
    payload["structural"] = (
        *_iv_result(20.0, 10.0)["structural"],  # type: ignore[misc]
        _coefficient("fuel"),
    )

    with pytest.raises(ValidationError, match="first-stage coverage"):
        Iv2slsResult.model_validate(payload)


def test_rdd_result_requires_all_sensitivity_windows() -> None:
    with pytest.raises(ValidationError, match="0.5, 1.0, and 1.5"):
        RddResult.model_validate(_rdd_result((0.5, 1.0)))


def test_rdd_result_rejects_missing_donut_support() -> None:
    payload = _rdd_result((0.5, 1.0, 1.5))
    support = dict(payload["support"])  # type: ignore[arg-type]
    support["donut_left_observations"] = 0
    payload["support"] = support

    with pytest.raises(ValidationError):
        RddResult.model_validate(payload)


def test_shared_csv_reader_rejects_extra_columns(tmp_path: Path) -> None:
    path = tmp_path / "coefficients.csv"
    path.write_text("term,estimate,extra\npolicy,1,hidden\n", encoding="utf-8")

    with pytest.raises(CausalOutputInvalid, match="invalid schema"):
        read_rows(path, ("term", "estimate"))


def test_result_models_are_strict_and_frozen() -> None:
    coefficient = RegressionCoefficient.model_validate(_coefficient())
    with pytest.raises(ValidationError, match="frozen"):
        coefficient.estimate = 2.0


def test_public_primitives_validate_complete_green_examples() -> None:
    assert (
        FirstStageDiagnostic.model_validate(
            _iv_result(20.0, 10.0)["first_stage"][0]  # type: ignore[index]
        ).f_statistic
        == 20.0
    )
    assert RegressionSupport(observations=10, clusters=2).clusters == 2
    assert (
        CausalPackageConfiguration.model_validate(
            _configuration("panel-fe")
        ).fixest_version
        == "0.14.0"
    )
    assert (
        BandwidthEstimate.model_validate(
            {"multiplier": 1.0, "coefficient": _coefficient("cutoff")}
        ).multiplier
        == 1.0
    )
    assert RddSupport.model_validate(_rdd_result((0.5, 1.0, 1.5))["support"])


def test_existing_did_recipe_exposes_its_exact_output_manifest(tmp_path: Path) -> None:
    recipe = recipe_for("did-event-study", workspace=tmp_path / "work")

    assert recipe.expected_outputs == frozenset(
        {
            "baseline.csv",
            "group_time_att.csv",
            "dynamic.csv",
            "support.csv",
            "support_by_group_time.csv",
            "cohort_timing.csv",
            "covariate_balance.csv",
            "package_configuration.csv",
            "event_study.svg",
        }
    )
