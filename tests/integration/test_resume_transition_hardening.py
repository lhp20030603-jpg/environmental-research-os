"""Transactional resume-transition recovery regressions."""

from pathlib import Path

import pytest

from envresearch.kernel.engine import (
    CheckpointCorruptionError,
    RunEngine,
    SimulatedInterruption,
    TaskDefinition,
)
from envresearch.models.enums import WorkflowStatus
from envresearch.models.run import RunManifest, RunReport


@pytest.mark.parametrize(
    "failure_point",
    ["after-intermediate", "after-event", "after-running"],
)
def test_resume_transition_journal_reconciles_every_publication_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    """Retry must finish one legal transition without duplicate resume events."""
    calls: list[str] = []

    def interrupt_once() -> None:
        calls.append("task")
        if len(calls) == 1:
            raise SimulatedInterruption("first attempt")

    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(RunManifest(run_id="resume-run", benchmark_id="resume-case"))
    task = TaskDefinition("task", interrupt_once)
    with pytest.raises(SimulatedInterruption):
        engine.execute([task])

    journal = tmp_path / "recovery" / "resume-pending.json"
    if failure_point == "after-intermediate":
        original_append = engine.events.append
        failed = False

        def fail_event_once(event: object) -> None:
            nonlocal failed
            if not failed and getattr(event, "event_type", None) == "run.resumed":
                failed = True
                raise OSError("event publication failed")
            original_append(event)  # type: ignore[arg-type]

        monkeypatch.setattr(engine.events, "append", fail_event_once)
    elif failure_point == "after-event":
        original_write = engine.artifacts.write_json
        failed = False

        def fail_running_once(relative: Path, payload: dict[str, object]) -> object:
            nonlocal failed
            if (
                not failed
                and relative == Path("run-report.json")
                and payload.get("status") == "running"
            ):
                failed = True
                raise OSError("running report publication failed")
            return original_write(relative, payload)

        monkeypatch.setattr(engine.artifacts, "write_json", fail_running_once)
    else:
        original_unlink = Path.unlink
        failed = False

        def fail_cleanup_once(path: Path, missing_ok: bool = False) -> None:
            nonlocal failed
            if not failed and path == journal:
                failed = True
                raise OSError("transition cleanup failed")
            original_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", fail_cleanup_once)

    with pytest.raises(OSError):
        engine.resume([task])
    assert journal.exists()

    recovered = RunEngine.for_workspace(tmp_path)
    report = recovered.resume([task])
    events = recovered.events.read_all()

    assert report.status is WorkflowStatus.PASSED
    assert calls == ["task", "task"]
    assert not journal.exists()
    assert [event.event_type for event in events].count("run.resumed") == 1
    resumed = next(event for event in events if event.event_type == "run.resumed")
    assert (resumed.from_status, resumed.to_status) == (
        WorkflowStatus.REPAIR_PENDING,
        WorkflowStatus.RUNNING,
    )
    assert len({event.event_id for event in events}) == len(events)


def test_failed_resume_persists_intermediate_report_before_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed event append must leave the legal intermediate report durable."""
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(RunManifest(run_id="state-run", benchmark_id="state-case"))

    def interrupt() -> None:
        raise SimulatedInterruption("stop")

    task = TaskDefinition("task", interrupt)
    with pytest.raises(SimulatedInterruption):
        engine.execute([task])

    def fail_resume_event(event: object) -> None:
        if getattr(event, "event_type", None) == "run.resumed":
            raise OSError("event publication failed")
        raise AssertionError("unexpected event")

    monkeypatch.setattr(engine.events, "append", fail_resume_event)
    with pytest.raises(OSError):
        engine.resume([task])

    report = RunReport.model_validate(
        engine.artifacts.read_json(Path("run-report.json"))
    )
    assert report.status is WorkflowStatus.REPAIR_PENDING


def test_truncated_resume_event_routes_through_recoverable_corruption_outbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A torn resume event must publish one finding and retire its old journal."""
    calls: list[str] = []

    def interrupt_once() -> None:
        calls.append("task")
        if len(calls) == 1:
            raise SimulatedInterruption("first attempt")

    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(RunManifest(run_id="torn-run", benchmark_id="torn-case"))
    task = TaskDefinition("task", interrupt_once)
    with pytest.raises(SimulatedInterruption):
        engine.execute([task])

    original_append = engine.events.append

    def append_torn_event(event: object) -> None:
        if getattr(event, "event_type", None) == "run.resumed":
            with (tmp_path / "events.jsonl").open("ab") as event_file:
                event_file.write(b'{"event_id":"torn"')
            raise OSError("partial event append")
        original_append(event)  # type: ignore[arg-type]

    monkeypatch.setattr(engine.events, "append", append_torn_event)
    with pytest.raises(OSError, match="partial event append"):
        engine.resume([task])

    resume_journal = tmp_path / "recovery" / "resume-pending.json"
    corruption_journal = tmp_path / "recovery" / "corruption-pending.json"
    assert resume_journal.exists()

    recovering = RunEngine.for_workspace(tmp_path)
    original_write = recovering.artifacts.write_json
    failed = False

    def fail_rejected_report_once(relative: Path, payload: dict[str, object]) -> object:
        nonlocal failed
        if (
            not failed
            and relative == Path("run-report.json")
            and payload.get("status") == "rejected"
        ):
            failed = True
            raise OSError("corruption report publication failed")
        return original_write(relative, payload)

    monkeypatch.setattr(recovering.artifacts, "write_json", fail_rejected_report_once)
    with pytest.raises(OSError, match="corruption report publication failed"):
        recovering.resume([task])
    assert resume_journal.exists()
    assert corruption_journal.exists()

    finalized = RunEngine.for_workspace(tmp_path)
    with pytest.raises(CheckpointCorruptionError) as raised:
        finalized.resume([task])

    assert not resume_journal.exists()
    assert not corruption_journal.exists()
    report = RunReport.model_validate(
        finalized.artifacts.read_json(Path("run-report.json"))
    )
    assert report.status is WorkflowStatus.REJECTED
    assert report.findings == [raised.value.finding]
    assert raised.value.finding.code == "CHECKPOINT_CORRUPTED"

    with pytest.raises(CheckpointCorruptionError) as repeated:
        RunEngine.for_workspace(tmp_path).resume([task])
    repeated_report = RunReport.model_validate(
        finalized.artifacts.read_json(Path("run-report.json"))
    )
    assert repeated.value.finding.id == raised.value.finding.id
    assert repeated_report.findings == [raised.value.finding]
