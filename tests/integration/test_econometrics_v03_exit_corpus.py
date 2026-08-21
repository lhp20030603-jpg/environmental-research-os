"""Checked local corpus authority for the blinded V0.3 exit."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from envresearch.econometrics.exit_corpus import freeze_exit_corpus
from envresearch.econometrics.exit_models import (
    ExitCaseInput,
    ExitExpectationCatalog,
    V03ExitManifest,
)
from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.recipes import recipe_for

CORPUS = Path(__file__).parents[2] / "benchmarks/econometrics/v03-exit"


def test_checked_corpus_freezes_exact_blind_matrix(tmp_path: Path) -> None:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())

    frozen = freeze_exit_corpus(CORPUS.resolve(), runner, evaluator)
    manifest = runner.load(frozen.manifest_ref, V03ExitManifest)
    catalog = evaluator.load(frozen.catalog_ref, ExitExpectationCatalog)

    assert len(manifest.cases) == len(catalog.cases) == 16
    assert manifest.expectation_catalog_ref == frozen.catalog_ref
    assert {item.case_id for item in manifest.cases} == {
        item.case_id for item in catalog.cases
    }
    for case in manifest.cases:
        payload = runner.load(case.case_ref, ExitCaseInput)
        assert payload.case_id == case.case_id
        assert payload.family == case.family
        assert payload.spec.data_path.is_relative_to(runner.root / "exit/data")
        assert runner.load_bytes(payload.data_ref)

    runner_bytes = b"".join(path.read_bytes() for path in runner.root.rglob("*.json"))
    assert b"expected_code" not in runner_bytes
    assert b"DID_PRETREND_EXCEEDED" not in runner_bytes


def test_checked_failure_catalog_is_exact_and_separate(tmp_path: Path) -> None:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    frozen = freeze_exit_corpus(CORPUS.resolve(), runner, evaluator)
    catalog = evaluator.load(frozen.catalog_ref, ExitExpectationCatalog)

    failures = {
        item.case_id: item.expected_code
        for item in catalog.cases
        if item.role != "green"
    }
    assert failures == {
        "fail-did-pretrend": "DID_PRETREND_EXCEEDED",
        "fail-iv-weak-instrument": "IV_WEAK_INSTRUMENT",
        "fail-measurement-unit": "MEASUREMENT_UNIT_MISMATCH",
        "fail-meta-influence": "META_INFLUENCE_EXCEEDED",
        "fail-rct-attrition": "RCT_ATTRITION_EXCEEDED",
        "fail-rdd-support": "RDD_SUPPORT_INSUFFICIENT",
        "fail-scm-prefit": "SCM_PREFIT_EXCEEDED",
        "integrity-output-tamper": "EVIDENCE_TAMPERED",
    }


def test_green_oracles_cover_every_registered_output(tmp_path: Path) -> None:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    frozen = freeze_exit_corpus(CORPUS.resolve(), runner, evaluator)
    manifest = runner.load(frozen.manifest_ref, V03ExitManifest)
    catalog = evaluator.load(frozen.catalog_ref, ExitExpectationCatalog)
    expectations = {item.case_id: item for item in catalog.cases}

    for case in manifest.cases:
        if case.role != "green":
            continue
        observed = {item.output_name for item in expectations[case.case_id].comparisons}
        assert (
            observed
            == recipe_for(
                case.family, workspace=tmp_path / case.case_id
            ).expected_outputs
        )


@pytest.mark.parametrize(
    ("relative", "target"),
    (
        ("runner/data/rct.csv", Path("runner/data/measurement.csv")),
        ("runner/green-rct.yaml", Path("runner/green-did.yaml")),
    ),
)
def test_corpus_rejects_symlinked_inputs(
    relative: str, target: Path, tmp_path: Path
) -> None:
    copied = tmp_path / "corpus"
    shutil.copytree(CORPUS, copied)
    path = copied / relative
    path.unlink()
    path.symlink_to(copied / target)
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())

    with pytest.raises(ValueError, match="non-symlink"):
        freeze_exit_corpus(copied.resolve(), runner, evaluator)


def test_frozen_case_binds_immutable_csv_bytes(tmp_path: Path) -> None:
    copied = tmp_path / "corpus"
    shutil.copytree(CORPUS, copied)
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    frozen = freeze_exit_corpus(copied.resolve(), runner, evaluator)
    manifest = runner.load(frozen.manifest_ref, V03ExitManifest)
    case = next(item for item in manifest.cases if item.case_id == "fail-rct-attrition")
    payload = runner.load(case.case_ref, ExitCaseInput)
    frozen_bytes = runner.load_bytes(payload.data_ref)

    source = copied / "runner/data/rct.csv"
    source.write_bytes(source.read_bytes() + b"\n")

    assert runner.load(frozen.manifest_ref, V03ExitManifest) == manifest
    assert runner.load_bytes(payload.data_ref) == frozen_bytes
    assert payload.spec.data_path.read_bytes() == frozen_bytes
