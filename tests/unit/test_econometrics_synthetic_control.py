"""Synthetic-control recipe rendering and output policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from envresearch.econometrics._causal_outputs import CausalOutputInvalid
from envresearch.econometrics._wave1_support import wave_result_matches_snapshot
from envresearch.econometrics.analysis_specs import ANALYSIS_SPEC_ADAPTER, AnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataSnapshot, MissingValueCount
from envresearch.econometrics.synthetic_control import SyntheticControlRecipe
from envresearch.econometrics.wave1_results import SyntheticControlResult
from envresearch.models.artifact import ArtifactRef


def _spec(path: Path) -> AnalysisSpec:
    return ANALYSIS_SPEC_ADAPTER.validate_json(
        json.dumps(
            {
                "schema_version": "econometrics.synthetic-control.v1",
                "method_id": "synthetic-control",
                "data_path": str(path),
                "columns": {
                    "unit": "unit",
                    "time": "year",
                    "outcome": "emissions",
                    "predictors": [],
                },
                "treated_unit": "treated",
                "intervention_time": 2010,
                "max_pre_rmspe": 2.0,
                "max_leave_one_out_change": 2.0,
                "budget": {
                    "inactivity_seconds": 60,
                    "max_output_bytes": 1_000_000,
                    "max_workspace_bytes": 10_000_000,
                },
            }
        )
    )


def _snapshot() -> LocalDataSnapshot:
    columns = ("unit", "year", "emissions", "income")
    return LocalDataSnapshot(
        reference=ArtifactRef(
            artifact_id="local-data-scm", artifact_version=1, content_hash="a" * 64
        ),
        relative_path=Path("artifacts/econometrics/data/scm.csv"),
        sha256="a" * 64,
        size_bytes=100,
        row_count=12,
        columns=columns,
        missing_values=tuple(
            MissingValueCount(column=item, count=0) for item in columns
        ),
    )


def _authority() -> ArtifactRef:
    return ArtifactRef(
        artifact_id="r-package-authority-synthdid-0.0.9",
        artifact_version=1,
        content_hash="b" * 64,
    )


def test_scm_script_is_registered_owned_and_offline(tmp_path: Path) -> None:
    script = SyntheticControlRecipe(tmp_path / "work").render(
        _spec(tmp_path / "scm.csv"), _snapshot()
    )
    text = script.path.read_text(encoding="utf-8")
    assert script.template_id == "synthetic-control-v1"
    assert "synthdid::synthdid_estimate" in text
    assert "set.seed(20260812)" in text
    assert '"placebo.csv"' in text and '"leave_one_out.csv"' in text
    assert "--" not in text
    assert str(tmp_path) not in text


def _outputs(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "effect.csv").write_text(
        "term,estimate,std_error,conf_low,conf_high\nATT,-1.4,0.2,-1.8,-1.0\n",
        encoding="utf-8",
    )
    (root / "weights.csv").write_text(
        "donor,weight\ndonor-a,0.6\ndonor-b,0.4\n", encoding="utf-8"
    )
    (root / "gaps.csv").write_text(
        "time,treated,synthetic,gap,period\n2008,11,10.8,0.2,pre\n2009,10,9.8,0.2,pre\n2010,8,9.4,-1.4,post\n2011,7,8.4,-1.4,post\n",
        encoding="utf-8",
    )
    (root / "rmspe.csv").write_text(
        "pre_periods,post_periods,pre_rmspe,post_rmspe,max_pre_rmspe,post_pre_ratio\n2,2,0.2,1.4,2,7\n",
        encoding="utf-8",
    )
    (root / "placebo.csv").write_text(
        "unit,effect\ndonor-a,0.1\ndonor-b,-0.1\n", encoding="utf-8"
    )
    (root / "leave_one_out.csv").write_text(
        "omitted,effect,absolute_change\ndonor-a,-1.2,0.2\ndonor-b,-1.6,0.2\n",
        encoding="utf-8",
    )
    (root / "package_configuration.csv").write_text(
        "method_id,r_version,package_version,intervention_time,leave_one_out_threshold\nsynthetic-control,R version 4.4.3,0.0.9,2010,2\n",
        encoding="utf-8",
    )
    (root / "synthetic_control.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><g class="x-tick"/></svg>\n',
        encoding="utf-8",
    )


def test_scm_parser_requires_exact_authority_and_sensitivity(tmp_path: Path) -> None:
    _outputs(tmp_path)
    result = SyntheticControlRecipe(tmp_path / "work").parse(tmp_path, (_authority(),))
    assert isinstance(result, SyntheticControlResult)
    assert result.pre_periods == 2 and len(result.gaps) == 4
    with pytest.raises(ValueError, match="synthdid"):
        SyntheticControlRecipe(tmp_path / "work").parse(tmp_path, ())


def test_scm_parser_rejects_nonconvex_or_tampered_outputs(tmp_path: Path) -> None:
    _outputs(tmp_path)
    (tmp_path / "weights.csv").write_text(
        "donor,weight\ndonor-a,1.2\ndonor-b,-0.2\n", encoding="utf-8"
    )
    with pytest.raises(ValueError):
        SyntheticControlRecipe(tmp_path / "work").parse(tmp_path, (_authority(),))


def test_scm_threshold_and_package_substitution_have_stable_failures(
    tmp_path: Path,
) -> None:
    _outputs(tmp_path)
    (tmp_path / "rmspe.csv").write_text(
        "pre_periods,post_periods,pre_rmspe,post_rmspe,max_pre_rmspe,post_pre_ratio\n2,2,0.2,1.4,0.05,7\n",
        encoding="utf-8",
    )
    with pytest.raises(CausalOutputInvalid) as prefit:
        SyntheticControlRecipe(tmp_path / "work").parse(tmp_path, (_authority(),))
    assert prefit.value.code == "SCM_PREFIT_EXCEEDED"
    _outputs(tmp_path)
    wrong = ArtifactRef(
        artifact_id="r-package-authority-synthdid-0.0.8",
        artifact_version=1,
        content_hash="c" * 64,
    )
    with pytest.raises(CausalOutputInvalid, match="version"):
        SyntheticControlRecipe(tmp_path / "work").parse(tmp_path, (wrong,))


def test_scm_support_is_rebuilt_from_owned_snapshot(tmp_path: Path) -> None:
    _outputs(tmp_path)
    result = SyntheticControlRecipe(tmp_path / "work").parse(tmp_path, (_authority(),))
    source = Path(__file__).parents[1] / "fixtures/econometrics/synthetic_control.csv"
    assert wave_result_matches_snapshot(source.read_bytes(), _spec(source), result)  # type: ignore[arg-type]
    forged = result.model_copy(update={"pre_periods": 3})
    assert not wave_result_matches_snapshot(source.read_bytes(), _spec(source), forged)  # type: ignore[arg-type]


def test_scm_rejects_contradictory_output_configuration(tmp_path: Path) -> None:
    _outputs(tmp_path)
    (tmp_path / "package_configuration.csv").write_text(
        "method_id,r_version,package_version,intervention_time,leave_one_out_threshold\nmeta-analysis,R version 4.4.3,0.0.9,999,2\n",
        encoding="utf-8",
    )
    with pytest.raises(CausalOutputInvalid, match="another method"):
        SyntheticControlRecipe(tmp_path / "work").parse(tmp_path, (_authority(),))
