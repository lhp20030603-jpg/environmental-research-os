"""Integration tests for checkpoint-driven workflow recovery."""

import json
import os
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path

import pytest

from envresearch.kernel.engine import (
    CheckpointCorruptionError,
    RunEngine,
    SimulatedInterruption,
    TaskDefinition,
)
from envresearch.models.enums import FindingSeverity
from envresearch.models.run import RunManifest


def _manifest(run_id: str = "run-001") -> RunManifest:
    return RunManifest(run_id=run_id, benchmark_id="recovery-case")


def _interrupt_once(calls: list[str]) -> Callable[[], None]:
    def interrupt() -> None:
        calls.append("second")
        if calls.count("second") == 1:
            raise SimulatedInterruption("forced test interruption")

    return interrupt


def _interrupted_engine(
    workspace: Path,
) -> tuple[RunEngine, list[str], list[TaskDefinition]]:
    calls: list[str] = []

    def first() -> None:
        calls.append("first")

    engine = RunEngine.for_workspace(workspace)
    engine.initialize(_manifest())
    tasks = [
        TaskDefinition("first", first, version="interrupt-v1"),
        TaskDefinition("second", _interrupt_once(calls), version="interrupt-v1"),
    ]
    with pytest.raises(SimulatedInterruption, match="forced"):
        engine.execute(tasks)
    return engine, calls, tasks


def test_resume_skips_completed_task_and_restarts_interrupted_task(tmp_path: Path) -> None:
    calls: list[str] = []

    def first() -> None:
        calls.append("first")

    def second() -> None:
        calls.append("second")
        if calls.count("second") == 1:
            raise SimulatedInterruption("forced test interruption")

    engine = RunEngine.for_workspace(tmp_path)
    tasks = [TaskDefinition("first", first), TaskDefinition("second", second)]

    with pytest.raises(SimulatedInterruption):
        engine.execute(tasks)
    engine.resume(tasks)

    assert calls == ["first", "second", "second"]


def test_repeated_resume_of_passed_run_is_idempotent(tmp_path: Path) -> None:
    """A verified passed run must not re-execute tasks or append events."""
    engine, calls, tasks = _interrupted_engine(tmp_path)
    report = engine.resume(tasks)
    events = engine.events.read_all()

    repeated = engine.resume(tasks)

    assert calls == ["first", "second", "second"]
    assert repeated == report
    assert engine.events.read_all() == events


def test_failed_task_emits_failure_without_checkpoint(tmp_path: Path) -> None:
    """Checkpointing a failed task would cause resume to skip unfinished work."""
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(_manifest())

    def fail() -> None:
        raise RuntimeError("task failed")

    with pytest.raises(RuntimeError, match="task failed"):
        engine.execute([TaskDefinition("broken", fail)])

    assert not (tmp_path / "checkpoints" / "broken.json").exists()
    assert [event.event_type for event in engine.events.read_all()] == [
        "task.started",
        "task.failed",
    ]


@pytest.mark.parametrize("damage", ["altered", "missing"])
def test_resume_rejects_altered_or_missing_associated_artifact(
    tmp_path: Path, damage: str
) -> None:
    """Skipping artifact verification would trust stale completed work."""
    output = tmp_path / "derived" / "result.txt"

    def produce() -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("verified", encoding="utf-8")

    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(_manifest())
    tasks = [
        TaskDefinition("produce", produce, artifact_paths=(Path("derived/result.txt"),))
    ]
    engine.execute(tasks)
    if damage == "altered":
        output.write_text("tampered", encoding="utf-8")
    else:
        output.unlink()

    with pytest.raises(CheckpointCorruptionError) as raised:
        engine.resume(tasks)

    finding = raised.value.finding
    assert finding.code == "CHECKPOINT_CORRUPTED"
    assert finding.severity is FindingSeverity.CRITICAL
    assert json.loads(
        (tmp_path / "findings" / f"{finding.id}.json").read_text(encoding="utf-8")
    )["code"] == "CHECKPOINT_CORRUPTED"
    persisted_report = json.loads(
        (tmp_path / "run-report.json").read_text(encoding="utf-8")
    )
    assert persisted_report["findings"][0]["id"] == finding.id


@pytest.mark.parametrize(
    "mutate",
    [
        lambda checkpoint: checkpoint.pop("completed_at"),
        lambda checkpoint: checkpoint.pop("status"),
        lambda checkpoint: checkpoint.pop("schema_version"),
        lambda checkpoint: checkpoint.pop("artifact_hashes"),
        lambda checkpoint: checkpoint.update({"status": "failed"}),
        lambda checkpoint: checkpoint.update({"artifact_hashes": []}),
        lambda checkpoint: checkpoint.update(
            {"completed_at": "2026-08-04T12:00:00+00:00"}
        ),
    ],
    ids=[
        "missing-timestamp",
        "missing-status",
        "missing-schema",
        "missing-artifacts",
        "wrong-status",
        "wrong-field-type",
        "altered-json",
    ],
)
def test_resume_rejects_malformed_or_altered_checkpoint(
    tmp_path: Path, mutate: Callable[[dict[str, object]], object]
) -> None:
    """Weak checkpoint validation would let incomplete or edited state pass."""
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(_manifest())
    tasks = [TaskDefinition("done", lambda: None)]
    engine.execute(tasks)
    path = tmp_path / "checkpoints" / "done.json"
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    mutate(checkpoint)
    path.write_text(json.dumps(checkpoint), encoding="utf-8")

    with pytest.raises(CheckpointCorruptionError) as raised:
        engine.resume(tasks)

    assert raised.value.finding.code == "CHECKPOINT_CORRUPTED"


def test_resume_rejects_invalid_checkpoint_json(tmp_path: Path) -> None:
    """Truncated checkpoint JSON must not be mistaken for incomplete work."""
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(_manifest())
    tasks = [TaskDefinition("done", lambda: None)]
    engine.execute(tasks)
    (tmp_path / "checkpoints" / "done.json").write_text(
        '{"task_id":"done"', encoding="utf-8"
    )

    with pytest.raises(CheckpointCorruptionError):
        engine.resume(tasks)


def test_resume_rejects_non_utf8_checkpoint_bytes(tmp_path: Path) -> None:
    """Unreadable checkpoint bytes must still persist the corruption finding."""
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(_manifest())
    tasks = [TaskDefinition("done", lambda: None)]
    engine.execute(tasks)
    (tmp_path / "checkpoints" / "done.json").write_bytes(b"\xff\xfe")

    with pytest.raises(CheckpointCorruptionError):
        engine.resume(tasks)


def test_pass_event_anchors_checkpoint_hash_against_rehashed_edits(
    tmp_path: Path,
) -> None:
    """A self-consistent replacement checkpoint must not replace recorded history."""
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(_manifest())
    tasks = [TaskDefinition("done", lambda: None)]
    engine.execute(tasks)
    path = tmp_path / "checkpoints" / "done.json"
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    checkpoint["completed_at"] = "2026-08-04T12:00:00Z"
    core = {key: value for key, value in checkpoint.items() if key != "checkpoint_hash"}
    checkpoint["checkpoint_hash"] = sha256(
        json.dumps(
            core, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()
    path.write_text(json.dumps(checkpoint), encoding="utf-8")

    with pytest.raises(CheckpointCorruptionError):
        engine.resume(tasks)


def test_resume_rejects_missing_checkpoint_for_recorded_pass(tmp_path: Path) -> None:
    """A deleted checkpoint must not cause a previously passed task to rerun."""
    engine, calls, tasks = _interrupted_engine(tmp_path)
    (tmp_path / "checkpoints" / "first.json").unlink()

    with pytest.raises(CheckpointCorruptionError):
        engine.resume(tasks)

    assert calls == ["first", "second"]


@pytest.mark.parametrize("drift", ["order", "definition"])
def test_resume_rejects_task_order_or_definition_drift(
    tmp_path: Path, drift: str
) -> None:
    """A checkpoint from a different task plan must never authorize skipping."""
    engine, calls, tasks = _interrupted_engine(tmp_path)
    if drift == "order":
        changed = list(reversed(tasks))
    else:
        changed = [
            TaskDefinition("first", tasks[0].action, version="2"),
            tasks[1],
        ]

    with pytest.raises(CheckpointCorruptionError):
        engine.resume(changed)

    assert calls == ["first", "second"]


def test_resume_rejects_plan_drift_before_first_checkpoint(tmp_path: Path) -> None:
    """An interruption before task one passes must still pin the original plan."""
    calls: list[str] = []
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(_manifest())

    def interrupt() -> None:
        calls.append("original")
        raise SimulatedInterruption("stop before checkpoint")

    with pytest.raises(SimulatedInterruption):
        engine.execute([TaskDefinition("first", interrupt, version="plan-v1")])

    with pytest.raises(CheckpointCorruptionError) as raised:
        engine.resume(
            [
                TaskDefinition(
                    "replacement",
                    lambda: calls.append("replacement"),
                    version="plan-v1",
                )
            ]
        )

    assert calls == ["original"]
    assert raised.value.finding.evidence[0] == "task-plan.json"


def test_atomic_plan_is_pinned_before_first_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure to append the first event must not leave an unbound running plan."""
    calls: list[str] = []
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(_manifest())
    original_append = engine.events.append

    def fail_first_event(*args: object) -> None:
        monkeypatch.setattr(engine.events, "append", original_append)
        raise OSError("event append failed")

    monkeypatch.setattr(engine.events, "append", fail_first_event)
    with pytest.raises(OSError, match="event append failed"):
        engine.execute(
            [
                TaskDefinition(
                    "original",
                    lambda: calls.append("original"),
                    version="plan-v1",
                )
            ]
        )

    assert (tmp_path / "task-plan.json").exists()
    with pytest.raises(CheckpointCorruptionError):
        engine.resume(
            [
                TaskDefinition(
                    "replacement",
                    lambda: calls.append("replacement"),
                    version="plan-v1",
                )
            ]
        )
    assert calls == []


def test_duplicate_task_ids_are_rejected_before_execution(tmp_path: Path) -> None:
    """Duplicate IDs would alias two definitions onto one checkpoint path."""
    calls: list[str] = []
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(_manifest())
    tasks = [
        TaskDefinition("same", lambda: calls.append("one")),
        TaskDefinition("same", lambda: calls.append("two")),
    ]

    with pytest.raises(ValueError, match="duplicate task ID: same"):
        engine.execute(tasks)

    assert calls == []
    assert engine.events.read_all() == []


def test_checkpoint_replace_failure_leaves_no_partial_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed checkpoint replacement must remain journaled and resumable."""
    original_replace = os.replace
    failed = False

    def fail_checkpoint_replace(source: str | Path, destination: str | Path) -> None:
        nonlocal failed
        if Path(destination).name == "atomic.json" and not failed:
            failed = True
            raise OSError("checkpoint replace failed")
        original_replace(source, destination)

    monkeypatch.setattr("envresearch.storage.atomic.os.replace", fail_checkpoint_replace)
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(_manifest())
    calls: list[str] = []
    task = TaskDefinition("atomic", lambda: calls.append("atomic"), version="v1")

    with pytest.raises(OSError, match="checkpoint replace failed"):
        engine.execute([task])

    assert not (tmp_path / "checkpoints" / "atomic.json").exists()
    assert list((tmp_path / "checkpoints").glob(".atomic.json.*")) == []
    assert [event.event_type for event in engine.events.read_all()] == ["task.started"]

    report = RunEngine.for_workspace(tmp_path).resume([task])

    assert calls == ["atomic"]
    assert report.completed_tasks == ["atomic"]
    assert (tmp_path / "checkpoints" / "atomic.json").is_file()
