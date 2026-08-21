"""Panel FE and IV/2SLS recipe rendering and output-policy tests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from envresearch.econometrics.analysis_specs import (
    ANALYSIS_SPEC_ADAPTER,
    AnalysisSpec,
)
from envresearch.econometrics.causal_models import Iv2slsResult, PanelFeResult
from envresearch.econometrics.data_snapshot import (
    LocalDataSnapshot,
    MissingValueCount,
    snapshot_csv,
)
from envresearch.econometrics.iv_2sls import Iv2slsRecipe
from envresearch.econometrics.panel_fe import PanelFeRecipe
from envresearch.econometrics.r_runtime import FORBIDDEN_R
from envresearch.econometrics.recipes import recipe_for
from envresearch.models.artifact import ArtifactRef
from envresearch.storage.research_artifacts import ResearchArtifactStore

FIXTURES = Path(__file__).parents[1] / "fixtures" / "econometrics"


def _common(path: Path) -> dict[str, object]:
    return {
        "data_path": str(path),
        "inference": {"confidence_level": 0.95, "cluster_column": "unit"},
        "budget": {
            "inactivity_seconds": 60,
            "max_output_bytes": 1_000_000,
            "max_workspace_bytes": 10_000_000,
        },
    }


def _panel_spec(path: Path) -> AnalysisSpec:
    return ANALYSIS_SPEC_ADAPTER.validate_json(
        json.dumps(
            {
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
        )
    )


def _iv_spec(path: Path) -> AnalysisSpec:
    return ANALYSIS_SPEC_ADAPTER.validate_json(
        json.dumps(
            {
                **_common(path),
                "schema_version": "econometrics.iv-2sls.v1",
                "method_id": "iv-2sls",
                "columns": {
                    "outcome": "emissions",
                    "endogenous": ["price"],
                    "instruments": ["wind"],
                    "controls": ["income"],
                    "fixed_effects": ["unit", "year"],
                },
                "weak_instrument_f_threshold": 10.0,
            }
        )
    )


def _snapshot(columns: tuple[str, ...]) -> LocalDataSnapshot:
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
        columns=columns,
        missing_values=tuple(
            MissingValueCount(column=column, count=0) for column in columns
        ),
    )


def test_panel_script_contains_declared_fe_and_cluster_only(tmp_path: Path) -> None:
    spec = _panel_spec(tmp_path / "panel.csv")
    script = PanelFeRecipe(tmp_path / "work").render(
        spec, _snapshot(spec.required_columns())
    )
    text = script.path.read_text(encoding="utf-8")

    assert "fixest::feols" in text
    assert 'fixed_effect_columns <- c("unit", "year")' in text
    assert 'cluster_column <- "unit"' in text
    assert str(spec.data_path) not in text
    assert FORBIDDEN_R.search(text) is None
    assert "xml_escape(coefficients$term[index])" in text
    assert 'class="x-tick"' in text
    assert 'class="confidence-interval"' in text
    assert "map_x(coefficients$estimate[index])" in text
    assert "plot_height <- max(300, 150 + nrow(coefficients) * 34)" in text


def test_iv_script_emits_all_mandatory_outputs(tmp_path: Path) -> None:
    spec = _iv_spec(tmp_path / "iv.csv")
    script = Iv2slsRecipe(tmp_path / "work").render(
        spec, _snapshot(spec.required_columns())
    )
    text = script.path.read_text(encoding="utf-8")

    assert '"structural.csv"' in text
    assert '"first_stage.csv"' in text
    assert '"reduced_form.csv"' in text
    assert "covariance[instrument_columns, instrument_columns" in text
    assert "fixest::wald" not in text
    assert '"overidentification.csv"' in text
    assert "xml_escape(structural$term[index])" in text
    assert 'class="x-tick"' in text
    assert 'class="confidence-interval"' in text
    assert "map_x(structural$estimate[index])" in text
    assert "plot_height <- max(300, 150 + nrow(structural) * 34)" in text
    assert FORBIDDEN_R.search(text) is None


def test_panel_and_iv_are_available_through_the_shared_registry(tmp_path: Path) -> None:
    assert isinstance(
        recipe_for("panel-fe", workspace=tmp_path / "panel"), PanelFeRecipe
    )
    assert isinstance(recipe_for("iv-2sls", workspace=tmp_path / "iv"), Iv2slsRecipe)


@pytest.mark.parametrize(
    ("spec_factory", "fixture_name", "expected_rows"),
    [
        (_panel_spec, "panel_fe.csv", 12),
        (_iv_spec, "iv_2sls.csv", 32),
    ],
)
def test_checked_local_fixtures_snapshot_and_render(
    tmp_path: Path,
    spec_factory: Callable[[Path], AnalysisSpec],
    fixture_name: str,
    expected_rows: int,
) -> None:
    spec = spec_factory(FIXTURES / fixture_name)
    snapshot = snapshot_csv(spec, ResearchArtifactStore(tmp_path / "store"))
    recipe = recipe_for(spec.method_id, workspace=tmp_path / "work")
    script = recipe.render(spec, snapshot)

    assert snapshot.row_count == expected_rows
    assert script.path.is_file()
    assert str(FIXTURES) not in script.path.read_text(encoding="utf-8")


def _write_coefficient(path: Path, term: str) -> None:
    path.write_text(
        f"term,estimate,std_error,conf_low,conf_high\n{term},1,0.2,0.6,1.4\n",
        encoding="utf-8",
    )


def _write_common(root: Path, method_id: str, *, panel: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)
    support = (
        "observations,clusters,units,time_periods\n100,20,20,5\n"
        if panel
        else "observations,clusters\n100,20\n"
    )
    (root / "support.csv").write_text(support, encoding="utf-8")
    (root / "package_configuration.csv").write_text(
        "method_id,r_version,fixest_version,confidence_level,cluster_column,"
        "fixed_effects,estimator_label,cutoff,bandwidth,kernel,donut_radius\n"
        f"{method_id},R version 4.4.3,0.14.0,0.95,unit,unit;year,"
        f"{'fixest::feols-panel-fe' if method_id == 'panel-fe' else 'fixest::feols-2sls'},,,,\n",
        encoding="utf-8",
    )
    (root / "coefficient_plot.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text>0</text></svg>\n',
        encoding="utf-8",
    )


def test_panel_parser_returns_typed_complete_result(tmp_path: Path) -> None:
    _write_common(tmp_path, "panel-fe", panel=True)
    _write_coefficient(tmp_path / "coefficients.csv", "policy")
    (tmp_path / "fit.csv").write_text(
        "r_squared,within_r_squared\n0.8,0.4\n", encoding="utf-8"
    )

    result = PanelFeRecipe(tmp_path / "work").parse(tmp_path)

    assert isinstance(result, PanelFeResult)
    assert result.coefficients[0].term == "policy"


def test_iv_parser_rejects_weak_first_stage(tmp_path: Path) -> None:
    _write_common(tmp_path, "iv-2sls")
    _write_coefficient(tmp_path / "structural.csv", "price")
    _write_coefficient(tmp_path / "reduced_form.csv", "wind")
    (tmp_path / "first_stage.csv").write_text(
        "endogenous,instruments,f_statistic,threshold\nprice,wind,7,10\n",
        encoding="utf-8",
    )
    (tmp_path / "overidentification.csv").write_text(
        "test,statistic,p_value,degrees_of_freedom\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="weak instrument"):
        Iv2slsRecipe(tmp_path / "work").parse(tmp_path)


def test_iv_parser_returns_typed_result_with_bound_figure(tmp_path: Path) -> None:
    _write_common(tmp_path, "iv-2sls")
    _write_coefficient(tmp_path / "structural.csv", "price")
    _write_coefficient(tmp_path / "reduced_form.csv", "wind")
    (tmp_path / "first_stage.csv").write_text(
        "endogenous,instruments,f_statistic,threshold\nprice,wind,20,10\n",
        encoding="utf-8",
    )
    (tmp_path / "overidentification.csv").write_text(
        "test,statistic,p_value,degrees_of_freedom\n", encoding="utf-8"
    )

    result = Iv2slsRecipe(tmp_path / "work").parse(tmp_path)

    assert isinstance(result, Iv2slsResult)
    assert result.first_stage[0].f_statistic == 20.0
    assert (
        result.figure_sha256
        == hashlib.sha256((tmp_path / "coefficient_plot.svg").read_bytes()).hexdigest()
    )


def test_iv_parser_requires_overidentification_evidence_when_available(
    tmp_path: Path,
) -> None:
    _write_common(tmp_path, "iv-2sls")
    _write_coefficient(tmp_path / "structural.csv", "price")
    (tmp_path / "reduced_form.csv").write_text(
        "term,estimate,std_error,conf_low,conf_high\n"
        "wind,1,0.2,0.6,1.4\nsolar,0.8,0.2,0.4,1.2\n",
        encoding="utf-8",
    )
    (tmp_path / "first_stage.csv").write_text(
        "endogenous,instruments,f_statistic,threshold\nprice,wind;solar,20,10\n",
        encoding="utf-8",
    )
    (tmp_path / "overidentification.csv").write_text(
        "test,statistic,p_value,degrees_of_freedom\nSargan,1.5,0.22,1\n",
        encoding="utf-8",
    )

    result = Iv2slsRecipe(tmp_path / "work").parse(tmp_path)

    assert result.overidentification is not None
    assert result.overidentification.test == "Sargan"
