"""Closed scientific-code mapping for generated R failures."""

from pathlib import Path

import pytest
from test_econometrics_r_runtime import RecordingExecutor, _runner

from envresearch.econometrics._r_failure_codes import registered_failure_code
from envresearch.econometrics.r_evidence import RCommandResult
from envresearch.econometrics.r_runtime import RExecutionFailed


def test_missing_r_package_has_stable_failure_code(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    runner, _, script = _runner(tmp_path, executor)
    executor.analysis_result = RCommandResult(
        return_code=1,
        stdout=b"",
        stderr=b"there is no package called 'fixest'",
    )

    with pytest.raises(RExecutionFailed) as captured:
        runner.run(script)

    assert captured.value.code == "R_PACKAGE_UNAVAILABLE"


@pytest.mark.parametrize(
    ("template_id", "code"),
    (
        ("hedonic-pricing-v1", "HEDONIC_TERM_UNIDENTIFIED"),
        ("travel-cost-v1", "TRAVEL_COST_SLOPE_INVALID"),
        ("contingent-valuation-v1", "CV_MONOTONICITY_FAILED"),
        ("dce-clogit-v1", "DCE_TERM_UNIDENTIFIED"),
    ),
)
def test_generated_script_can_emit_registered_scientific_failure_code(
    template_id: str, code: str, tmp_path: Path
) -> None:
    executor = RecordingExecutor()
    runner, _, script = _runner(tmp_path, executor)
    script = script.model_copy(update={"template_id": template_id})
    runner.approved_scripts = {template_id: script.sha256}
    executor.analysis_result = RCommandResult(
        return_code=1,
        stdout=b"",
        stderr=f"ENVRESEARCH_CODE:{code}\n".encode(),
    )

    with pytest.raises(RExecutionFailed) as captured:
        runner.run(script)

    assert captured.value.code == code


def test_generated_script_cannot_invent_scientific_failure_code(
    tmp_path: Path,
) -> None:
    executor = RecordingExecutor()
    runner, _, script = _runner(tmp_path, executor)
    executor.analysis_result = RCommandResult(
        return_code=1,
        stdout=b"",
        stderr=b"ENVRESEARCH_CODE:FAKE_PASS\n",
    )

    with pytest.raises(RExecutionFailed) as captured:
        runner.run(script)

    assert captured.value.code == "R_EXECUTION_FAILED"


@pytest.mark.parametrize(
    ("template_id", "code"),
    (
        ("hedonic-pricing-v1", "HEDONIC_TERM_UNIDENTIFIED"),
        ("travel-cost-v1", "TRAVEL_COST_SLOPE_INVALID"),
        ("contingent-valuation-v1", "CV_BID_SLOPE_INVALID"),
        ("dce-clogit-v1", "DCE_COST_SLOPE_INVALID"),
    ),
)
def test_scientific_failure_code_is_bound_to_exact_template_and_line(
    template_id: str, code: str
) -> None:
    marker = f"ENVRESEARCH_CODE:{code}"

    assert registered_failure_code(template_id, marker + "\n") == code
    assert registered_failure_code("did-event-study-v1", marker + "\n") is None
    assert registered_failure_code(template_id, "prefix " + marker + "\n") is None
