"""Hardening regressions for durable workflow recovery."""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import partial
from pathlib import Path

import pytest

from envresearch.kernel.engine import (
    CheckpointCorruptionError,
    RunEngine,
    SimulatedInterruption,
    TaskDefinition,
)
from envresearch.kernel.task_identity import (
    definition_hash,
    plan_description,
    plan_hash,
)
from envresearch.models.enums import FindingSeverity, WorkflowStatus
from envresearch.models.run import RunManifest, RunReport


def _manifest(run_id: str = "hardening-run") -> RunManifest:
    return RunManifest(run_id=run_id, benchmark_id="hardening-case")


def _rehash_json_payload(path: Path, **updates: object) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(updates)
    hash_field = "record_hash"
    core = {key: value for key, value in payload.items() if key != hash_field}
    import hashlib

    payload[hash_field] = hashlib.sha256(
        json.dumps(
            core, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_external_plan_anchor_rejects_rehashed_plan_after_first_event_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A self-consistent replacement plan must not bypass a pre-event anchor."""
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(_manifest())
    original_append = engine.events.append

    def fail_first_event(*args: object) -> None:
        monkeypatch.setattr(engine.events, "append", original_append)
        raise OSError("event append failed")

    monkeypatch.setattr(engine.events, "append", fail_first_event)
    original = [TaskDefinition("original", lambda: None)]
    with pytest.raises(OSError, match="event append failed"):
        engine.execute(original)

    replacement = [TaskDefinition("replacement", lambda: None)]
    replacement_hash = plan_hash(tuple(replacement))
    replacement_description = plan_description(tuple(replacement))
    _rehash_json_payload(
        tmp_path / "task-plan.json",
        plan_hash=replacement_hash,
        tasks=replacement_description,
    )

    with pytest.raises(CheckpointCorruptionError, match="plan"):
        engine.resume(replacement)


@pytest.mark.parametrize("failure_target", ["anchor", "running-report"])
def test_execute_reconciles_plan_publication_partial_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_target: str,
) -> None:
    """Retry must safely finish a plan/anchor/report publication interrupted once."""
    calls: list[str] = []
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(_manifest())
    original_write = engine.artifacts.write_json
    failed = False

    def fail_once(relative: Path, payload: dict[str, object]) -> object:
        nonlocal failed
        is_target = (
            failure_target == "anchor" and relative == Path("task-plan-anchor.json")
        ) or (
            failure_target == "running-report"
            and relative == Path("run-report.json")
            and payload.get("status") == "running"
        )
        if is_target and not failed:
            failed = True
            raise OSError(f"{failure_target} write failed")
        return original_write(relative, payload)

    monkeypatch.setattr(engine.artifacts, "write_json", fail_once)
    task = TaskDefinition("once", lambda: calls.append("once"), version="v1")
    with pytest.raises(OSError, match="write failed"):
        engine.execute([task])

    report = engine.execute([task])

    assert report.completed_tasks == ["once"]
    assert calls == ["once"]


def _append_value(values: list[str], value: str) -> None:
    values.append(value)


class _BoundWorker:
    def __init__(self) -> None:
        self.values: list[str] = []

    def run(self) -> None:
        self.values.append("run")


class _CallableWorker:
    def __init__(self) -> None:
        self.values: list[str] = []

    def __call__(self) -> None:
        self.values.append("call")


@pytest.mark.parametrize(
    "action",
    [
        partial(_append_value, [], "partial"),
        _BoundWorker().run,
        _CallableWorker(),
    ],
    ids=["mutable-partial", "bound-method", "callable-instance"],
)
def test_json_like_callable_state_is_snapshotted(
    action: Callable[[], object],
) -> None:
    """Supported partial, bound, and callable state keeps its captured identity."""
    task = TaskDefinition("stateful", action)
    before = definition_hash(task)

    action()

    assert definition_hash(task) == before


def test_mutable_closure_keeps_its_construction_identity(tmp_path: Path) -> None:
    """A JSON-like closure may mutate without changing the persisted plan."""
    values: list[str] = []

    def mutate() -> None:
        values.append("changed")

    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(_manifest())

    task = TaskDefinition("closure", mutate)
    engine.execute([task])
    engine.resume([task])

    assert values == ["changed"]


def test_explicit_identity_allows_stateful_callback(tmp_path: Path) -> None:
    """Callers can explicitly own identity changes for mutable callback state."""
    values: list[str] = []
    task = TaskDefinition(
        "stateful",
        partial(_append_value, values, "called"),
        version="state-v1",
    )
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(_manifest())

    engine.execute([task])
    engine.resume([task])

    assert values == ["called"]


def test_resume_uses_only_fixed_event_vocabulary(tmp_path: Path) -> None:
    """Resume must not invent an event for its persisted intermediate state."""
    calls: list[str] = []

    def interrupt_once() -> None:
        calls.append("task")
        if len(calls) == 1:
            raise SimulatedInterruption("first attempt")

    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(_manifest())
    task = TaskDefinition("task", interrupt_once, version="interrupt-v1")
    with pytest.raises(SimulatedInterruption):
        engine.execute([task])

    engine.resume([task])
    events = engine.events.read_all()

    assert [event.event_type for event in events] == [
        "task.started",
        "run.interrupted",
        "run.resumed",
        "task.started",
        "task.passed",
    ]
    assert (events[2].from_status, events[2].to_status) == (
        WorkflowStatus.REPAIR_PENDING,
        WorkflowStatus.RUNNING,
    )


def _completed_artifact_run(tmp_path: Path) -> tuple[RunEngine, list[TaskDefinition]]:
    output = tmp_path / "derived" / "result.txt"

    def produce() -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("verified", encoding="utf-8")

    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(_manifest())
    tasks = [
        TaskDefinition(
            "produce",
            produce,
            artifact_paths=(Path("derived/result.txt"),),
        )
    ]
    engine.execute(tasks)
    output.write_text("tampered", encoding="utf-8")
    return engine, tasks


def test_passed_run_corruption_uses_superseded_invalidation(tmp_path: Path) -> None:
    """A passed run is superseded without inventing an invalidation event."""
    engine, tasks = _completed_artifact_run(tmp_path)
    before = engine.events.read_all()

    with pytest.raises(CheckpointCorruptionError) as raised:
        engine.resume(tasks)

    report = RunReport.model_validate(engine.artifacts.read_json(Path("run-report.json")))
    assert report.status is WorkflowStatus.SUPERSEDED
    assert report.findings == [raised.value.finding]
    assert engine.events.read_all() == before


@pytest.mark.parametrize("corrupt_bytes", [b'{"truncated"', b"\xff\xfe"])
def test_corrupt_event_log_still_persists_critical_finding(
    tmp_path: Path, corrupt_bytes: bytes
) -> None:
    """Unreadable audit history must enter the same durable corruption pathway."""
    engine, tasks = _completed_artifact_run(tmp_path)
    with (tmp_path / "events.jsonl").open("ab") as event_file:
        event_file.write(corrupt_bytes)

    with pytest.raises(CheckpointCorruptionError) as raised:
        engine.resume(tasks)

    assert raised.value.finding.code == "CHECKPOINT_CORRUPTED"
    assert raised.value.finding.severity is FindingSeverity.CRITICAL
    assert any("event log" in evidence for evidence in raised.value.finding.evidence)
    report = RunReport.model_validate(engine.artifacts.read_json(Path("run-report.json")))
    assert report.status is WorkflowStatus.SUPERSEDED


@pytest.mark.parametrize("failure_point", ["report", "cleanup"])
def test_corruption_publication_journal_reconciles_partial_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    """A crash after either publication write must be idempotently recoverable."""
    engine, tasks = _completed_artifact_run(tmp_path)
    journal_path = tmp_path / "recovery" / "corruption-pending.json"
    if failure_point == "report":
        original_write = engine.artifacts.write_json
        failed = False

        def fail_report_once(relative: Path, payload: dict[str, object]) -> object:
            nonlocal failed
            if (
                not failed
                and relative == Path("run-report.json")
                and payload.get("status") == "superseded"
            ):
                failed = True
                raise OSError("report publication failed")
            return original_write(relative, payload)

        monkeypatch.setattr(engine.artifacts, "write_json", fail_report_once)
    else:
        original_unlink = Path.unlink
        failed = False

        def fail_cleanup_once(path: Path, missing_ok: bool = False) -> None:
            nonlocal failed
            if path == journal_path and not failed:
                failed = True
                raise OSError("journal cleanup failed")
            original_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", fail_cleanup_once)

    with pytest.raises(OSError):
        engine.resume(tasks)
    assert journal_path.exists()

    recovered = RunEngine.for_workspace(tmp_path)
    with pytest.raises(CheckpointCorruptionError) as raised:
        recovered.resume(tasks)

    assert not journal_path.exists()
    report = RunReport.model_validate(
        recovered.artifacts.read_json(Path("run-report.json"))
    )
    assert report.status is WorkflowStatus.SUPERSEDED
    assert [finding.id for finding in report.findings] == [raised.value.finding.id]
