from __future__ import annotations

from pathlib import Path

import pytest
from test_econometrics_hedonic import _snapshot
from test_econometrics_valuation_contracts import dce

from envresearch.econometrics.discrete_choice import DiscreteChoiceRecipe
from envresearch.econometrics.r_runtime import FORBIDDEN_R
from envresearch.econometrics.recipes import recipe_for
from envresearch.econometrics.valuation_results import DiscreteChoiceResult


def test_dce_recipe_has_exact_output_contract(tmp_path: Path) -> None:
    recipe = DiscreteChoiceRecipe(tmp_path / "work")
    assert recipe.expected_outputs == frozenset(
        {
            "coefficients.csv",
            "covariance.csv",
            "wtp.csv",
            "choice_support.csv",
            "sensitivity.csv",
            "package_configuration.csv",
            "dce_plot.svg",
        }
    )
    assert isinstance(
        recipe_for("dce-clogit", workspace=tmp_path / "registered"),
        DiscreteChoiceRecipe,
    )


def test_dce_script_binds_conditional_logit_and_cluster(tmp_path: Path) -> None:
    spec = dce(tmp_path / "source.csv")
    script = DiscreteChoiceRecipe(tmp_path / "work").render(spec, _snapshot(spec))
    text = script.path.read_text(encoding="utf-8")
    assert "survival::clogit" in text
    assert "strata(" in text
    assert "cluster(" in text
    assert 'loadNamespace("survival", lib.loc = managed_library)' in text
    assert 'emit_failure("DCE_COST_SLOPE_INVALID", 52)' in text
    assert str(spec.data_path) not in text
    assert FORBIDDEN_R.search(text) is None


def test_dce_parser_returns_typed_result_and_rejects_near_zero_cost(
    tmp_path: Path,
) -> None:
    _write_outputs(tmp_path)
    result = DiscreteChoiceRecipe(tmp_path / "work").parse(tmp_path)
    assert isinstance(result, DiscreteChoiceResult)
    assert tuple(item.estimate for item in result.welfare) == (2.0, 1.0)

    coefficient = tmp_path / "coefficients.csv"
    coefficient.write_text(
        coefficient.read_text(encoding="utf-8").replace(
            "cost,-0.5,0.1,-0.7,-0.3",
            "cost,-0.0001,0.1,-0.2,0.2",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as caught:
        DiscreteChoiceRecipe(tmp_path / "work").parse(tmp_path)
    assert getattr(caught.value, "code", None) == "DCE_COST_SLOPE_INVALID"


def test_dce_parser_reconstructs_attribute_wtp_uncertainty(tmp_path: Path) -> None:
    _write_outputs(tmp_path)
    welfare = tmp_path / "wtp.csv"
    welfare.write_text(
        welfare.read_text(encoding="utf-8").replace("0.565685424949238", "999"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as caught:
        DiscreteChoiceRecipe(tmp_path / "work").parse(tmp_path)

    assert getattr(caught.value, "code", None) == "DCE_TERM_UNIDENTIFIED"


def test_dce_parser_reconstructs_sensitivity_wtp(tmp_path: Path) -> None:
    _write_outputs(tmp_path)
    sensitivity = tmp_path / "sensitivity.csv"
    sensitivity.write_text(
        sensitivity.read_text(encoding="utf-8").replace("1.05,-0.5", "99,-0.5"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as caught:
        DiscreteChoiceRecipe(tmp_path / "work").parse(tmp_path)

    assert getattr(caught.value, "code", None) == "OUTPUT_INVALID"


def _write_outputs(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    terms = ("cost", "air_quality", "green_space")
    (root / "coefficients.csv").write_text(
        "term,estimate,std_error,confidence_low,confidence_high\n"
        "cost,-0.5,0.1,-0.7,-0.3\nair_quality,1,0.2,0.6,1.4\ngreen_space,0.5,0.1,0.3,0.7\n",
        encoding="utf-8",
    )
    (root / "covariance.csv").write_text(
        "row_term,column_term,value\n"
        + "".join(
            f"{left},{right},{0.01 if left == right == 'cost' else 0.04 if left == right == 'air_quality' else 0.01 if left == right else 0}\n"
            for left in terms
            for right in terms
        ),
        encoding="utf-8",
    )
    (root / "wtp.csv").write_text(
        "name,estimate,std_error,confidence_low,confidence_high,currency,price_base,time_basis,population_basis,transformation,numerator_term,denominator_term\n"
        "air_quality-wtp,2,0.565685424949238,0.8912769405202581,3.108723059479742,USD,2025,per-year,sample-household,negative-attribute-over-cost,air_quality,cost\n"
        "green_space-wtp,1,0.282842712474619,0.44563847026012904,1.554361529739871,USD,2025,per-year,sample-household,negative-attribute-over-cost,green_space,cost\n",
        encoding="utf-8",
    )
    (root / "choice_support.csv").write_text(
        "observations,primary_units,groups,zero_or_no_count,min_abs_cost_coefficient\n"
        "40,10,20,20,0.001\n",
        encoding="utf-8",
    )
    (root / "sensitivity.csv").write_text(
        "label,estimate,baseline_estimate,absolute_change,max_sensitivity_change,numerator_coefficient,denominator_coefficient,model_form\n"
        "include-alternative-specific-constants,2.1,2,0.1,0.25,1.05,-0.5,conditional-logit\n",
        encoding="utf-8",
    )
    (root / "package_configuration.csv").write_text(
        "method_id,r_version,confidence_level,cluster_column,fixed_effects,functional_form,family,link\n"
        "dce-clogit,R version 4.4.3,0.95,respondent_id,,,,\n",
        encoding="utf-8",
    )
    (root / "dce_plot.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><g class="x-tick"><text>0</text></g></svg>\n',
        encoding="utf-8",
    )
