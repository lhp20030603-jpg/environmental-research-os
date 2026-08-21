"""Process locking, gate, and command execution integration tests."""

from __future__ import annotations

import multiprocessing
import sys
import time
from functools import partial
from pathlib import Path
from typing import Protocol

import pytest

from envresearch.kernel.engine import (
    RunEngine,
    SimulatedInterruption,
    TaskCommandError,
    TaskDefinition,
)
from envresearch.kernel.gates import GateDecision, GateRequest
from envresearch.models.benchmark import CommandSpec
from envresearch.models.enums import GateStatus, WorkflowStatus
from envresearch.models.run import RunManifest
from envresearch.runner.command import CommandRunner


def _manifest() -> RunManifest:
    return RunManifest(run_id="execution-run", benchmark_id="execution-case")


def _interrupt_then_append(workspace: Path) -> None:
    marker = workspace / "interrupted.marker"
    if not marker.exists():
        marker.write_text("interrupted", encoding="utf-8")
        raise SimulatedInterruption("prepare concurrent resume")
    with (workspace / "calls.log").open("a", encoding="utf-8") as calls:
        calls.write("called\n")
        calls.flush()
        time.sleep(0.3)


class _ResultQueue(Protocol):
    def put(self, value: str) -> None: ...


def _resume_worker(workspace_text: str, results: _ResultQueue) -> None:
    workspace = Path(workspace_text)
    task = TaskDefinition(
        "locked",
        partial(_interrupt_then_append, workspace),
        version="lock-v1",
    )
    RunEngine.for_workspace(workspace).resume([task])
    results.put("ok")


def test_concurrent_processes_execute_resumed_task_once(tmp_path: Path) -> None:
    """A workspace lock serializes verification through checkpoint publication."""
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(_manifest())
    task = TaskDefinition(
        "locked",
        partial(_interrupt_then_append, tmp_path),
        version="lock-v1",
    )
    with pytest.raises(SimulatedInterruption):
        engine.execute([task])

    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    workers = [
        context.Process(target=_resume_worker, args=(str(tmp_path), results))
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert [worker.exitcode for worker in workers] == [0, 0]
    assert sorted(results.get(timeout=2) for _ in workers) == ["ok", "ok"]
    assert (tmp_path / "calls.log").read_text(encoding="utf-8").splitlines() == [
        "called"
    ]
    events = RunEngine.for_workspace(tmp_path).events.read_all()
    assert len({event.event_id for event in events}) == len(events)
    assert [event.event_type for event in events].count("task.started") == 2


def test_required_gate_blocks_then_allows_callback_task(tmp_path: Path) -> None:
    """A required approval is checked before callback execution."""
    calls: list[str] = []
    engine = RunEngine.for_workspace(tmp_path)
    engine.initialize(_manifest())
    engine.gates.request(
        GateRequest(id="approval", name="Approval", requested_by="requester")
    )
    task = TaskDefinition(
        "gated",
        lambda: calls.append("called"),
        version="gate-v1",
        required_gate="approval",
    )

    with pytest.raises(PermissionError):
        engine.execute([task])
    assert calls == []
    engine.gates.decide(
        "approval",
        GateDecision(
            status=GateStatus.APPROVED,
            decided_by="reviewer",
            rationale="approved",
        ),
    )
    engine.resume([task])
    assert calls == ["called"]


def test_command_task_uses_trusted_runner_and_hashes_output(tmp_path: Path) -> None:
    """Command tasks execute through CommandRunner and checkpoint outputs."""
    runner = CommandRunner({"python": Path(sys.executable)})
    engine = RunEngine.for_workspace(tmp_path, runner=runner)
    engine.initialize(_manifest())
    command = CommandSpec(
        argv=[
            "python",
            "-c",
            "from pathlib import Path; Path('result.txt').write_text('ok')",
        ]
    )
    task = TaskDefinition(
        "command",
        artifact_paths=(Path("result.txt"),),
        command=command,
    )

    report = engine.execute([task])

    assert report.status is WorkflowStatus.PASSED
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "ok"


@pytest.mark.parametrize(
    "command",
    [
        CommandSpec(argv=["python", "-c", "raise SystemExit(7)"]),
        CommandSpec(
            argv=["python", "-c", "import time; time.sleep(2)"],
            timeout_seconds=1,
        ),
    ],
    ids=["nonzero", "timeout"],
)
def test_command_failure_has_event_without_checkpoint(
    tmp_path: Path, command: CommandSpec
) -> None:
    """Nonzero and timed-out commands fail without checkpointing."""
    engine = RunEngine.for_workspace(
        tmp_path, runner=CommandRunner({"python": Path(sys.executable)})
    )
    engine.initialize(_manifest())

    with pytest.raises(TaskCommandError):
        engine.execute([TaskDefinition("command", command=command)])

    assert not (tmp_path / "checkpoints" / "command.json").exists()
    assert engine.events.read_all()[-1].event_type == "task.failed"
