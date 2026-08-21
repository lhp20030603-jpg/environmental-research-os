"""Stable scientific-failure codes required by the V0.3 exit matrix."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from envresearch.econometrics.contracts import LocalAnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataInvalid
from envresearch.econometrics.did_diagnostics import pretrend_exceeded
from envresearch.econometrics.iv_2sls import Iv2slsRecipe
from envresearch.econometrics.local_validation import validate_csv


def _did_spec(tmp_path: Path, threshold: float = 0.2) -> LocalAnalysisSpec:
    return LocalAnalysisSpec(
        schema_version="econometrics.local-analysis.v1",
        method_id="did-event-study",
        data_path=tmp_path / "panel.csv",
        columns={
            "unit": "unit",
            "time": "year",
            "outcome": "outcome",
            "treatment_cohort": "cohort",
            "covariates": (),
        },
        comparison_group="never-treated",
        reference_period=-1,
        max_pretrend_abs=threshold,
        inference={
            "confidence_level": 0.95,
            "cluster_column": "unit",
            "interval_mode": "pointwise",
            "bootstrap_seed": 20260812,
        },
        budget={
            "inactivity_seconds": 60,
            "max_output_bytes": 1_000_000,
            "max_workspace_bytes": 10_000_000,
        },
    )


def test_did_pretrend_threshold_is_finite_and_blocks_large_leads(
    tmp_path: Path,
) -> None:
    result = SimpleNamespace(
        dynamic=SimpleNamespace(
            estimates=(
                SimpleNamespace(event_time=-2, estimate=0.2),
                SimpleNamespace(event_time=0, estimate=1.0),
            )
        )
    )
    assert pretrend_exceeded(_did_spec(tmp_path, 0.19), result)  # type: ignore[arg-type]
    assert not pretrend_exceeded(_did_spec(tmp_path, 0.2), result)  # type: ignore[arg-type]
    alternate_reference = _did_spec(tmp_path, 0.2).model_copy(
        update={"reference_period": -2}
    )
    alternate_result = SimpleNamespace(
        dynamic=SimpleNamespace(
            estimates=(
                SimpleNamespace(event_time=-2, estimate=99.0),
                SimpleNamespace(event_time=-1, estimate=0.3),
                SimpleNamespace(event_time=0, estimate=99.0),
            )
        )
    )
    assert pretrend_exceeded(alternate_reference, alternate_result)  # type: ignore[arg-type]

    varying_base_last_pseudo_att = SimpleNamespace(
        dynamic=SimpleNamespace(
            estimates=(SimpleNamespace(event_time=-1, estimate=0.21),)
        )
    )
    assert pretrend_exceeded(  # type: ignore[arg-type]
        _did_spec(tmp_path, 0.2), varying_base_last_pseudo_att
    )
    for threshold in (0.0, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            _did_spec(tmp_path, threshold)


def test_iv_weak_first_stage_has_stable_failure_code(tmp_path: Path) -> None:
    from test_econometrics_panel_iv import _write_coefficient, _write_common

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
    with pytest.raises(ValueError) as caught:
        Iv2slsRecipe(tmp_path / "work").parse(tmp_path)
    assert getattr(caught.value, "code", None) == "IV_WEAK_INSTRUMENT"

    (tmp_path / "first_stage.csv").write_text(
        "endogenous,instruments,f_statistic,threshold\nweak instrument ,wind,12,10\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as malformed:
        Iv2slsRecipe(tmp_path / "work").parse(tmp_path)
    assert getattr(malformed.value, "code", None) == "OUTPUT_INVALID"

    for f_statistic, threshold in ((-1, 10), (-2, -1), (12, 0)):
        (tmp_path / "first_stage.csv").write_text(
            "endogenous,instruments,f_statistic,threshold\n"
            f"price,wind,{f_statistic},{threshold}\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError) as invalid_domain:
            Iv2slsRecipe(tmp_path / "work").parse(tmp_path)
        assert getattr(invalid_domain.value, "code", None) == "OUTPUT_INVALID"


def test_rdd_support_failure_has_stable_code(tmp_path: Path) -> None:
    from test_econometrics_causal_data import _spec, rdd_payload

    path = tmp_path / "rdd.csv"
    path.write_text(
        "emissions,score,income\n1,0,5\n2,1,6\n3,2,7\n4,3,8\n",
        encoding="utf-8",
    )
    with pytest.raises(LocalDataInvalid) as caught:
        validate_csv(_spec(rdd_payload(path)))
    assert caught.value.code == "RDD_SUPPORT_INSUFFICIENT"
