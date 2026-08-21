"""CLI error mapping for exact Valuation Core run and evaluation operations."""

from __future__ import annotations

import json
from pathlib import Path

from test_econometrics_valuation_exit import _OfflineService, offline_refs
from typer.testing import CliRunner

from envresearch.cli import app
from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.valuation_exit_runner import (
    ValuationExitRunner,
    ValuationRegistryAnalysisExecutor,
)
from envresearch.models.artifact import ArtifactRef


def _options(tmp_path: Path) -> list[str]:
    return [
        "--runner-root",
        str((tmp_path / "runner").resolve()),
        "--evaluator-root",
        str((tmp_path / "evaluator").resolve()),
        "--analysis-root",
        str((tmp_path / "analysis").resolve()),
    ]


def _write_ref(path: Path, name: str) -> None:
    path.write_text(
        ArtifactRef(
            artifact_id=name, artifact_version=1, content_hash="0" * 64
        ).model_dump_json(),
        encoding="utf-8",
    )


def test_run_and_evaluate_map_missing_exact_state_to_typed_cli_error(
    tmp_path: Path, monkeypatch
) -> None:
    service = _OfflineService()
    monkeypatch.setattr(
        "envresearch.econometrics.cli._service_for", lambda *args, **kwargs: service
    )
    manifest = tmp_path / "manifest.json"
    run = tmp_path / "run.json"
    catalog = tmp_path / "catalog.json"
    _write_ref(manifest, "missing-manifest")
    _write_ref(run, "missing-run")
    _write_ref(catalog, "missing-catalog")

    run_result = CliRunner().invoke(
        app,
        [
            "econometrics",
            "valuation-exit-run",
            str(manifest),
            *_options(tmp_path),
            "--r-executable",
            "/usr/bin/false",
            "--r-sha256",
            "0" * 64,
            "--json",
        ],
    )
    evaluate_result = CliRunner().invoke(
        app,
        [
            "econometrics",
            "valuation-exit-evaluate",
            str(run),
            str(catalog),
            *_options(tmp_path),
            "--json",
        ],
    )

    assert run_result.exit_code == 2
    assert json.loads(run_result.stdout)["error"]["code"] == "VALUATION_EXIT_INVALID"
    assert evaluate_result.exit_code == 2
    assert (
        json.loads(evaluate_result.stdout)["error"]["code"] == "VALUATION_EXIT_INVALID"
    )


def test_evaluate_returns_nonzero_for_an_authenticated_failed_report(
    tmp_path: Path, monkeypatch
) -> None:
    runner = ExitRegistry((tmp_path / "runner").resolve())
    evaluator = ExitRegistry((tmp_path / "evaluator").resolve())
    refs = offline_refs(tmp_path, runner, evaluator)
    service = _OfflineService()
    run_ref = ValuationExitRunner(
        runner,
        ValuationRegistryAnalysisExecutor(runner, service),  # type: ignore[arg-type]
    ).run(refs.manifest_ref)
    service.snapshot_mode = "mismatch"
    run_path = tmp_path / "run.json"
    catalog_path = tmp_path / "catalog.json"
    run_path.write_text(run_ref.model_dump_json(), encoding="utf-8")
    catalog_path.write_text(refs.catalog_ref.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(
        "envresearch.econometrics.cli._service_for", lambda *args, **kwargs: service
    )

    result = CliRunner().invoke(
        app,
        [
            "econometrics",
            "valuation-exit-evaluate",
            str(run_path),
            str(catalog_path),
            *_options(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["report"]["status"] == "failed"
