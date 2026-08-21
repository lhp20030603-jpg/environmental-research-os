"""RCT recipe rendering and strict output-policy tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from envresearch.econometrics._wave1_support import wave_result_matches_snapshot
from envresearch.econometrics.analysis_specs import ANALYSIS_SPEC_ADAPTER, AnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataSnapshot, MissingValueCount
from envresearch.econometrics.r_runtime import FORBIDDEN_R
from envresearch.econometrics.rct import RctRecipe
from envresearch.econometrics.wave1_results import RctResult
from envresearch.models.artifact import ArtifactRef


def _spec(path: Path) -> AnalysisSpec:
    return ANALYSIS_SPEC_ADAPTER.validate_json(
        json.dumps(
            {
                "schema_version": "econometrics.rct-itt.v1",
                "method_id": "rct-itt",
                "data_path": str(path),
                "columns": {
                    "unit": "unit",
                    "assignment": "assigned",
                    "outcome": "outcome",
                    "baseline_covariates": ["baseline"],
                },
                "inference": {"confidence_level": 0.95, "cluster_column": None},
                "max_attrition_rate": 0.25,
                "balance_smd_threshold": 0.25,
                "budget": {
                    "inactivity_seconds": 60,
                    "max_output_bytes": 1_000_000,
                    "max_workspace_bytes": 10_000_000,
                },
            }
        )
    )


def _snapshot() -> LocalDataSnapshot:
    columns = ("unit", "assigned", "outcome", "baseline")
    return LocalDataSnapshot(
        reference=ArtifactRef(
            artifact_id="local-data-rct", artifact_version=1, content_hash="a" * 64
        ),
        relative_path=Path("artifacts/econometrics/data/rct.csv"),
        sha256="a" * 64,
        size_bytes=100,
        row_count=20,
        columns=columns,
        missing_values=tuple(
            MissingValueCount(column=name, count=0) for name in columns
        ),
    )


def _authority() -> ArtifactRef:
    return ArtifactRef(
        artifact_id="r-package-authority-fixest-0.14.0",
        artifact_version=1,
        content_hash="b" * 64,
    )


def test_rct_script_is_registered_owned_and_offline(tmp_path: Path) -> None:
    script = RctRecipe(tmp_path / "work").render(
        _spec(tmp_path / "rct.csv"), _snapshot()
    )
    text = script.path.read_text(encoding="utf-8")
    assert script.template_id == "rct-itt-v1"
    assert "fixest::feols" in text
    assert '"allocation.csv"' in text
    assert '"attrition.csv"' in text
    assert '"balance.csv"' in text
    assert 'class="x-tick"' in text
    assert FORBIDDEN_R.search(text) is None
    assert str(tmp_path) not in text


def _write_outputs(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    coefficient = "term,estimate,std_error,conf_low,conf_high\nassigned,2,0.5,1,3\n"
    (root / "unadjusted.csv").write_text(coefficient, encoding="utf-8")
    (root / "ancova.csv").write_text(coefficient, encoding="utf-8")
    (root / "allocation.csv").write_text(
        "arm,assigned,outcomes_observed,outcomes_missing\ncontrol,6,6,0\ntreated,6,5,1\n",
        encoding="utf-8",
    )
    (root / "attrition.csv").write_text(
        "attrition_rate,max_attrition_rate\n0.0833333333333333,0.25\n",
        encoding="utf-8",
    )
    (root / "balance.csv").write_text("term,smd\nbaseline,0\n", encoding="utf-8")
    (root / "package_configuration.csv").write_text(
        "method_id,r_version,confidence_level,balance_smd_threshold\n"
        "rct-itt,R version 4.4.3,0.95,0.25\n",
        encoding="utf-8",
    )
    (root / "coefficient_plot.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><g class="x-tick"/></svg>\n',
        encoding="utf-8",
    )


def test_rct_parser_builds_support_derived_result(tmp_path: Path) -> None:
    _write_outputs(tmp_path)
    result = RctRecipe(tmp_path / "work").parse(tmp_path, (_authority(),))
    assert isinstance(result, RctResult)
    assert result.support.control_outcomes_observed == 6
    assert result.attrition_rate == pytest.approx(1 / 12)


def test_rct_support_is_recomputed_from_owned_fixture(tmp_path: Path) -> None:
    _write_outputs(tmp_path)
    result = RctRecipe(tmp_path / "work").parse(tmp_path, (_authority(),))
    source = Path(__file__).parents[1] / "fixtures/econometrics/rct_itt.csv"
    spec = _spec(source)
    assert wave_result_matches_snapshot(source.read_bytes(), spec, result)  # type: ignore[arg-type]
    forged = result.model_copy(
        update={"support": result.support.model_copy(update={"total": 13})}
    )
    assert not wave_result_matches_snapshot(source.read_bytes(), spec, forged)  # type: ignore[arg-type]


def test_rct_parser_rejects_all_missing_arm_and_attrition_forgery(
    tmp_path: Path,
) -> None:
    _write_outputs(tmp_path)
    allocation = tmp_path / "allocation.csv"
    allocation.write_text(
        "arm,assigned,outcomes_observed,outcomes_missing\ncontrol,6,0,6\ntreated,6,5,1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        RctRecipe(tmp_path / "work").parse(tmp_path, (_authority(),))
