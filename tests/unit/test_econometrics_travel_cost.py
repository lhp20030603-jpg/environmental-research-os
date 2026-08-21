from __future__ import annotations

from pathlib import Path

import pytest
from test_econometrics_hedonic import _snapshot
from test_econometrics_valuation_contracts import travel_cost

from envresearch.econometrics.r_runtime import FORBIDDEN_R
from envresearch.econometrics.recipes import recipe_for
from envresearch.econometrics.travel_cost import TravelCostRecipe
from envresearch.econometrics.valuation_results import TravelCostResult
from envresearch.models.artifact import ArtifactRef


def test_travel_cost_recipe_has_exact_output_contract(tmp_path: Path) -> None:
    recipe = TravelCostRecipe(tmp_path / "work")
    assert recipe.expected_outputs == frozenset(
        {
            "coefficients.csv",
            "covariance.csv",
            "consumer_surplus.csv",
            "support.csv",
            "dispersion.csv",
            "fit_evidence.csv",
            "sensitivity.csv",
            "package_configuration.csv",
            "travel_cost_plot.svg",
        }
    )
    assert isinstance(
        recipe_for("travel-cost", workspace=tmp_path / "registered"), TravelCostRecipe
    )


def test_travel_cost_script_binds_family_offset_and_no_auto_switch(
    tmp_path: Path,
) -> None:
    spec = travel_cost(tmp_path / "source.csv")
    script = TravelCostRecipe(tmp_path / "work").render(spec, _snapshot(spec))
    text = script.path.read_text(encoding="utf-8")
    assert 'family_name <- "negative-binomial"' in text
    assert "MASS::glm.nb" in text
    assert (
        'required_package <- if (family_name == "poisson") "fixest" else "MASS"' in text
    )
    assert "loadNamespace(required_package, lib.loc = managed_library)" in text
    assert (
        'cat("ENVRESEARCH_CODE:TRAVEL_COST_SLOPE_INVALID\\n", file = stderr())' in text
    )
    assert "stats::offset(log(" in text
    assert 'site_effect <- sprintf("factor(%s)"' in text
    assert "AIC(" not in text
    assert str(spec.data_path) not in text
    assert FORBIDDEN_R.search(text) is None


def test_travel_cost_parser_returns_typed_result(tmp_path: Path) -> None:
    _write_outputs(tmp_path)
    authority = ArtifactRef(
        artifact_id="r-package-mass",
        artifact_version=1,
        content_hash="b" * 64,
    )
    result = TravelCostRecipe(tmp_path / "work").parse(tmp_path, (authority,))
    assert isinstance(result, TravelCostResult)
    assert result.welfare[0].estimate == 2.0
    assert result.configuration.package_authorities == (authority,)


def test_travel_cost_parser_maps_nonnegative_slope_to_typed_failure(
    tmp_path: Path,
) -> None:
    _write_outputs(tmp_path)
    coefficient = tmp_path / "coefficients.csv"
    coefficient.write_text(
        "term,estimate,std_error,confidence_low,confidence_high\n"
        "travel_cost,0.5,0.1,0.3,0.7\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as caught:
        TravelCostRecipe(tmp_path / "work").parse(tmp_path)
    assert getattr(caught.value, "code", None) == "TRAVEL_COST_SLOPE_INVALID"


def test_travel_cost_parser_maps_poisson_dispersion_failure(tmp_path: Path) -> None:
    _write_outputs(tmp_path)
    (tmp_path / "dispersion.csv").write_text(
        "dispersion,max_dispersion,log_likelihood,deviance,residual_df,theta\n"
        "3,2,-30,20,15,\n",
        encoding="utf-8",
    )
    configuration = tmp_path / "package_configuration.csv"
    configuration.write_text(
        configuration.read_text(encoding="utf-8").replace(
            "negative-binomial", "poisson"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as caught:
        TravelCostRecipe(tmp_path / "work").parse(tmp_path)
    assert getattr(caught.value, "code", None) == "TRAVEL_COST_DISPERSION_EXCEEDED"


def test_travel_cost_rejects_forged_welfare_uncertainty(tmp_path: Path) -> None:
    _write_outputs(tmp_path)
    welfare = tmp_path / "consumer_surplus.csv"
    welfare.write_text(
        welfare.read_text(encoding="utf-8").replace(
            "2,0.4,1.21601440618398,2.78398559381602", "2,999,-100000,100000"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="welfare uncertainty"):
        TravelCostRecipe(tmp_path / "work").parse(tmp_path)


def _write_outputs(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "coefficients.csv").write_text(
        "term,estimate,std_error,confidence_low,confidence_high\ntravel_cost,-0.5,0.1,-0.7,-0.3\n",
        encoding="utf-8",
    )
    (root / "covariance.csv").write_text(
        "row_term,column_term,value\ntravel_cost,travel_cost,0.01\n",
        encoding="utf-8",
    )
    (root / "consumer_surplus.csv").write_text(
        "name,estimate,std_error,confidence_low,confidence_high,currency,price_base,time_basis,population_basis,transformation,numerator_term,denominator_term\n"
        "consumer-surplus,2,0.4,1.21601440618398,2.78398559381602,USD,2025,per-year,sample-household,negative-inverse-cost,,travel_cost\n",
        encoding="utf-8",
    )
    (root / "support.csv").write_text(
        "observations,primary_units,groups,zero_or_no_count\n20,20,1,4\n",
        encoding="utf-8",
    )
    (root / "dispersion.csv").write_text(
        "dispersion,max_dispersion,log_likelihood,deviance,residual_df,theta\n"
        "1.2,2,-30,20,15,10\n",
        encoding="utf-8",
    )
    (root / "fit_evidence.csv").write_text(
        "row_index,observed,fitted\n1,1,1\n", encoding="utf-8"
    )
    (root / "sensitivity.csv").write_text(
        "label,estimate,baseline_estimate,absolute_change,max_sensitivity_change,raw_coefficient,model_form\n"
        "exclude-substitute-controls,2.1,2,0.1,0.25,-0.4761904761904762,negative-binomial\n",
        encoding="utf-8",
    )
    (root / "package_configuration.csv").write_text(
        "method_id,r_version,confidence_level,cluster_column,fixed_effects,functional_form,family,link\n"
        "travel-cost,R version 4.4.3,0.95,,site_id, ,negative-binomial,\n".replace(
            ", ,", ",,"
        ),
        encoding="utf-8",
    )
    (root / "travel_cost_plot.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><g class="x-tick"><text>0</text></g></svg>\n',
        encoding="utf-8",
    )
