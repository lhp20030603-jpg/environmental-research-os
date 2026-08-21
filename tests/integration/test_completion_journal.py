"""Crash-consistency regressions for per-task completion publication."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from envresearch.kernel.engine import RunEngine, TaskDefinition
from envresearch.models.enums import WorkflowStatus
from envresearch.models.run import RunManifest, RunReport


def test_fresh_resume_repairs_pass_event_without_rerunning_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pass-event append failure must remain a recoverable partial publication."""
    calls: list[str] = []
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(
        RunManifest(run_id="completion-run", benchmark_id="completion-case")
    )
    task = TaskDefinition("once", lambda: calls.append("once"), version="v1")
    original_append = engine.events.append
    failed = False

    def fail_pass_once(event: object) -> None:
        nonlocal failed
        if not failed and getattr(event, "event_type", None) == "task.passed":
            failed = True
            raise OSError("pass event publication failed")
        original_append(event)  # type: ignore[arg-type]

    monkeypatch.setattr(engine.events, "append", fail_pass_once)

    with pytest.raises(OSError, match="pass event publication failed"):
        engine.execute([task])

    checkpoint = engine.artifacts.read_json(Path("checkpoints/once.json"))
    recovered = RunEngine.for_workspace(tmp_path)
    report = recovered.resume([task])
    pass_events = [
        event for event in recovered.events.read_all() if event.event_type == "task.passed"
    ]

    assert calls == ["once"]
    assert report.status is WorkflowStatus.PASSED
    assert report.completed_tasks == ["once"]
    assert len(pass_events) == 1
    assert pass_events[0].payload["checkpoint_hash"] == checkpoint["checkpoint_hash"]
    assert not (tmp_path / "recovery" / "task-completion-pending.json").exists()


@pytest.mark.parametrize("failure_point", ["report", "cleanup"])
def test_fresh_resume_reconciles_later_completion_publication_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    """Event, report, and cleanup retries must remain exact-once publications."""
    calls: list[str] = []
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(
        RunManifest(run_id="completion-run", benchmark_id="completion-case")
    )
    task = TaskDefinition("once", lambda: calls.append("once"), version="v1")
    journal = tmp_path / "recovery" / "task-completion-pending.json"
    failed = False
    if failure_point == "report":
        original_write = engine.artifacts.write_json

        def fail_report_once(relative: Path, payload: dict[str, object]) -> object:
            nonlocal failed
            if (
                not failed
                and relative == Path("run-report.json")
                and payload.get("completed_tasks") == ["once"]
            ):
                failed = True
                raise OSError("completion report publication failed")
            return original_write(relative, payload)

        monkeypatch.setattr(engine.artifacts, "write_json", fail_report_once)
    else:
        original_unlink = Path.unlink

        def fail_cleanup_once(path: Path, missing_ok: bool = False) -> None:
            nonlocal failed
            if not failed and path == journal:
                failed = True
                raise OSError("completion journal cleanup failed")
            original_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", fail_cleanup_once)

    with pytest.raises(OSError, match="completion"):
        engine.execute([task])
    assert journal.exists()

    recovered = RunEngine.for_workspace(tmp_path)
    report = recovered.resume([task])

    assert calls == ["once"]
    assert report.status is WorkflowStatus.PASSED
    assert report.completed_tasks == ["once"]
    assert [event.event_type for event in recovered.events.read_all()].count(
        "task.passed"
    ) == 1
    assert not journal.exists()


def test_stale_completion_journal_accepts_a_later_durable_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replayed journal deletion must not roll back subsequent run progress."""
    calls: list[str] = []
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(
        RunManifest(run_id="completion-run", benchmark_id="completion-case")
    )
    task = TaskDefinition("once", lambda: calls.append("once"), version="v1")
    journal = tmp_path / "recovery" / "task-completion-pending.json"
    original_unlink = Path.unlink
    failed = False

    def fail_cleanup_once(path: Path, missing_ok: bool = False) -> None:
        nonlocal failed
        if not failed and path == journal:
            failed = True
            raise OSError("completion journal cleanup failed")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_cleanup_once)
    with pytest.raises(OSError):
        engine.execute([task])

    completed = RunReport.model_validate(
        engine.artifacts.read_json(Path("run-report.json"))
    )
    advanced = completed.model_copy(
        update={
            "status": WorkflowStatus.PASSED,
            "finished_at": datetime(2026, 8, 5, tzinfo=UTC),
        }
    )
    engine.artifacts.write_json(
        Path("run-report.json"), advanced.model_dump(mode="json")
    )

    report = RunEngine.for_workspace(tmp_path).resume([task])

    assert report == advanced
    assert calls == ["once"]
    assert not journal.exists()
