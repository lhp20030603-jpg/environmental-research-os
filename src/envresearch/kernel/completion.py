"""Journaled publication of a successful task completion."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from envresearch.kernel.checkpoints import TaskExecution, checkpoint_path
from envresearch.kernel.events import EventLog, EventRecord, append_event_once
from envresearch.kernel.task_identity import payload_hash
from envresearch.models.enums import WorkflowStatus
from envresearch.models.run import RunManifest, RunReport
from envresearch.storage.artifacts import ArtifactStore

_JOURNAL_PATH = Path("recovery/task-completion-pending.json")


class CompletionJournalError(RuntimeError):
    """Raised when a completion journal or its published state diverges."""


class TaskCompletionPublisher:
    """Publish checkpoint, pass event, and report through one durable outbox."""

    journal_path = _JOURNAL_PATH

    def __init__(self, artifacts: ArtifactStore, events: EventLog) -> None:
        self.artifacts = artifacts
        self.events = events

    def publish(
        self,
        manifest: RunManifest,
        report: RunReport,
        execution: TaskExecution,
    ) -> RunReport:
        """Persist exact publication intent before exposing any completion state."""
        pending = self.reconcile(manifest)
        if pending is not None:
            return pending
        if execution.task_id in report.completed_tasks:
            raise CompletionJournalError("task is already complete in the source report")
        completed = report.model_copy(deep=True)
        completed.completed_tasks.append(execution.task_id)
        event = self._pass_event(manifest, execution)
        core: dict[str, object] = {
            "checkpoint": execution.model_dump(mode="json"),
            "event": event.model_dump(mode="json"),
            "source_report": report.model_dump(mode="json"),
            "completed_report": completed.model_dump(mode="json"),
        }
        self.artifacts.write_json(
            _JOURNAL_PATH, {**core, "record_hash": payload_hash(core)}
        )
        return self._apply_journal(manifest, core)

    def reconcile(self, manifest: RunManifest) -> RunReport | None:
        """Finish an interrupted completion publication exactly once."""
        if not (self.artifacts.root / _JOURNAL_PATH).exists():
            return None
        payload = self.artifacts.read_json(_JOURNAL_PATH)
        expected = {
            "checkpoint",
            "event",
            "source_report",
            "completed_report",
            "record_hash",
        }
        if set(payload) != expected:
            raise CompletionJournalError("completion journal fields changed")
        record_hash = payload.pop("record_hash")
        if not isinstance(record_hash, str) or payload_hash(payload) != record_hash:
            raise CompletionJournalError("completion journal hash mismatch")
        return self._apply_journal(manifest, payload)

    def _apply_journal(
        self, manifest: RunManifest, payload: Mapping[str, object]
    ) -> RunReport:
        execution, event, source, completed = self._validated_payload(
            manifest, payload
        )
        relative = checkpoint_path(execution.task_id)
        checkpoint_payload = execution.model_dump(mode="json")
        checkpoint_file = self.artifacts.root / relative
        if checkpoint_file.exists():
            if self.artifacts.read_json(relative) != checkpoint_payload:
                raise CompletionJournalError("completion checkpoint diverged")
        else:
            self.artifacts.write_json(relative, checkpoint_payload)

        append_event_once(self.events, event)
        current = self._read_report()
        if current == source:
            self._write_report(completed)
            published = completed
        elif current == completed or self._is_later_report(current, completed):
            published = current
        else:
            raise CompletionJournalError("completion report state diverged")
        (self.artifacts.root / _JOURNAL_PATH).unlink()
        return published

    def _validated_payload(
        self, manifest: RunManifest, payload: Mapping[str, object]
    ) -> tuple[TaskExecution, EventRecord, RunReport, RunReport]:
        values = tuple(
            payload.get(name)
            for name in (
                "checkpoint",
                "event",
                "source_report",
                "completed_report",
            )
        )
        if not all(isinstance(value, Mapping) for value in values):
            raise CompletionJournalError("completion journal payload is invalid")
        try:
            execution = TaskExecution.model_validate(values[0])
            event = EventRecord.model_validate(values[1])
            source = RunReport.model_validate(values[2])
            completed = RunReport.model_validate(values[3])
        except (TypeError, ValidationError, ValueError) as error:
            raise CompletionJournalError(
                f"completion journal payload is invalid: {error}"
            ) from error

        expected_event = self._pass_event(
            manifest,
            execution,
            event_id=event.event_id,
            timestamp=event.timestamp,
        )
        expected_completed = source.model_copy(deep=True)
        expected_completed.completed_tasks.append(execution.task_id)
        identities = {
            (source.run_id, source.benchmark_id),
            (completed.run_id, completed.benchmark_id),
        }
        if identities != {(manifest.run_id, manifest.benchmark_id)}:
            raise CompletionJournalError("completion report identity mismatch")
        if event != expected_event:
            raise CompletionJournalError("completion event contents changed")
        if completed != expected_completed:
            raise CompletionJournalError("completion report contents changed")
        return execution, event, source, completed

    def _pass_event(
        self,
        manifest: RunManifest,
        execution: TaskExecution,
        *,
        event_id: str | None = None,
        timestamp: datetime | None = None,
    ) -> EventRecord:
        sequence = len(self.events.read_all()) + 1
        return EventRecord(
            event_id=event_id or f"{manifest.run_id}.{sequence:08d}",
            run_id=manifest.run_id,
            event_type="task.passed",
            actor="run-engine",
            timestamp=timestamp or datetime.now(UTC),
            from_status=WorkflowStatus.RUNNING,
            to_status=WorkflowStatus.RUNNING,
            payload={
                "task_id": execution.task_id,
                "task_index": execution.task_index,
                "checkpoint_hash": execution.checkpoint_hash,
                "artifact_hashes": dict(execution.artifact_hashes),
            },
        )

    def _read_report(self) -> RunReport:
        return RunReport.model_validate(
            self.artifacts.read_json(Path("run-report.json"))
        )

    @staticmethod
    def _is_later_report(current: RunReport, completed: RunReport) -> bool:
        """Recognize safe forward progress if a journal deletion is replayed."""
        completed_count = len(completed.completed_tasks)
        return (
            current.run_id == completed.run_id
            and current.benchmark_id == completed.benchmark_id
            and current.started_at == completed.started_at
            and current.completed_tasks[:completed_count]
            == completed.completed_tasks
        )

    def _write_report(self, report: RunReport) -> None:
        self.artifacts.write_json(
            Path("run-report.json"), report.model_dump(mode="json")
        )
