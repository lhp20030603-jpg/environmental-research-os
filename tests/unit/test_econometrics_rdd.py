"""Sharp local-linear RDD rendering and output-policy tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from envresearch.econometrics.analysis_specs import (
    ANALYSIS_SPEC_ADAPTER,
    AnalysisSpec,
)
from envresearch.econometrics.causal_models import RddResult
from envresearch.econometrics.data_snapshot import (
    LocalDataSnapshot,
    MissingValueCount,
    snapshot_csv,
)
from envresearch.econometrics.r_runtime import FORBIDDEN_R
from envresearch.econometrics.rdd import RddRecipe
from envresearch.econometrics.recipes import recipe_for
from envresearch.models.artifact import ArtifactRef
from envresearch.storage.research_artifacts import ResearchArtifactStore

FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "econometrics" / "rdd_local_linear.csv"
)


def _spec(path: Path) -> AnalysisSpec:
    return ANALYSIS_SPEC_ADAPTER.validate_json(
        json.dumps(
            {
                "schema_version": "econometrics.rdd-local-linear.v1",
                "method_id": "rdd-local-linear",
                "data_path": str(path),
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
                "inference": {"confidence_level": 0.95, "cluster_column": None},
                "budget": {
                    "inactivity_seconds": 60,
                    "max_output_bytes": 1_000_000,
                    "max_workspace_bytes": 10_000_000,
                },
            }
        )
    )


def _snapshot() -> LocalDataSnapshot:
    digest = "e" * 64
    columns = ("emissions", "score", "income")
    return LocalDataSnapshot(
        reference=ArtifactRef(
            artifact_id="local-data-eeeeeeeeeeeeeeee",
            artifact_version=1,
            content_hash=digest,
        ),
        relative_path=Path("artifacts/econometrics/data") / f"{digest}.csv",
        sha256=digest,
        size_bytes=200,
        row_count=21,
        columns=columns,
        missing_values=tuple(
            MissingValueCount(column=column, count=0) for column in columns
        ),
    )


def test_rdd_script_derives_sharp_treatment_and_triangular_weights(
    tmp_path: Path,
) -> None:
    script = RddRecipe(tmp_path / "work").render(
        _spec(tmp_path / "rdd.csv"), _snapshot()
    )
    text = script.path.read_text(encoding="utf-8")

    assert "treatment_internal <- running_internal >= cutoff" in text
    assert "1 - abs(centered_internal) / current_bandwidth" in text
    assert "rdrobust" not in text
    assert FORBIDDEN_R.search(text) is None


def _write_coefficient(path: Path, term: str, estimate: float = 1.0) -> None:
    path.write_text(
        "term,estimate,std_error,conf_low,conf_high\n"
        f"{term},{estimate},0.2,{estimate - 0.4},{estimate + 0.4}\n",
        encoding="utf-8",
    )


def _write_outputs(root: Path, multipliers: tuple[float, ...]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _write_coefficient(root / "main.csv", "cutoff")
    rows = ["multiplier,term,estimate,std_error,conf_low,conf_high"]
    rows.extend(f"{item},cutoff,1,0.2,0.6,1.4" for item in multipliers)
    (root / "bandwidth_sensitivity.csv").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )
    _write_coefficient(root / "donut.csv", "cutoff")
    _write_coefficient(root / "covariate_continuity.csv", "income", 0.1)
    (root / "support.csv").write_text(
        "observations,left_observations,right_observations,left_unique_running,"
        "right_unique_running,donut_left_observations,donut_right_observations\n"
        "80,40,40,8,8,35,35\n",
        encoding="utf-8",
    )
    (root / "package_configuration.csv").write_text(
        "method_id,r_version,fixest_version,confidence_level,cluster_column,"
        "fixed_effects,estimator_label,cutoff,bandwidth,kernel,donut_radius\n"
        "rdd-local-linear,R version 4.4.3,0.14.0,0.95,,,sharp-local-linear,"
        "0,4,triangular,0.25\n",
        encoding="utf-8",
    )
    (root / "rdd_plot.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text>cutoff</text></svg>\n',
        encoding="utf-8",
    )


def test_rdd_parser_requires_exact_bandwidth_multipliers(tmp_path: Path) -> None:
    _write_outputs(tmp_path, (0.5, 1.0))

    with pytest.raises(ValueError, match="bandwidth"):
        RddRecipe(tmp_path / "work").parse(tmp_path)


def test_rdd_parser_rejects_one_sided_donut_support(tmp_path: Path) -> None:
    _write_outputs(tmp_path, (0.5, 1.0, 1.5))
    support = tmp_path / "support.csv"
    support.write_text(support.read_text().replace("35,35", "0,35"), encoding="utf-8")

    with pytest.raises(ValueError, match="support"):
        RddRecipe(tmp_path / "work").parse(tmp_path)


def test_rdd_parser_returns_typed_complete_result(tmp_path: Path) -> None:
    _write_outputs(tmp_path, (0.5, 1.0, 1.5))

    result = RddRecipe(tmp_path / "work").parse(tmp_path)

    assert isinstance(result, RddResult)
    assert tuple(item.multiplier for item in result.bandwidth_sensitivity) == (
        0.5,
        1.0,
        1.5,
    )
    assert "rdrobust RBC not included" in result.inference_limitation


def test_rdd_is_available_through_shared_registry(tmp_path: Path) -> None:
    assert isinstance(
        recipe_for("rdd-local-linear", workspace=tmp_path / "rdd"), RddRecipe
    )


def test_checked_rdd_fixture_snapshots_and_renders(tmp_path: Path) -> None:
    spec = _spec(FIXTURE)
    snapshot = snapshot_csv(spec, ResearchArtifactStore(tmp_path / "store"))
    script = RddRecipe(tmp_path / "work").render(spec, snapshot)

    assert snapshot.row_count == 21
    assert script.template_id == "rdd-local-linear-v1"
    assert str(FIXTURE) not in script.path.read_text(encoding="utf-8")
