from __future__ import annotations

from pathlib import Path

import pytest
from test_econometrics_hedonic import _snapshot
from test_econometrics_valuation_contracts import contingent_valuation

from envresearch.econometrics.contingent_valuation import ContingentValuationRecipe
from envresearch.econometrics.r_runtime import FORBIDDEN_R
from envresearch.econometrics.recipes import recipe_for
from envresearch.econometrics.valuation_results import ContingentValuationResult


def test_cv_recipe_has_exact_output_contract(tmp_path: Path) -> None:
    recipe = ContingentValuationRecipe(tmp_path / "work")
    assert recipe.expected_outputs == frozenset(
        {
            "coefficients.csv",
            "covariance.csv",
            "wtp.csv",
            "bid_support.csv",
            "bid_yes_shares.csv",
            "probabilities.csv",
            "sensitivity.csv",
            "package_configuration.csv",
            "cv_plot.svg",
        }
    )
    assert isinstance(
        recipe_for("contingent-valuation", workspace=tmp_path / "registered"),
        ContingentValuationRecipe,
    )


def test_cv_script_binds_registered_link_and_failure_protocol(tmp_path: Path) -> None:
    spec = contingent_valuation(tmp_path / "source.csv")
    script = ContingentValuationRecipe(tmp_path / "work").render(spec, _snapshot(spec))
    text = script.path.read_text(encoding="utf-8")
    assert 'link_name <- "logit"' in text
    assert "stats::glm" in text
    assert "automatic" not in text
    assert 'emit_failure("CV_BID_SLOPE_INVALID", 42)' in text
    assert str(spec.data_path) not in text
    assert FORBIDDEN_R.search(text) is None


def test_cv_parser_returns_typed_result_and_rejects_positive_bid(
    tmp_path: Path,
) -> None:
    _write_outputs(tmp_path)
    result = ContingentValuationRecipe(tmp_path / "work").parse(tmp_path)
    assert isinstance(result, ContingentValuationResult)
    assert result.welfare[0].estimate == 20.0

    coefficient = tmp_path / "coefficients.csv"
    coefficient.write_text(
        coefficient.read_text(encoding="utf-8").replace(
            "bid,-0.1,0.01,-0.12,-0.08", "bid,0.1,0.01,0.08,0.12"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as caught:
        ContingentValuationRecipe(tmp_path / "work").parse(tmp_path)
    assert getattr(caught.value, "code", None) == "CV_BID_SLOPE_INVALID"


def test_cv_parser_reconstructs_wtp_uncertainty(tmp_path: Path) -> None:
    _write_outputs(tmp_path)
    welfare = tmp_path / "wtp.csv"
    welfare.write_text(
        welfare.read_text(encoding="utf-8").replace("2.8284271247461903", "999"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as caught:
        ContingentValuationRecipe(tmp_path / "work").parse(tmp_path)

    assert getattr(caught.value, "code", None) == "CV_WTP_UNIDENTIFIED"


def test_cv_parser_reconstructs_sensitivity_wtp(tmp_path: Path) -> None:
    _write_outputs(tmp_path)
    sensitivity = tmp_path / "sensitivity.csv"
    sensitivity.write_text(
        sensitivity.read_text(encoding="utf-8").replace("2.01,-0.1", "99,-0.1"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as caught:
        ContingentValuationRecipe(tmp_path / "work").parse(tmp_path)

    assert getattr(caught.value, "code", None) == "OUTPUT_INVALID"


def _write_outputs(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "coefficients.csv").write_text(
        "term,estimate,std_error,confidence_low,confidence_high\n"
        "(Intercept),2,0.2,1.6,2.4\nbid,-0.1,0.01,-0.12,-0.08\nincome,0.01,0.002,0.006,0.014\n",
        encoding="utf-8",
    )
    terms = ("(Intercept)", "bid", "income")
    (root / "covariance.csv").write_text(
        "row_term,column_term,value\n"
        + "".join(
            f"{left},{right},{0.04 if left == right == '(Intercept)' else 0.0001 if left == right == 'bid' else 0.000004 if left == right else 0}\n"
            for left in terms
            for right in terms
        ),
        encoding="utf-8",
    )
    (root / "wtp.csv").write_text(
        "name,estimate,std_error,confidence_low,confidence_high,currency,price_base,time_basis,population_basis,transformation,numerator_term,denominator_term\n"
        "median-wtp,20,2.8284271247461903,14.45638470260129,25.54361529739871,USD,2025,per-year,sample-household,negative-intercept-over-bid,(Intercept),bid\n",
        encoding="utf-8",
    )
    (root / "bid_support.csv").write_text(
        "observations,primary_units,groups,zero_or_no_count\n20,20,4,10\n",
        encoding="utf-8",
    )
    (root / "bid_yes_shares.csv").write_text(
        "bid,yes_count,observations,yes_share\n"
        "10,4,5,0.8\n20,3,5,0.6\n30,2,5,0.4\n40,1,5,0.2\n",
        encoding="utf-8",
    )
    (root / "probabilities.csv").write_text(
        "minimum,maximum,extreme_share,max_extreme_share\n0.1,0.9,0.05,0.1\n",
        encoding="utf-8",
    )
    (root / "sensitivity.csv").write_text(
        "label,estimate,baseline_estimate,absolute_change,max_sensitivity_change,numerator_coefficient,denominator_coefficient,model_form\n"
        "exclude-covariates,20.1,20,0.1,0.25,2.01,-0.1,logit\n",
        encoding="utf-8",
    )
    (root / "package_configuration.csv").write_text(
        "method_id,r_version,confidence_level,cluster_column,fixed_effects,functional_form,family,link\n"
        "contingent-valuation,R version 4.4.3,0.95,,,,,logit\n",
        encoding="utf-8",
    )
    (root / "cv_plot.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><g class="x-tick"><text>0</text></g></svg>\n',
        encoding="utf-8",
    )
