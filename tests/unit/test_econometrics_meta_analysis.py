"""Meta-analysis recipe rendering and output policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from envresearch.econometrics._causal_outputs import CausalOutputInvalid
from envresearch.econometrics._wave1_support import wave_result_matches_snapshot
from envresearch.econometrics.analysis_specs import ANALYSIS_SPEC_ADAPTER, AnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataSnapshot, MissingValueCount
from envresearch.econometrics.meta_analysis import MetaAnalysisRecipe
from envresearch.econometrics.wave1_results import MetaAnalysisResult
from envresearch.models.artifact import ArtifactRef


def _spec(path: Path) -> AnalysisSpec:
    return ANALYSIS_SPEC_ADAPTER.validate_json(
        json.dumps(
            {
                "schema_version": "econometrics.meta-analysis.v1",
                "method_id": "meta-analysis",
                "data_path": str(path),
                "columns": {"study": "study", "effect": "effect", "variance": "var"},
                "confidence_level": 0.95,
                "max_leave_one_out_change": 0.5,
                "model": "fixed-and-dl-random",
                "budget": {
                    "inactivity_seconds": 60,
                    "max_output_bytes": 1_000_000,
                    "max_workspace_bytes": 10_000_000,
                },
            }
        )
    )


def _snapshot() -> LocalDataSnapshot:
    columns = ("study", "effect", "var")
    return LocalDataSnapshot(
        reference=ArtifactRef(
            artifact_id="local-data-meta", artifact_version=1, content_hash="a" * 64
        ),
        relative_path=Path("artifacts/econometrics/data/meta.csv"),
        sha256="a" * 64,
        size_bytes=100,
        row_count=3,
        columns=columns,
        missing_values=tuple(
            MissingValueCount(column=item, count=0) for item in columns
        ),
    )


def _authority() -> ArtifactRef:
    return ArtifactRef(
        artifact_id="r-package-authority-metafor-4.8-0",
        artifact_version=1,
        content_hash="b" * 64,
    )


def test_meta_script_is_registered_owned_and_offline(tmp_path: Path) -> None:
    script = MetaAnalysisRecipe(tmp_path / "work").render(
        _spec(tmp_path / "meta.csv"), _snapshot()
    )
    text = script.path.read_text(encoding="utf-8")
    assert script.template_id == "meta-analysis-v1"
    assert "metafor::rma.uni" in text
    assert '"heterogeneity.csv"' in text and '"leave_one_out.csv"' in text
    assert "qnorm(1 - (1 - confidence_level) / 2)" in text
    assert str(tmp_path) not in text


def _outputs(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    coefficient = "term,estimate,std_error,conf_low,conf_high\n%s,0.14,0.05,0.04,0.24\n"
    (root / "fixed.csv").write_text(coefficient % "fixed", encoding="utf-8")
    (root / "random.csv").write_text(coefficient % "random", encoding="utf-8")
    (root / "heterogeneity.csv").write_text(
        "studies,q,i_squared,tau_squared,inverse_variance_support,prediction_low,prediction_high\n3,1,0,0,183.3333333333,-0.1,0.38\n",
        encoding="utf-8",
    )
    (root / "study_weights.csv").write_text(
        "study,effect,std_error,weight\ns1,0.1,0.1,0.5454545455\ns2,0.2,0.1414213562,0.2727272727\ns3,0.15,0.1732050808,0.1818181818\n",
        encoding="utf-8",
    )
    (root / "leave_one_out.csv").write_text(
        "omitted,effect,absolute_change\ns1,0.16,0.02\ns2,0.12,0.02\ns3,0.14,0\n",
        encoding="utf-8",
    )
    (root / "funnel.csv").write_text(
        "study,effect,std_error\ns1,0.1,0.1\ns2,0.2,0.1414213562\ns3,0.15,0.1732050808\n",
        encoding="utf-8",
    )
    (root / "package_configuration.csv").write_text(
        "method_id,r_version,package_version,confidence_level,model,leave_one_out_threshold\nmeta-analysis,R version 4.4.3,4.8.0,0.95,fixed-and-dl-random,0.5\n",
        encoding="utf-8",
    )
    (root / "forest_funnel.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><g class="x-tick"/></svg>\n',
        encoding="utf-8",
    )


def test_meta_parser_requires_authority_and_complete_study_evidence(
    tmp_path: Path,
) -> None:
    _outputs(tmp_path)
    result = MetaAnalysisRecipe(tmp_path / "work").parse(tmp_path, (_authority(),))
    assert isinstance(result, MetaAnalysisResult)
    assert result.studies == 3 and len(result.funnel) == 3
    with pytest.raises(ValueError, match="metafor"):
        MetaAnalysisRecipe(tmp_path / "work").parse(tmp_path, ())


def test_meta_parser_rejects_influence_forgery(tmp_path: Path) -> None:
    _outputs(tmp_path)
    (tmp_path / "leave_one_out.csv").write_text(
        "omitted,effect,absolute_change\ns1,99,0.02\ns2,0.12,0.02\ns3,0.14,0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        MetaAnalysisRecipe(tmp_path / "work").parse(tmp_path, (_authority(),))


def test_meta_influence_and_package_substitution_have_stable_failures(
    tmp_path: Path,
) -> None:
    _outputs(tmp_path)
    (tmp_path / "leave_one_out.csv").write_text(
        "omitted,effect,absolute_change\ns1,0.8,0.66\ns2,0.12,0.02\ns3,0.14,0\n",
        encoding="utf-8",
    )
    with pytest.raises(CausalOutputInvalid) as influence:
        MetaAnalysisRecipe(tmp_path / "work").parse(tmp_path, (_authority(),))
    assert influence.value.code == "META_INFLUENCE_EXCEEDED"
    _outputs(tmp_path)
    wrong = ArtifactRef(
        artifact_id="r-package-authority-metafor-4.7.0",
        artifact_version=1,
        content_hash="c" * 64,
    )
    with pytest.raises(CausalOutputInvalid, match="version"):
        MetaAnalysisRecipe(tmp_path / "work").parse(tmp_path, (wrong,))


def test_meta_support_is_rebuilt_from_owned_snapshot(tmp_path: Path) -> None:
    _outputs(tmp_path)
    result = MetaAnalysisRecipe(tmp_path / "work").parse(tmp_path, (_authority(),))
    source = Path(__file__).parents[1] / "fixtures/econometrics/meta_analysis.csv"
    assert wave_result_matches_snapshot(source.read_bytes(), _spec(source), result)  # type: ignore[arg-type]
    forged = result.model_copy(update={"inverse_variance_support": 1.0})
    assert not wave_result_matches_snapshot(source.read_bytes(), _spec(source), forged)  # type: ignore[arg-type]


def test_meta_rejects_contradictory_output_configuration(tmp_path: Path) -> None:
    _outputs(tmp_path)
    (tmp_path / "package_configuration.csv").write_text(
        "method_id,r_version,package_version,confidence_level,model,leave_one_out_threshold\nsynthetic-control,R version 4.4.3,4.8.0,0.95,fixed-and-dl-random,0.5\n",
        encoding="utf-8",
    )
    with pytest.raises(CausalOutputInvalid, match="model"):
        MetaAnalysisRecipe(tmp_path / "work").parse(tmp_path, (_authority(),))
