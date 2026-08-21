"""Regression coverage for V0.3.1 handoff materialization races."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from envresearch.econometrics.valuation_exit_models import (
    ValuationExitCatalogBinding,
    ValuationExitManifest,
    ValuationExitReport,
    ValuationExitRun,
)
from envresearch.econometrics.valuation_transition import (
    V031ExitHarness,
    accepted_analysis_reports,
)


@pytest.mark.parametrize("authority", ("report", "run", "catalog-binding"))
def test_accepted_reports_reconstruct_full_chain_after_materialization(
    authority: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _copy_sealed_root(tmp_path)
    harness = V031ExitHarness(root)
    real_status_service = harness._status_service
    calls = 0
    mutations: list[str] = []

    def status_service() -> object:
        nonlocal calls
        calls += 1
        service = _status_service_for_pre_reseal_root(real_status_service())
        if calls != 2:
            return service
        original_status = service.status
        changed = False

        def status(reference: object) -> object:
            nonlocal changed
            report = original_status(reference)  # type: ignore[arg-type]
            if not changed:
                changed = True
                _advance_current_authority(harness, authority)
                mutations.append(authority)
            return report

        service.status = status  # type: ignore[method-assign]
        return service

    monkeypatch.setattr(harness, "_status_service", status_service)

    with pytest.raises(ValueError, match="current|stale|consistent|does not match"):
        accepted_analysis_reports(harness)
    assert mutations == [authority]


@pytest.mark.parametrize("authority", ("report", "run", "catalog-binding"))
def test_accepted_reports_recheck_authority_changed_within_final_replay(
    authority: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _copy_sealed_root(tmp_path)
    harness = V031ExitHarness(root)
    real_status_service = harness._status_service
    calls = 0
    mutations: list[str] = []

    def status_service() -> object:
        nonlocal calls
        calls += 1
        service = _status_service_for_pre_reseal_root(real_status_service())
        if calls != 3:
            return service
        original_status = service.status
        changed = False

        def status(reference: object) -> object:
            nonlocal changed
            report = original_status(reference)  # type: ignore[arg-type]
            if not changed:
                changed = True
                _advance_current_authority(harness, authority)
                mutations.append(authority)
            return report

        service.status = status  # type: ignore[method-assign]
        return service

    monkeypatch.setattr(harness, "_status_service", status_service)

    with pytest.raises(ValueError, match="current|stale|consistent|does not match"):
        accepted_analysis_reports(harness)
    assert calls == 3
    assert mutations == [authority]


def test_final_replay_rechecks_run_changed_after_reconstruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = V031ExitHarness(_copy_sealed_root(tmp_path))
    real_reconstruct = harness._reconstruct
    monkeypatch.setattr(
        harness,
        "_status_service",
        lambda: _status_service_for_pre_reseal_root(
            V031ExitHarness._status_service(harness)
        ),
    )
    calls = 0

    def reconstruct() -> ValuationExitReport:
        nonlocal calls
        report = real_reconstruct()
        calls += 1
        if calls == 2:
            _advance_current_authority(harness, "run")
        return report

    monkeypatch.setattr(harness, "_reconstruct", reconstruct)

    with pytest.raises(ValueError, match="current|stale|consistent"):
        accepted_analysis_reports(harness)
    assert calls == 2


def test_accepted_reports_finish_with_full_current_chain_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = V031ExitHarness(_copy_sealed_root(tmp_path))
    real_run_and_evaluate = harness.run_and_evaluate
    monkeypatch.setattr(
        harness,
        "_status_service",
        lambda: _status_service_for_pre_reseal_root(
            V031ExitHarness._status_service(harness)
        ),
    )
    calls = 0

    def run_and_evaluate() -> ValuationExitReport:
        nonlocal calls
        report = real_run_and_evaluate()
        calls += 1
        if calls == 2:
            _advance_current_authority(harness, "run")
        return report

    monkeypatch.setattr(harness, "run_and_evaluate", run_and_evaluate)

    with pytest.raises(ValueError, match="current|stale|consistent"):
        accepted_analysis_reports(harness)
    assert calls == 2


def _copy_sealed_root(tmp_path: Path) -> Path:
    value = os.getenv("ENVRESEARCH_V031_ACCEPTANCE_ROOT")
    if value is None:
        pytest.skip("set ENVRESEARCH_V031_ACCEPTANCE_ROOT for transition security")
    target = (tmp_path / "sealed-copy").resolve()
    shutil.copytree(Path(value).resolve(strict=True), target)
    return target


def _advance_current_authority(harness: V031ExitHarness, authority: str) -> None:
    manifest = harness.runner.load(harness.marker.manifest_ref, ValuationExitManifest)
    cases = {
        "report": (
            harness.evaluator,
            f"valuation-report-{manifest.manifest_id}",
            harness.marker.report_ref,
            ValuationExitReport,
        ),
        "run": (
            harness.runner,
            f"valuation-run-{manifest.manifest_id}",
            harness.marker.run_ref,
            ValuationExitRun,
        ),
        "catalog-binding": (
            harness.evaluator,
            f"valuation-catalog-{manifest.manifest_id}",
            harness.marker.catalog_binding_ref,
            ValuationExitCatalogBinding,
        ),
    }
    registry, subject, reference, model = cases[authority]
    payload = registry.load(reference, model)
    revised = registry.publish(
        reference.artifact_id, payload, version=reference.artifact_version + 1
    )
    registry.set_current(subject, revised)


def _status_service_for_pre_reseal_root(service: object) -> object:
    """Keep this exit-current race isolated from the separately tested CV upgrade."""
    original_status = service.status  # type: ignore[attr-defined]

    def status(reference: object) -> object:
        payload = json.loads(
            service.files.read(reference.relative_path)  # type: ignore[attr-defined]
        )
        if not (
            payload["status"] == "passed"
            and payload["spec"]["method_id"] == "contingent-valuation"
        ):
            return original_status(reference)
        snapshot = payload["snapshot"]
        return SimpleNamespace(
            status=payload["status"],
            code=payload["code"],
            snapshot=None
            if snapshot is None
            else SimpleNamespace(sha256=snapshot["sha256"]),
            outputs=tuple(
                SimpleNamespace(
                    name=item["name"],
                    relative_path=Path(item["relative_path"]),
                    sha256=item["sha256"],
                    size_bytes=item["size_bytes"],
                )
                for item in payload["outputs"]
            ),
        )

    return SimpleNamespace(files=service.files, status=status)  # type: ignore[attr-defined]
