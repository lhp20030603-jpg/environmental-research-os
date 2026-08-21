from __future__ import annotations

from pathlib import Path

import pytest
from test_econometrics_valuation_contracts import hedonic

from envresearch.econometrics.data_snapshot import LocalDataSnapshot, MissingValueCount
from envresearch.econometrics.hedonic import HedonicRecipe
from envresearch.econometrics.r_runtime import FORBIDDEN_R
from envresearch.econometrics.recipes import recipe_for
from envresearch.econometrics.valuation_results import HedonicResult
from envresearch.models.artifact import ArtifactRef


def _snapshot(spec) -> LocalDataSnapshot:
    digest = "d" * 64
    return LocalDataSnapshot(
        reference=ArtifactRef(
            artifact_id="local-data-dddddddddddddddd",
            artifact_version=1,
            content_hash=digest,
        ),
        relative_path=Path("artifacts/econometrics/data") / f"{digest}.csv",
        sha256=digest,
        size_bytes=100,
        row_count=20,
        columns=spec.required_columns(),
        missing_values=tuple(
            MissingValueCount(column=item, count=0) for item in spec.required_columns()
        ),
    )


def test_hedonic_recipe_has_exact_output_contract(tmp_path: Path) -> None:
    recipe = HedonicRecipe(tmp_path / "work")
    assert recipe.expected_outputs == frozenset(
        {
            "coefficients.csv",
            "covariance.csv",
            "implicit_price.csv",
            "support.csv",
            "collinearity.csv",
            "sensitivity.csv",
            "package_configuration.csv",
            "hedonic_plot.svg",
        }
    )
    assert isinstance(
        recipe_for("hedonic-pricing", workspace=tmp_path / "registered"), HedonicRecipe
    )


def test_hedonic_script_binds_declared_design_without_source_path(
    tmp_path: Path,
) -> None:
    spec = hedonic(tmp_path / "source.csv")
    script = HedonicRecipe(tmp_path / "work").render(spec, _snapshot(spec))
    text = script.path.read_text(encoding="utf-8")
    assert "fixest::feols" in text
    assert 'loadNamespace("fixest", lib.loc = managed_library)' in text
    assert (
        'cat("ENVRESEARCH_CODE:HEDONIC_TERM_UNIDENTIFIED\\n", file = stderr())' in text
    )
    assert 'functional_form <- "log-level"' in text
    assert 'sensitivity_form <- "level-level"' in text
    assert str(spec.data_path) not in text
    assert 'class="x-tick"' in text
    assert FORBIDDEN_R.search(text) is None


def test_valuation_renderer_accepts_canonical_double_underscore_column(
    tmp_path: Path,
) -> None:
    original = hedonic(tmp_path / "source.csv")
    payload = original.model_dump()
    payload["columns"]["price"] = "price__usd"  # type: ignore[index]
    spec = type(original).model_validate(payload)

    script = HedonicRecipe(tmp_path / "work").render(spec, _snapshot(spec))

    assert 'price_column <- "price__usd"' in script.path.read_text(encoding="utf-8")


def test_hedonic_parser_returns_typed_result(tmp_path: Path) -> None:
    _write_hedonic_outputs(tmp_path)
    authority = ArtifactRef(
        artifact_id="r-package-fixest",
        artifact_version=1,
        content_hash="a" * 64,
    )
    result = HedonicRecipe(tmp_path / "work").parse(tmp_path, (authority,))
    assert isinstance(result, HedonicResult)
    assert result.welfare[0].estimate == -50.0
    assert result.configuration.package_authorities == (authority,)


def test_hedonic_parser_rejects_missing_or_forged_output(tmp_path: Path) -> None:
    _write_hedonic_outputs(tmp_path)
    (tmp_path / "covariance.csv").unlink()
    with pytest.raises(ValueError):
        HedonicRecipe(tmp_path / "work").parse(tmp_path)


def test_hedonic_parser_maps_unidentified_term_and_collinearity(tmp_path: Path) -> None:
    _write_hedonic_outputs(tmp_path)
    (tmp_path / "coefficients.csv").write_text(
        "term,estimate,std_error,confidence_low,confidence_high\nother,-0.5,0.1,-0.7,-0.3\n",
        encoding="utf-8",
    )
    (tmp_path / "covariance.csv").write_text(
        "row_term,column_term,value\nother,other,0.01\n", encoding="utf-8"
    )
    with pytest.raises(ValueError) as unidentified:
        HedonicRecipe(tmp_path / "work").parse(tmp_path)
    assert getattr(unidentified.value, "code", None) == "HEDONIC_TERM_UNIDENTIFIED"

    _write_hedonic_outputs(tmp_path)
    (tmp_path / "collinearity.csv").write_text(
        "condition_number,max_condition_number,max_vif,reference_price,reference_environment\n31,30,1.5,100,10\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as collinear:
        HedonicRecipe(tmp_path / "work").parse(tmp_path)
    assert getattr(collinear.value, "code", None) == "HEDONIC_COLLINEARITY_EXCEEDED"


def test_hedonic_rejects_forged_welfare_uncertainty(tmp_path: Path) -> None:
    _write_hedonic_outputs(tmp_path)
    welfare = tmp_path / "implicit_price.csv"
    welfare.write_text(
        welfare.read_text(encoding="utf-8").replace(
            "-50,10,-69.5996398454005,-30.4003601545995",
            "-50,999,-100000,100000",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="welfare uncertainty"):
        HedonicRecipe(tmp_path / "work").parse(tmp_path)


def _write_hedonic_outputs(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "coefficients.csv").write_text(
        "term,estimate,std_error,confidence_low,confidence_high\npm25,-0.5,0.1,-0.7,-0.3\n",
        encoding="utf-8",
    )
    (root / "covariance.csv").write_text(
        "row_term,column_term,value\npm25,pm25,0.01\n", encoding="utf-8"
    )
    (root / "implicit_price.csv").write_text(
        "name,estimate,std_error,confidence_low,confidence_high,currency,price_base,time_basis,population_basis,transformation,numerator_term,denominator_term\n"
        "implicit-price,-50,10,-69.5996398454005,-30.4003601545995,USD,2025,per-year,sample-household,marginal-implicit-price,pm25,price\n",
        encoding="utf-8",
    )
    (root / "support.csv").write_text(
        "observations,primary_units,groups,zero_or_no_count\n20,20,4,0\n",
        encoding="utf-8",
    )
    (root / "collinearity.csv").write_text(
        "condition_number,max_condition_number,max_vif,reference_price,reference_environment\n2,30,1.5,100,10\n",
        encoding="utf-8",
    )
    (root / "sensitivity.csv").write_text(
        "label,estimate,baseline_estimate,absolute_change,max_sensitivity_change,raw_coefficient,model_form\n"
        "alternative-functional-form,-49.9,-50,0.1,0.25,-49.9,level-level\n",
        encoding="utf-8",
    )
    (root / "package_configuration.csv").write_text(
        "method_id,r_version,confidence_level,cluster_column,fixed_effects,functional_form,family,link\n"
        "hedonic-pricing,R version 4.4.3,0.95,district,district;year,log-level,,\n",
        encoding="utf-8",
    )
    (root / "hedonic_plot.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><g class="x-tick"><text>0</text></g></svg>\n',
        encoding="utf-8",
    )
