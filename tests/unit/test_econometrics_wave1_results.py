"""Typed invariant tests for remaining Wave-1 results."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from envresearch.econometrics.wave1_results import (
    DonorWeight,
    EnvironmentalMeasurementResult,
    LeaveOneOutEffect,
    MeasurementResult,
    MeasurementSupport,
    MetaAnalysisResult,
    RctResult,
    StudyWeight,
    SyntheticControlResult,
)


def _coefficient(term: str = "effect") -> dict[str, object]:
    return {
        "term": term,
        "estimate": 1.0,
        "std_error": 0.1,
        "conf_low": 0.8,
        "conf_high": 1.2,
    }


def _configuration(method_id: str) -> dict[str, object]:
    package = {
        "rct-itt": "fixest",
        "synthetic-control": "synthdid",
        "meta-analysis": "metafor",
    }.get(method_id, "base")
    return {
        "method_id": method_id,
        "r_version": "4.4.3",
        "confidence_level": 0.95 if method_id in {"rct-itt", "meta-analysis"} else None,
        "package_authorities": (
            {
                "artifact_id": f"r-package-authority-{package}-1.0.0",
                "artifact_version": 1,
                "content_hash": "a" * 64,
            },
        ),
    }


def _rct() -> dict[str, object]:
    return {
        "method_id": "rct-itt",
        "unadjusted": _coefficient("assigned"),
        "ancova": _coefficient("assigned"),
        "support": {
            "total": 10,
            "assigned_control": 5,
            "assigned_treated": 5,
            "control_outcomes_observed": 4,
            "treated_outcomes_observed": 5,
            "outcomes_observed": 9,
            "outcomes_missing": 1,
        },
        "balance": ({"term": "baseline", "smd": 0.1},),
        "attrition_rate": 0.1,
        "max_attrition_rate": 0.2,
        "max_abs_balance_smd": 0.1,
        "balance_smd_threshold": 0.2,
        "configuration": _configuration("rct-itt"),
        "figure_sha256": "b" * 64,
    }


def _scm() -> dict[str, object]:
    return {
        "method_id": "synthetic-control",
        "effect": _coefficient(),
        "donor_weights": (
            {"donor": "a", "weight": 0.6},
            {"donor": "b", "weight": 0.4},
        ),
        "gaps": (
            {"time": 1, "treated": 1, "synthetic": 0.8, "gap": 0.2, "period": "pre"},
            {"time": 2, "treated": 2, "synthetic": 2.2, "gap": -0.2, "period": "pre"},
            {"time": 3, "treated": 3, "synthetic": 2.8, "gap": 0.2, "period": "pre"},
            {"time": 4, "treated": 3, "synthetic": 2, "gap": 1, "period": "post"},
            {"time": 5, "treated": 3, "synthetic": 2, "gap": 1, "period": "post"},
        ),
        "placebos": ({"unit": "a", "effect": 0.1}, {"unit": "b", "effect": -0.1}),
        "leave_one_out": (
            {"omitted": "a", "effect": 1.1, "absolute_change": 0.1},
            {"omitted": "b", "effect": 1.2, "absolute_change": 0.2},
        ),
        "pre_periods": 3,
        "post_periods": 2,
        "pre_rmspe": 0.2,
        "post_rmspe": 1.0,
        "post_pre_ratio": 5.0,
        "intervention_time": 4.0,
        "package_version": "1.0.0",
        "max_pre_rmspe": 1.0,
        "max_leave_one_out_change": 0.2,
        "leave_one_out_threshold": 0.5,
        "configuration": _configuration("synthetic-control"),
        "figure_sha256": "b" * 64,
    }


def _measurement() -> dict[str, object]:
    return {
        "method_id": "environmental-measurement",
        "support": {"total": 10, "valid": 8, "missing": 2, "monitors": 2},
        "quantiles": {"q25": 15.0, "median": 20.0, "q75": 25.0},
        "temporal": ({"date": "2020-01-01", "mean": 20.0},),
        "monitor_coverage": (
            {"monitor": "a", "total": 5, "valid": 4, "missing": 1},
            {"monitor": "b", "total": 5, "valid": 4, "missing": 1},
        ),
        "mean": 20.0,
        "minimum": 10.0,
        "maximum": 30.0,
        "exceedances": 1,
        "exceedance_threshold": 35.0,
        "missing_rate": 0.2,
        "max_missing_rate": 0.3,
        "declared_unit": "ug/m3",
        "configuration": _configuration("environmental-measurement"),
        "figure_sha256": "b" * 64,
    }


def _meta() -> dict[str, object]:
    return {
        "method_id": "meta-analysis",
        "fixed": _coefficient(),
        "random": _coefficient(),
        "study_weights": (
            {"study": "a", "effect": 0.9, "std_error": 0.2, "weight": 0.6},
            {"study": "b", "effect": 1.1, "std_error": 0.3, "weight": 0.4},
        ),
        "funnel": (
            {"study": "a", "effect": 0.9, "std_error": 0.2},
            {"study": "b", "effect": 1.1, "std_error": 0.3},
        ),
        "leave_one_out": (
            {"omitted": "a", "effect": 1.1, "absolute_change": 0.1},
            {"omitted": "b", "effect": 1.2, "absolute_change": 0.2},
        ),
        "studies": 2,
        "q": 1.0,
        "i_squared": 20.0,
        "tau_squared": 0.1,
        "inverse_variance_support": 36.11111111111111,
        "prediction_low": 0.5,
        "prediction_high": 1.5,
        "package_version": "1.0.0",
        "model": "fixed-and-dl-random",
        "max_leave_one_out_change": 0.2,
        "leave_one_out_threshold": 0.5,
        "configuration": _configuration("meta-analysis"),
        "figure_sha256": "b" * 64,
    }


@pytest.mark.parametrize(
    ("model", "payload"),
    (
        (RctResult, _rct()),
        (SyntheticControlResult, _scm()),
        (EnvironmentalMeasurementResult, _measurement()),
        (MetaAnalysisResult, _meta()),
    ),
)
def test_complete_wave1_results_validate(model: type[object], payload: object) -> None:
    assert model.model_validate(payload)  # type: ignore[attr-defined]


def test_public_measurement_result_name_is_stable() -> None:
    assert MeasurementResult is EnvironmentalMeasurementResult


def test_donor_weights_are_individually_bounded() -> None:
    with pytest.raises(ValidationError):
        DonorWeight(donor="control", weight=1.1)


def test_study_weights_are_finite_and_bounded() -> None:
    with pytest.raises(ValidationError):
        StudyWeight(study="study-a", effect=0.1, std_error=0.2, weight=float("nan"))


def test_measurement_support_reconciles_missing_and_valid_counts() -> None:
    with pytest.raises(ValidationError, match="reconcile"):
        MeasurementSupport(total=10, valid=8, missing=1, monitors=2)


def test_rct_attrition_must_equal_support() -> None:
    payload = _rct()
    payload["attrition_rate"] = 0.0
    with pytest.raises(ValidationError, match="support"):
        RctResult.model_validate(payload)


def test_measurement_monitor_count_cannot_exceed_rows() -> None:
    payload = _measurement()
    support = deepcopy(payload["support"])
    assert isinstance(support, dict)
    support["monitors"] = 11
    payload["support"] = support
    with pytest.raises(ValidationError, match="monitor"):
        EnvironmentalMeasurementResult.model_validate(payload)


@pytest.mark.parametrize(
    ("model", "payload"),
    ((SyntheticControlResult, _scm()), (MetaAnalysisResult, _meta())),
)
def test_sensitivity_results_are_complete_and_recomputed(
    model: type[object], payload: dict[str, object]
) -> None:
    missing = deepcopy(payload)
    missing["leave_one_out"] = ()
    with pytest.raises(ValidationError):
        model.model_validate(missing)  # type: ignore[attr-defined]

    forged = deepcopy(payload)
    forged["max_leave_one_out_change"] = 0.01
    with pytest.raises(ValidationError, match="maximum"):
        model.model_validate(forged)  # type: ignore[attr-defined]


def test_leave_one_out_effect_requires_finite_values() -> None:
    with pytest.raises(ValidationError):
        LeaveOneOutEffect(omitted="a", effect=float("inf"), absolute_change=0.1)


@pytest.mark.parametrize(
    ("model", "payload"),
    ((SyntheticControlResult, _scm()), (MetaAnalysisResult, _meta())),
)
def test_leave_one_out_change_is_recomputed_from_central_effect(
    model: type[object], payload: dict[str, object]
) -> None:
    payload = deepcopy(payload)
    records = list(payload["leave_one_out"])  # type: ignore[arg-type]
    records[0] = {"omitted": "a", "effect": 1000.0, "absolute_change": 0.1}
    payload["leave_one_out"] = tuple(records)
    with pytest.raises(ValidationError, match="central effect"):
        model.model_validate(payload)  # type: ignore[attr-defined]


@pytest.mark.parametrize("field", ("max_abs_balance_smd", "balance_smd_threshold"))
def test_rct_rejects_nonfinite_balance_diagnostics(field: str) -> None:
    payload = _rct()
    payload[field] = float("inf")
    with pytest.raises(ValidationError, match="finite"):
        RctResult.model_validate(payload)


@pytest.mark.parametrize(
    ("model", "payload", "field", "value"),
    (
        (RctResult, _rct(), "max_abs_balance_smd", 0.3),
        (EnvironmentalMeasurementResult, _measurement(), "max_missing_rate", 0.1),
    ),
)
def test_wave1_results_reject_diagnostics_above_threshold(
    model: type[object], payload: dict[str, object], field: str, value: object
) -> None:
    payload = deepcopy(payload)
    payload[field] = value
    if model is RctResult:
        payload["balance"] = ({"term": "baseline", "smd": value},)
    with pytest.raises(ValidationError, match="threshold"):
        model.model_validate(payload)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("model", "payload"),
    ((SyntheticControlResult, _scm()), (MetaAnalysisResult, _meta())),
)
def test_leave_one_out_threshold_is_enforced(
    model: type[object], payload: dict[str, object]
) -> None:
    payload = deepcopy(payload)
    records = list(payload["leave_one_out"])  # type: ignore[arg-type]
    records[0] = {"omitted": "a", "effect": 1.6, "absolute_change": 0.6}
    payload["leave_one_out"] = tuple(records)
    payload["max_leave_one_out_change"] = 0.6
    with pytest.raises(ValidationError, match="threshold"):
        model.model_validate(payload)  # type: ignore[attr-defined]


def test_measurement_exceedances_cannot_exceed_valid_support() -> None:
    payload = _measurement()
    payload["exceedances"] = 9
    with pytest.raises(ValidationError, match="valid"):
        EnvironmentalMeasurementResult.model_validate(payload)
