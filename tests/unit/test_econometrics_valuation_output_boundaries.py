"""Malformed sealed-output coverage for Valuation Core parsers."""

from __future__ import annotations

from pathlib import Path

import pytest

from envresearch.econometrics._causal_outputs import CausalOutputInvalid
from envresearch.econometrics._valuation_outputs import (
    coefficients,
    configuration,
    covariance,
    sensitivities,
    support,
    welfare,
)


def _csv(tmp_path: Path, name: str, header: str, *rows: str) -> Path:
    path = tmp_path / name
    path.write_text("\n".join((header, *rows, "")), encoding="utf-8")
    return path


def test_coefficients_reject_invalid_numeric_and_duplicate_term(tmp_path: Path) -> None:
    header = "term,estimate,std_error,confidence_low,confidence_high"
    invalid = _csv(tmp_path, "invalid.csv", header, "cost,nope,0.1,-1,0")
    with pytest.raises(CausalOutputInvalid, match="coefficients are invalid"):
        coefficients(invalid)

    duplicate = _csv(
        tmp_path,
        "duplicate.csv",
        header,
        "cost,-0.5,0.1,-0.7,-0.3",
        "cost,-0.4,0.1,-0.6,-0.2",
    )
    with pytest.raises(CausalOutputInvalid, match="terms must be unique"):
        coefficients(duplicate)


def test_covariance_requires_unique_complete_finite_cells(tmp_path: Path) -> None:
    header = "row_term,column_term,value"
    duplicate = _csv(
        tmp_path,
        "duplicate-cov.csv",
        header,
        "cost,cost,0.01",
        "cost,cost,0.01",
    )
    with pytest.raises(CausalOutputInvalid, match="covariance is invalid"):
        covariance(duplicate, ("cost",))

    incomplete = _csv(
        tmp_path,
        "incomplete.csv",
        header,
        "cost,cost,0.01",
    )
    with pytest.raises(CausalOutputInvalid, match="covariance is invalid"):
        covariance(incomplete, ("cost", "air"))

    invalid = _csv(tmp_path, "invalid-cov.csv", header, "cost,cost,nope")
    with pytest.raises(CausalOutputInvalid, match="covariance is invalid"):
        covariance(invalid, ("cost",))


def test_welfare_rejects_invalid_typed_row(tmp_path: Path) -> None:
    header = (
        "name,estimate,std_error,confidence_low,confidence_high,currency,"
        "price_base,time_basis,population_basis,transformation,numerator_term,"
        "denominator_term"
    )
    path = _csv(
        tmp_path,
        "welfare.csv",
        header,
        "wtp,nope,1,0,2,USD,2025,annual,sample,negative-inverse-cost,,cost",
    )
    with pytest.raises(CausalOutputInvalid, match="welfare output is invalid"):
        welfare(path)


def test_support_requires_one_valid_row(tmp_path: Path) -> None:
    header = "observations,primary_units,groups,zero_or_no_count"
    multiple = _csv(
        tmp_path,
        "multiple.csv",
        header,
        "10,10,1,2",
        "10,10,1,2",
    )
    with pytest.raises(CausalOutputInvalid, match="one row"):
        support(multiple)

    invalid = _csv(tmp_path, "invalid-support.csv", header, "nope,10,1,2")
    with pytest.raises(CausalOutputInvalid, match="support is invalid"):
        support(invalid)


def test_sensitivity_requires_constant_threshold_and_model(tmp_path: Path) -> None:
    header = (
        "label,estimate,baseline_estimate,absolute_change,max_sensitivity_change,"
        "raw_coefficient,model_form"
    )
    thresholds = _csv(
        tmp_path,
        "thresholds.csv",
        header,
        "a,1.1,1,0.1,1,-0.5,poisson",
        "b,1.2,1,0.2,2,-0.5,poisson",
    )
    with pytest.raises(CausalOutputInvalid, match="sensitivity output is invalid"):
        sensitivities(thresholds)

    models = _csv(
        tmp_path,
        "models.csv",
        header,
        "a,1.1,1,0.1,1,-0.5,poisson",
        "b,1.2,1,0.2,1,-0.4,negative-binomial",
    )
    with pytest.raises(CausalOutputInvalid, match="sensitivity output is invalid"):
        sensitivities(models)

    invalid = _csv(
        tmp_path,
        "invalid-sensitivity.csv",
        header,
        "a,nope,1,0.1,1,-0.5,poisson",
    )
    with pytest.raises(CausalOutputInvalid, match="sensitivity output is invalid"):
        sensitivities(invalid)


def test_configuration_requires_one_typed_row(tmp_path: Path) -> None:
    header = (
        "method_id,r_version,confidence_level,cluster_column,fixed_effects,"
        "functional_form,family,link"
    )
    multiple = _csv(
        tmp_path,
        "multiple-configuration.csv",
        header,
        "travel-cost,R 4.4.3,0.95,,site,,poisson,",
        "travel-cost,R 4.4.3,0.95,,site,,poisson,",
    )
    with pytest.raises(CausalOutputInvalid, match="one row"):
        configuration(multiple)

    invalid = _csv(
        tmp_path,
        "invalid-configuration.csv",
        header,
        "travel-cost,R 4.4.3,nope,,site,,poisson,",
    )
    with pytest.raises(CausalOutputInvalid, match="configuration is invalid"):
        configuration(invalid)
