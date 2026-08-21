"""Seeded checked-corpus failures reject at their registered boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from envresearch.econometrics.data_snapshot import LocalDataInvalid
from envresearch.econometrics.exit_corpus import freeze_exit_corpus
from envresearch.econometrics.exit_models import ExitCaseInput, V03ExitManifest
from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.local_validation import validate_csv

CORPUS = Path(__file__).parents[2] / "benchmarks/econometrics/v03-exit"


@pytest.mark.parametrize(
    ("case_id", "code"),
    (
        ("fail-rct-attrition", "RCT_ATTRITION_EXCEEDED"),
        ("fail-rdd-support", "RDD_SUPPORT_INSUFFICIENT"),
        ("fail-measurement-unit", "MEASUREMENT_UNIT_MISMATCH"),
    ),
)
def test_seeded_input_failures_emit_exact_codes(
    case_id: str, code: str, tmp_path: Path
) -> None:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    frozen = freeze_exit_corpus(CORPUS.resolve(), runner, evaluator)
    manifest = runner.load(frozen.manifest_ref, V03ExitManifest)
    case = next(item for item in manifest.cases if item.case_id == case_id)
    payload = runner.load(case.case_ref, ExitCaseInput)

    with pytest.raises(LocalDataInvalid) as caught:
        validate_csv(payload.spec)
    assert caught.value.code == code


def test_green_checked_inputs_validate_without_execution(tmp_path: Path) -> None:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    frozen = freeze_exit_corpus(CORPUS.resolve(), runner, evaluator)
    manifest = runner.load(frozen.manifest_ref, V03ExitManifest)

    for case in manifest.cases:
        if case.role != "green":
            continue
        payload = runner.load(case.case_ref, ExitCaseInput)
        assert validate_csv(payload.spec).row_count > 0
