"""Durable manifest/report identity invariant regressions."""

import json
from pathlib import Path

import pytest

from envresearch.kernel.engine import CheckpointCorruptionError, RunEngine
from envresearch.models.enums import WorkflowStatus
from envresearch.models.run import RunManifest, RunReport


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("run_id", "other-run"), ("benchmark_id", "other-benchmark")],
)
def test_resume_routes_report_identity_mismatch_through_corruption_publisher(
    tmp_path: Path, field: str, replacement: str
) -> None:
    """A parseable report for another durable identity must never be resumed."""
    manifest = RunManifest(run_id="identity-run", benchmark_id="identity-case")
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(manifest)
    engine.execute([])
    report_path = tmp_path / "run-report.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload[field] = replacement
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CheckpointCorruptionError) as raised:
        RunEngine.for_workspace(tmp_path).resume([])

    persisted = RunReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    assert raised.value.finding.code == "CHECKPOINT_CORRUPTED"
    assert field.replace("_", " ") in raised.value.finding.message
    assert persisted.run_id == manifest.run_id
    assert persisted.benchmark_id == manifest.benchmark_id
    assert persisted.status is WorkflowStatus.SUPERSEDED
