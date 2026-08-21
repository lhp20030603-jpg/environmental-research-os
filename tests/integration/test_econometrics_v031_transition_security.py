"""Adversarial exact-authority coverage for the V0.3.1 transition."""

from __future__ import annotations

import os
import shutil
from multiprocessing import get_context
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from envresearch.econometrics.exit_evaluator import ValuationExitEvaluator
from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.valuation_transition import (
    TRANSITION_SUBJECT,
    V031ExitHarness,
    V031TransitionMarker,
    _StatusOnlyBackend,
    accepted_analysis_reports,
    publish_v031_transition,
)
from envresearch.models.artifact import ArtifactRef


def _sealed_root() -> Path:
    value = os.getenv("ENVRESEARCH_V031_ACCEPTANCE_ROOT")
    if value is None:
        pytest.skip("set ENVRESEARCH_V031_ACCEPTANCE_ROOT for transition security")
    return Path(value).resolve(strict=True)


def _copy_root(tmp_path: Path) -> Path:
    target = (tmp_path / "sealed-copy").resolve()
    shutil.copytree(_sealed_root(), target)
    return target


def _ref(name: str) -> ArtifactRef:
    return ArtifactRef(artifact_id=name, artifact_version=1, content_hash="a" * 64)


def _marker_payload(pack_root: Path) -> dict[str, object]:
    return {
        "schema_version": "econometrics.v031-transition.v1",
        "release": "V0.3.1",
        "status": "passed",
        "input_contract": "local-analysis-report+artifact-reference.v1",
        "manifest_ref": _ref("manifest"),
        "run_ref": _ref("run"),
        "catalog_binding_ref": _ref("binding"),
        "catalog_ref": _ref("catalog"),
        "report_ref": _ref("report"),
        "runtime_relative_path": Path("reviewed/Rscript"),
        "runtime_sha256": "b" * 64,
        "frozen_pack_root": pack_root,
        "frozen_pack_hash": "c" * 64,
    }


def _publish_distinct_worker(
    root_value: str,
    runtime_value: str,
    marker_payload: dict[str, Any],
    queue: Any,
) -> None:
    try:
        marker = V031TransitionMarker.model_validate(marker_payload)
        result = publish_v031_transition(
            Path(root_value),
            manifest_ref=marker.manifest_ref,
            run_ref=marker.run_ref,
            catalog_binding_ref=marker.catalog_binding_ref,
            catalog_ref=marker.catalog_ref,
            report_ref=marker.report_ref,
            runtime_relative_path=Path(runtime_value),
            runtime_sha256=marker.runtime_sha256,
            frozen_pack_root=marker.frozen_pack_root,
            frozen_pack_hash=marker.frozen_pack_hash,
        )
        queue.put(("ok", result.model_dump_json()))
    except Exception as error:  # noqa: BLE001 - child reports exact contention result
        queue.put(("error", f"{type(error).__name__}: {error}"))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("runtime_sha256", "ABC", "lowercase SHA-256"),
        ("frozen_pack_hash", "0" * 63, "lowercase SHA-256"),
        ("runtime_relative_path", Path("/absolute/Rscript"), "canonical and relative"),
        (
            "runtime_relative_path",
            Path("reviewed/../Rscript"),
            "canonical and relative",
        ),
        ("frozen_pack_root", Path("relative-pack"), "absolute and non-symlink"),
    ),
)
def test_transition_marker_rejects_ambiguous_authority(
    field: str, value: object, message: str, tmp_path: Path
) -> None:
    payload = _marker_payload(tmp_path.resolve())
    payload[field] = value

    with pytest.raises(ValidationError, match=message):
        V031TransitionMarker.model_validate(payload)


def test_transition_marker_rejects_symlinked_pack(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    link = tmp_path / "pack-link"
    link.symlink_to(pack, target_is_directory=True)

    with pytest.raises(ValidationError, match="absolute and non-symlink"):
        V031TransitionMarker.model_validate(_marker_payload(link.absolute()))


def test_transition_status_backend_cannot_execute() -> None:
    backend = _StatusOnlyBackend(())
    assert backend.package_authorities == ()

    with pytest.raises(RuntimeError, match="read-only"):
        backend.execute()


def test_transition_harness_requires_absolute_non_symlink_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute and non-symlink"):
        V031ExitHarness(Path("relative"))
    link = tmp_path / "root-link"
    link.symlink_to(_sealed_root(), target_is_directory=True)
    with pytest.raises(ValueError, match="absolute and non-symlink"):
        V031ExitHarness(link.absolute())


def test_transition_harness_has_no_public_noncurrent_escape() -> None:
    harness = V031ExitHarness(_sealed_root())

    with pytest.raises(TypeError, match="unexpected keyword argument"):
        V031ExitHarness(
            _sealed_root(), marker_ref=harness.marker_ref, require_current=False
        )


def test_transition_harness_uses_single_registry_authority(tmp_path: Path) -> None:
    root = _copy_root(tmp_path)
    pointer = root / "transition-reference.json"
    pointer.write_text(_ref("forged-transition").model_dump_json(), encoding="utf-8")

    assert V031ExitHarness(root).run_and_evaluate().status == "passed"


def test_transition_rechecks_current_after_harness_construction(tmp_path: Path) -> None:
    root = _copy_root(tmp_path)
    harness = V031ExitHarness(root)
    pointer = root / "evaluator/exit/current/valuation-transition-v031.json"
    pointer.chmod(0o600)
    pointer.write_text(_ref("forged-transition").model_dump_json(), encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        harness.run_and_evaluate()


def test_transition_reauthenticates_marker_after_harness_construction(
    tmp_path: Path,
) -> None:
    root = _copy_root(tmp_path)
    harness = V031ExitHarness(root)
    objects = root / "evaluator/exit/objects/valuation-transition-v031"
    marker_path = next(objects.glob("*.json"))
    marker_path.chmod(0o600)
    marker_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError):
        harness.run_and_evaluate()


def test_transition_reauthenticates_runtime_after_harness_construction(
    tmp_path: Path,
) -> None:
    root = _copy_root(tmp_path)
    harness = V031ExitHarness(root)
    runtime = root / harness.marker.runtime_relative_path
    runtime.chmod(0o700)
    runtime.write_bytes(runtime.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="runtime identity changed"):
        harness.run_and_evaluate()


def test_transition_harness_rejects_runtime_mutation(tmp_path: Path) -> None:
    root = _copy_root(tmp_path)
    runtime = root / "reviewed/Rscript"
    runtime.chmod(0o700)
    runtime.write_bytes(runtime.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="runtime identity changed"):
        V031ExitHarness(root)


def test_transition_harness_rejects_non_nine_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = V031ExitHarness(_sealed_root())
    actual = harness.run_and_evaluate()
    shortened = actual.model_copy(update={"outcomes": actual.outcomes[:-1]})
    monkeypatch.setattr(ValuationExitEvaluator, "status", lambda *args: shortened)

    with pytest.raises(ValueError, match="9/9 passed"):
        harness.run_and_evaluate()


def test_transition_publish_is_idempotent_and_exposes_only_green_reports() -> None:
    root = _sealed_root()
    harness = V031ExitHarness(root)
    marker = harness.marker

    reference = publish_v031_transition(
        root,
        manifest_ref=marker.manifest_ref,
        run_ref=marker.run_ref,
        catalog_binding_ref=marker.catalog_binding_ref,
        catalog_ref=marker.catalog_ref,
        report_ref=marker.report_ref,
        runtime_relative_path=marker.runtime_relative_path,
        runtime_sha256=marker.runtime_sha256,
        frozen_pack_root=marker.frozen_pack_root,
        frozen_pack_hash=marker.frozen_pack_hash,
    )
    reports = accepted_analysis_reports(V031ExitHarness(root))

    assert reference == harness.marker_ref
    assert (
        ExitRegistry(root / "evaluator", create=False).current(TRANSITION_SUBJECT)
        == reference
    )
    assert len(reports) == 4
    assert all(report.status == "passed" for _, report in reports)


def test_transition_publish_validates_before_promoting_current(tmp_path: Path) -> None:
    root = _copy_root(tmp_path)
    harness = V031ExitHarness(root)
    marker = harness.marker
    pointer = root / "evaluator/exit/current/valuation-transition-v031.json"
    pointer.chmod(0o600)
    pointer.unlink()

    with pytest.raises(ValueError, match="runtime identity changed"):
        publish_v031_transition(
            root,
            manifest_ref=marker.manifest_ref,
            run_ref=marker.run_ref,
            catalog_binding_ref=marker.catalog_binding_ref,
            catalog_ref=marker.catalog_ref,
            report_ref=marker.report_ref,
            runtime_relative_path=marker.runtime_relative_path,
            runtime_sha256="0" * 64,
            frozen_pack_root=marker.frozen_pack_root,
            frozen_pack_hash=marker.frozen_pack_hash,
        )

    assert (
        ExitRegistry(root / "evaluator", create=False).current(TRANSITION_SUBJECT)
        is None
    )
    with pytest.raises(ValueError, match="not sealed"):
        V031ExitHarness(root)


def test_transition_rejects_a_second_distinct_seal(tmp_path: Path) -> None:
    root = _copy_root(tmp_path)
    harness = V031ExitHarness(root)
    marker = harness.marker
    second_runtime = root / "reviewed/Rscript-copy"
    second_runtime.write_bytes((root / marker.runtime_relative_path).read_bytes())
    second_runtime.chmod(0o500)

    with pytest.raises(ValueError, match="already sealed"):
        publish_v031_transition(
            root,
            manifest_ref=marker.manifest_ref,
            run_ref=marker.run_ref,
            catalog_binding_ref=marker.catalog_binding_ref,
            catalog_ref=marker.catalog_ref,
            report_ref=marker.report_ref,
            runtime_relative_path=Path("reviewed/Rscript-copy"),
            runtime_sha256=marker.runtime_sha256,
            frozen_pack_root=marker.frozen_pack_root,
            frozen_pack_hash=marker.frozen_pack_hash,
        )


def test_two_process_distinct_publishers_leave_one_current_winner(
    tmp_path: Path,
) -> None:
    root = _copy_root(tmp_path)
    harness = V031ExitHarness(root)
    marker = harness.marker
    current = root / "evaluator/exit/current/valuation-transition-v031.json"
    current.chmod(0o600)
    current.unlink()
    for name in ("Rscript-a", "Rscript-b"):
        target = root / "reviewed" / name
        target.write_bytes((root / marker.runtime_relative_path).read_bytes())
        target.chmod(0o500)

    context = get_context("spawn")
    queue = context.Queue()
    payload = marker.model_dump()
    processes = tuple(
        context.Process(
            target=_publish_distinct_worker,
            args=(str(root), f"reviewed/{name}", payload, queue),
        )
        for name in ("Rscript-a", "Rscript-b")
    )
    for process in processes:
        process.start()
    outcomes = tuple(queue.get(timeout=60) for _ in processes)
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0

    winners = tuple(value for status, value in outcomes if status == "ok")
    errors = tuple(value for status, value in outcomes if status == "error")
    assert len(winners) == 1
    assert len(errors) == 1 and "already sealed" in errors[0]
    winner = ArtifactRef.model_validate_json(winners[0])
    assert (
        ExitRegistry(root / "evaluator", create=False).current(TRANSITION_SUBJECT)
        == winner
    )
    assert V031ExitHarness(root).run_and_evaluate().status == "passed"
