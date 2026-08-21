"""Execution adapters for callback, gated, and trusted command tasks."""

from pathlib import Path

from envresearch.kernel.gates import GateStore
from envresearch.kernel.task_identity import TaskDefinition
from envresearch.runner.command import CommandRunner


class SimulatedInterruption(RuntimeError):
    """Signal a controlled interruption while preserving resumable state."""


class TaskCommandError(RuntimeError):
    """Raised when a trusted command task times out or exits unsuccessfully."""


class TaskExecutor:
    """Execute one task through its declared callback or trusted command path."""

    def __init__(
        self,
        workspace: Path,
        gates: GateStore,
        runner: CommandRunner | None,
    ) -> None:
        self.workspace = workspace
        self.gates = gates
        self.runner = runner

    def run(self, task: TaskDefinition) -> None:
        """Require any gate and execute exactly one configured task implementation."""
        if task.required_gate is not None:
            self.gates.require_approved(task.required_gate)
        if task.action is not None:
            task.action()
            return
        if task.command is None:
            raise RuntimeError("task has neither callback nor command")
        if self.runner is None:
            raise RuntimeError("command task requires a configured CommandRunner")
        result = self.runner.run(task.command, self.workspace)
        if result.timed_out:
            raise TaskCommandError(f"task command timed out: {task.task_id}")
        if result.return_code != 0:
            raise TaskCommandError(
                f"task command exited with {result.return_code}: {task.task_id}"
            )
