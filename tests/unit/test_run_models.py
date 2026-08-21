from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from envresearch import __version__
from envresearch.models.enums import WorkflowStatus
from envresearch.models.run import RunManifest, RunReport


def test_manifest_round_trips_versioned_pending_run() -> None:
    """A new run must preserve its identity, UTC creation time, and versions."""
    created_at = datetime(2026, 8, 4, 9, 0, tzinfo=UTC)
    manifest = RunManifest(
        run_id="run-001",
        benchmark_id="wetland-replay-v1",
        created_at=created_at,
        versions={"python": "3.11", "pack": "wetland-v1"},
    )

    restored = RunManifest.model_validate_json(manifest.model_dump_json())

    assert restored.run_id == "run-001"
    assert restored.benchmark_id == "wetland-replay-v1"
    assert restored.kernel_version == __version__
    assert restored.schema_version == "1.0"
    assert restored.created_at == created_at
    assert restored.status is WorkflowStatus.PENDING
    assert restored.versions == {"python": "3.11", "pack": "wetland-v1"}


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2026, 8, 4, 9, 0),  # noqa: DTZ001
        datetime(2026, 8, 4, 9, 0, tzinfo=timezone(timedelta(hours=8))),
    ],
)
@pytest.mark.parametrize("field_name", ["created_at", "started_at", "finished_at"])
def test_run_artifacts_reject_non_utc_times(
    field_name: str, timestamp: datetime
) -> None:
    """Naive and offset run timestamps would prevent reliable replay ordering."""

    if field_name == "created_at":
        with pytest.raises(ValidationError, match="UTC"):
            RunManifest(
                run_id="run-002",
                benchmark_id="wetland-replay-v1",
                created_at=timestamp,
            )
    else:
        report_values: dict[str, object] = {
            "run_id": "run-002",
            "benchmark_id": "wetland-replay-v1",
            "status": WorkflowStatus.RUNNING,
            "started_at": datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
        }
        report_values[field_name] = timestamp
        with pytest.raises(ValidationError, match="UTC"):
            RunReport(**report_values)


def test_run_models_allow_collection_updates() -> None:
    """Run collections remain mutable for incremental workflow reporting."""
    manifest = RunManifest(run_id="run-003", benchmark_id="wetland-replay-v1")
    report = RunReport(
        run_id="run-003",
        benchmark_id="wetland-replay-v1",
        status=WorkflowStatus.RUNNING,
        started_at=datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
    )

    manifest.versions["pack"] = "wetland-v1"
    report.completed_tasks.append("ingest")
    report.output_comparisons.append({"name": "baseline"})

    assert manifest.versions == {"pack": "wetland-v1"}
    assert report.completed_tasks == ["ingest"]
    assert report.output_comparisons == [{"name": "baseline"}]
