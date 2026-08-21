"""Environmental-measurement recipe and output-policy tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from envresearch.econometrics._wave1_support import wave_result_matches_snapshot
from envresearch.econometrics.analysis_specs import ANALYSIS_SPEC_ADAPTER, AnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataSnapshot, MissingValueCount
from envresearch.econometrics.measurement import MeasurementRecipe
from envresearch.econometrics.r_runtime import FORBIDDEN_R
from envresearch.econometrics.wave1_results import MeasurementResult
from envresearch.models.artifact import ArtifactRef


def _spec(path: Path) -> AnalysisSpec:
    return ANALYSIS_SPEC_ADAPTER.validate_json(
        json.dumps(
            {
                "schema_version": "econometrics.environmental-measurement.v1",
                "method_id": "environmental-measurement",
                "data_path": str(path),
                "columns": {
                    "monitor": "monitor",
                    "timestamp": "date",
                    "value": "pm25",
                    "unit": "unit",
                    "detection_flag": "flag",
                },
                "declared_unit": "ug/m3",
                "max_missing_rate": 0.25,
                "valid_min": 0.0,
                "valid_max": 500.0,
                "exceedance_threshold": 35.0,
                "budget": {
                    "inactivity_seconds": 60,
                    "max_output_bytes": 1_000_000,
                    "max_workspace_bytes": 10_000_000,
                },
            }
        )
    )


def _snapshot() -> LocalDataSnapshot:
    columns = ("monitor", "date", "pm25", "unit", "flag")
    return LocalDataSnapshot(
        reference=ArtifactRef(
            artifact_id="local-data-measurement",
            artifact_version=1,
            content_hash="a" * 64,
        ),
        relative_path=Path("artifacts/econometrics/data/measurement.csv"),
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
        artifact_id="authority-r-base", artifact_version=1, content_hash="b" * 64
    )


def test_measurement_script_is_descriptive_owned_and_offline(tmp_path: Path) -> None:
    script = MeasurementRecipe(tmp_path / "work").render(
        _spec(tmp_path / "measurement.csv"), _snapshot()
    )
    text = script.path.read_text(encoding="utf-8")
    assert script.template_id == "environmental-measurement-v1"
    assert '"summary.csv"' in text
    assert '"completeness.csv"' in text
    assert '"exceedances.csv"' in text
    assert '"temporal.csv"' in text
    assert '"monitor_coverage.csv"' in text
    assert 'class="x-tick"' in text
    assert "max(abs(c(y_min, y_max)), 1) * 1e-6" in text
    assert "feols" not in text
    assert FORBIDDEN_R.search(text) is None
    assert str(tmp_path) not in text


def _write_outputs(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "summary.csv").write_text(
        "mean,minimum,q25,median,q75,maximum,exceedances\n22,12,15,18,27,36,1\n",
        encoding="utf-8",
    )
    (root / "completeness.csv").write_text(
        "total,valid,missing,monitors,missing_rate,max_missing_rate\n4,3,1,2,0.25,0.25\n",
        encoding="utf-8",
    )
    (root / "exceedances.csv").write_text("threshold,count\n35,1\n", encoding="utf-8")
    (root / "temporal.csv").write_text(
        "date,mean\n2020-01-01,24\n2020-01-02,18\n", encoding="utf-8"
    )
    (root / "monitor_coverage.csv").write_text(
        "monitor,total,valid,missing\nm1,2,2,0\nm2,2,1,1\n", encoding="utf-8"
    )
    (root / "package_configuration.csv").write_text(
        "method_id,r_version,declared_unit\n"
        "environmental-measurement,R version 4.4.3,ug/m3\n",
        encoding="utf-8",
    )
    (root / "measurement_plot.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><g class="x-tick"/></svg>\n',
        encoding="utf-8",
    )


def test_measurement_parser_returns_no_causal_effect(tmp_path: Path) -> None:
    _write_outputs(tmp_path)
    result = MeasurementRecipe(tmp_path / "work").parse(tmp_path, ())
    assert isinstance(result, MeasurementResult)
    assert result.support.valid == 3
    assert "effect" not in type(result).model_fields


def test_measurement_summary_is_recomputed_from_owned_fixture(tmp_path: Path) -> None:
    _write_outputs(tmp_path)
    result = MeasurementRecipe(tmp_path / "work").parse(tmp_path, ())
    source = (
        Path(__file__).parents[1]
        / "fixtures/econometrics/environmental_measurement.csv"
    )
    spec = _spec(source)
    assert wave_result_matches_snapshot(source.read_bytes(), spec, result)  # type: ignore[arg-type]
    forged = result.model_copy(update={"mean": 999.0})
    assert not wave_result_matches_snapshot(source.read_bytes(), spec, forged)  # type: ignore[arg-type]


def test_measurement_parser_rejects_incoherent_completeness(tmp_path: Path) -> None:
    _write_outputs(tmp_path)
    (tmp_path / "completeness.csv").write_text(
        "total,valid,missing,monitors,missing_rate,max_missing_rate\n4,3,0,2,0,0.25\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="reconcile"):
        MeasurementRecipe(tmp_path / "work").parse(tmp_path, (_authority(),))
