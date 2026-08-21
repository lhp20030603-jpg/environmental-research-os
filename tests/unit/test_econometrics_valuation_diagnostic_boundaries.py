"""Independent diagnostic reconstruction boundary coverage."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_econometrics_valuation_contracts import hedonic, travel_cost

from envresearch.econometrics._valuation_diagnostics import (
    _condition_number,
    _count_diagnostics,
    _inverse,
    _max_vif,
    hedonic_diagnostics_match,
    travel_diagnostics_match,
)
from envresearch.econometrics.valuation_results import HedonicResult, TravelCostResult


def test_diagnostic_linear_algebra_rejects_singular_design() -> None:
    with pytest.raises(ValueError, match="singular valuation design"):
        _condition_number(((1.0, 1.0), (2.0, 2.0)))
    with pytest.raises(ValueError, match="singular valuation correlation"):
        _inverse([[1.0, 1.0], [1.0, 1.0]])
    assert _max_vif(((1.0,), (2.0,))) == 1.0


def test_poisson_count_diagnostics_reconstruct_finite_evidence() -> None:
    dispersion, log_likelihood, deviance = _count_diagnostics(
        (0.0, 2.0), (0.5, 1.5), residual_df=1, theta=None
    )
    assert dispersion > 0
    assert log_likelihood < 0
    assert deviance > 0


def test_hedonic_diagnostics_fail_closed_on_malformed_snapshot(
    tmp_path: Path,
) -> None:
    result = HedonicResult.model_construct(
        sensitivities=(),
        sensitivity_form="level-level",
        reference_price=1.0,
        reference_environment=1.0,
    )
    assert not hedonic_diagnostics_match(
        b"not,expected\n1,2\n", hedonic(tmp_path / "hedonic.csv"), result
    )


def test_travel_diagnostics_reject_row_count_index_and_fitted_value(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    spec = travel_cost(tmp_path / "travel.csv")
    snapshot = (
        b"person_id,visits,travel_cost,exposure,site_id,substitute_cost\n"
        b"p1,1,10,1,park,5\n"
    )
    result = TravelCostResult.model_construct(
        residual_df=1,
        theta=None,
        sensitivities=(),
    )
    (output / "fit_evidence.csv").write_text(
        "row_index,observed,fitted\n", encoding="utf-8"
    )
    assert not travel_diagnostics_match(snapshot, output, spec, result)

    (output / "fit_evidence.csv").write_text(
        "row_index,observed,fitted\n2,1,1\n", encoding="utf-8"
    )
    assert not travel_diagnostics_match(snapshot, output, spec, result)

    (output / "fit_evidence.csv").write_text(
        "row_index,observed,fitted\n1,1,0\n", encoding="utf-8"
    )
    assert not travel_diagnostics_match(snapshot, output, spec, result)
