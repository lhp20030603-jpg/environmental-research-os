"""Checkpoint-driven execution and recovery for research workflow tasks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Never

from filelock import FileLock

from envresearch.kernel.checkpoints import (
    CheckpointManager,
    TaskExecution,
    checkpoint_path,
)
from envresearch.kernel.completion import (
    CompletionJournalError,
    TaskCompletionPublisher,
)
from envresearch.kernel.events import EventLog, EventLogCorruptionError
from envresearch.kernel.execution import (
    SimulatedInterruption,
    TaskCommandError,
    TaskExecutor,
)
from envresearch.kernel.gates import GateStore
from envresearch.kernel.recovery import (
    CheckpointCorruptionError,
    CorruptionPublisher,
    ResumeTransitionPublisher,
)
from envresearch.kernel.run_identity import bind_report_identity, report_identity_error
from envresearch.kernel.state import WorkflowStateMachine
from envresearch.kernel.task_identity import TaskDefinition, validate_unique_tasks
from envresearch.kernel.task_identity import plan_hash as task_plan_hash
from envresearch.models.enums import WorkflowStatus
from envresearch.models.run import RunManifest, RunReport
from envresearch.runner.command import CommandRunner
from envresearch.storage.artifacts import ArtifactStore

__all__ = [
    "CheckpointCorruptionError",
    "RunEngine",
    "SimulatedInterruption",
    "TaskCommandError",
    "TaskDefinition",
    "TaskExecution",
]


class RunEngine:
    """Execute a task plan and recover only from verified checkpoints."""

    def __init__(
        self,
        workspace: Path,
        artifacts: ArtifactStore,
        events: EventLog,
        state_machine: WorkflowStateMachine,
        gates: GateStore,
        runner: CommandRunner | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.artifacts = artifacts
        self.events = events
        self.state_machine = state_machine
        self.gates = gates
        self.runner = runner
        self.executor = TaskExecutor(self.workspace, gates, runner)
        self.recovery = CorruptionPublisher(artifacts, events, state_machine)
        self.resume_transition = ResumeTransitionPublisher(
            artifacts, events, state_machine
        )
        self.checkpoints = CheckpointManager(artifacts, events, self._raise_corruption)
        self.task_completion = TaskCompletionPublisher(artifacts, events)
        self._run_lock = FileLock(
            str(self.workspace / ".locks" / "run.filelock"), timeout=30
        )
        self._manifest: RunManifest | None = None
        self._report: RunReport | None = None

    @classmethod
    def for_workspace(
        cls, workspace: Path, *, runner: CommandRunner | None = None
    ) -> RunEngine:
        """Compose the durable kernel services for one workspace."""
        resolved = workspace.resolve()
        artifacts = ArtifactStore(resolved)
        events = EventLog(resolved / "events.jsonl")
        return cls(
            resolved,
            artifacts,
            events,
            WorkflowStateMachine(),
            GateStore(artifacts, events),
            runner,
        )

    def initialize(self, manifest: RunManifest) -> None:
        """Persist the manifest and initialize an empty pending run report."""
        with self._run_lock:
            self._initialize_locked(manifest)

    def _initialize_locked(self, manifest: RunManifest) -> None:
        if manifest.status is not WorkflowStatus.PENDING:
            raise ValueError("a run must be initialized in pending status")
        existing_manifest = self._read_manifest(required=False)
        if existing_manifest is not None and existing_manifest != manifest:
            raise ValueError("workspace is already initialized for another run")
        self._manifest = manifest
        self.artifacts.write_json(
            Path("run-manifest.json"), manifest.model_dump(mode="json")
        )
        existing_report = self._read_report(required=False)
        if existing_report is not None:
            self._report = existing_report
            return
        self._report = RunReport(
            run_id=manifest.run_id,
            benchmark_id=manifest.benchmark_id,
            status=WorkflowStatus.PENDING,
            started_at=datetime.now(UTC),
        )
        self._persist_report()

    def execute(self, tasks: Sequence[TaskDefinition]) -> RunReport:
        """Execute a fresh task plan, checkpointing each successful task."""
        with self._run_lock:
            return self._execute_locked(tasks)

    def _execute_locked(self, tasks: Sequence[TaskDefinition]) -> RunReport:
        task_plan = tuple(tasks)
        validate_unique_tasks(task_plan)
        self._ensure_initialized()
        report = self._require_report()
        if report.status is not WorkflowStatus.PENDING:
            raise ValueError("run has already started; use resume")
        plan_hash = task_plan_hash(task_plan)
        self.checkpoints.publish_plan(task_plan, plan_hash)
        report.status = self.state_machine.transition(
            report.status, WorkflowStatus.RUNNING
        )
        report.finished_at = None
        self._persist_report()
        return self._execute_from(task_plan, 0, plan_hash)

    def resume(self, tasks: Sequence[TaskDefinition]) -> RunReport:
        """Verify completed work and continue at the first unfinished task."""
        with self._run_lock:
            return self._resume_locked(tasks)

    def _resume_locked(self, tasks: Sequence[TaskDefinition]) -> RunReport:
        task_plan = tuple(tasks)
        validate_unique_tasks(task_plan)
        self._load_initialized_run()
        report = self._require_report()
        try:
            completed_report = self.task_completion.reconcile(
                self._require_manifest()
            )
        except EventLogCorruptionError as error:
            self._raise_corruption(
                "event-log",
                f"event log corruption: {error}",
                artifact_path=Path("events.jsonl"),
                subject="Event log",
                retire_path=self.task_completion.journal_path,
            )
        except CompletionJournalError as error:
            self._raise_corruption(
                "task-completion",
                str(error),
                artifact_path=self.task_completion.journal_path,
                subject="Task completion",
                retire_path=self.task_completion.journal_path,
            )
        if completed_report is not None:
            self._report = completed_report
            report = completed_report
        try:
            reconciled = self.resume_transition.reconcile()
        except EventLogCorruptionError as error:
            self._raise_corruption(
                "event-log",
                f"event log corruption: {error}",
                artifact_path=Path("events.jsonl"),
                subject="Event log",
                retire_path=self.resume_transition.journal_path,
            )
        if reconciled is not None:
            self._report = reconciled
            report = reconciled
        plan_hash = task_plan_hash(task_plan)
        try:
            self.checkpoints.verify_plan(task_plan, plan_hash)
            completed = self.checkpoints.verified_prefix(task_plan, plan_hash)
        except EventLogCorruptionError as error:
            self._raise_corruption(
                "event-log",
                f"event log corruption: {error}",
                artifact_path=Path("events.jsonl"),
                subject="Event log",
            )

        if len(completed) == len(task_plan) and report.status is WorkflowStatus.PASSED:
            return report
        if report.status is WorkflowStatus.PENDING:
            raise ValueError("run has not started; use execute")

        if reconciled is None:
            report = self.resume_transition.publish(
                self._require_manifest(), report, completed
            )
            self._report = report
        return self._execute_from(task_plan, len(completed), plan_hash)

    def _execute_from(
        self,
        tasks: tuple[TaskDefinition, ...],
        start_index: int,
        plan_hash: str,
    ) -> RunReport:
        report = self._require_report()
        for task_index in range(start_index, len(tasks)):
            task = tasks[task_index]
            self._append_event(
                "task.started",
                WorkflowStatus.RUNNING,
                WorkflowStatus.RUNNING,
                {
                    "task_id": task.task_id,
                    "task_index": task_index,
                    "plan_hash": plan_hash,
                },
            )
            try:
                self.executor.run(task)
                execution = self.checkpoints.prepare_checkpoint(
                    task, task_index, plan_hash
                )
            except SimulatedInterruption as error:
                self._interrupt(report, task, error)
                raise
            except Exception as error:
                self._fail(report, task, error)
                raise
            report = self.task_completion.publish(
                self._require_manifest(), report, execution
            )
            self._report = report

        report.status = self.state_machine.transition(
            WorkflowStatus.RUNNING, WorkflowStatus.PASSED
        )
        report.finished_at = datetime.now(UTC)
        self._persist_report()
        return report

    def _interrupt(
        self, report: RunReport, task: TaskDefinition, error: Exception
    ) -> None:
        report.status = self.state_machine.transition(
            WorkflowStatus.RUNNING, WorkflowStatus.FAILED
        )
        report.finished_at = datetime.now(UTC)
        self._append_event(
            "run.interrupted",
            WorkflowStatus.RUNNING,
            WorkflowStatus.FAILED,
            {
                "task_id": task.task_id,
                "error_type": type(error).__name__,
                "message": str(error),
            },
        )
        self._persist_report()

    def _fail(self, report: RunReport, task: TaskDefinition, error: Exception) -> None:
        report.status = self.state_machine.transition(
            WorkflowStatus.RUNNING, WorkflowStatus.FAILED
        )
        report.finished_at = datetime.now(UTC)
        self._append_event(
            "task.failed",
            WorkflowStatus.RUNNING,
            WorkflowStatus.FAILED,
            {
                "task_id": task.task_id,
                "error_type": type(error).__name__,
                "message": str(error),
            },
        )
        self._persist_report()

    def _raise_corruption(
        self,
        task_id: str,
        reason: str,
        artifact_path: Path | None = None,
        subject: str = "Checkpoint",
        retire_path: Path | None = None,
    ) -> Never:
        self.recovery.publish(
            self._require_manifest(),
            self._require_report(),
            task_id,
            reason,
            artifact_path=artifact_path or checkpoint_path(task_id),
            subject=subject,
            retire_path=retire_path,
        )

    def _append_event(
        self,
        event_type: str,
        from_status: WorkflowStatus,
        to_status: WorkflowStatus,
        payload: Mapping[str, object],
    ) -> None:
        manifest = self._require_manifest()
        self.events.append_next(
            run_id=manifest.run_id,
            event_type=event_type,
            actor="run-engine",
            from_status=from_status,
            to_status=to_status,
            payload=payload,
        )

    def _ensure_initialized(self) -> None:
        if self._read_manifest(required=False) is None:
            self.initialize(
                RunManifest(
                    run_id=self.workspace.name or "run",
                    benchmark_id="unspecified",
                )
            )
        else:
            self._load_initialized_run()

    def _load_initialized_run(self) -> None:
        self._manifest = self._read_manifest(required=True)
        recovered = self.recovery.reconcile()
        self._report = self._read_report(required=True)
        if recovered is not None:
            raise CheckpointCorruptionError(recovered)

    def _read_manifest(self, *, required: bool) -> RunManifest | None:
        path = self.workspace / "run-manifest.json"
        if not path.exists():
            if required:
                raise RuntimeError("run is not initialized")
            return None
        return RunManifest.model_validate(self.artifacts.read_json(Path(path.name)))

    def _read_report(self, *, required: bool) -> RunReport | None:
        path = self.workspace / "run-report.json"
        if not path.exists():
            if required:
                raise RuntimeError("run report is missing")
            return None
        report = RunReport.model_validate(self.artifacts.read_json(Path(path.name)))
        manifest = self._require_manifest()
        reason = report_identity_error(manifest, report)
        if reason is not None:
            self.recovery.publish(
                manifest,
                bind_report_identity(manifest, report),
                "run-report",
                reason,
                artifact_path=Path(path.name),
                subject="Run report",
            )
        return report

    def _persist_report(self) -> None:
        report = self._require_report()
        self.artifacts.write_json(
            Path("run-report.json"), report.model_dump(mode="json")
        )

    def _require_manifest(self) -> RunManifest:
        if self._manifest is None:
            raise RuntimeError("run is not initialized")
        return self._manifest

    def _require_report(self) -> RunReport:
        if self._report is None:
            raise RuntimeError("run report is not initialized")
        return self._report
