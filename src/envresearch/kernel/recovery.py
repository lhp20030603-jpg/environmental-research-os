"""Journaled publication of critical recovery findings."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Never

from envresearch.kernel.events import (
    EventLog,
    EventRecord,
)
from envresearch.kernel.state import WorkflowStateMachine
from envresearch.kernel.task_identity import payload_hash
from envresearch.models.enums import FindingSeverity, WorkflowStatus
from envresearch.models.finding import Finding
from envresearch.models.run import RunManifest, RunReport
from envresearch.storage.artifacts import ArtifactStore

_CORRUPTION_JOURNAL_PATH = Path("recovery/corruption-pending.json")
_RESUME_JOURNAL_PATH = Path("recovery/resume-pending.json")


class CheckpointCorruptionError(RuntimeError):
    """Raised after checkpoint corruption has been persisted as a finding."""

    def __init__(self, finding: Finding) -> None:
        super().__init__(finding.message)
        self.finding = finding


class CorruptionPublisher:
    """Atomically journal and idempotently publish corruption consequences."""

    def __init__(
        self,
        artifacts: ArtifactStore,
        events: EventLog,
        state_machine: WorkflowStateMachine,
    ) -> None:
        self.artifacts = artifacts
        self.events = events
        self.state_machine = state_machine

    def publish(
        self,
        manifest: RunManifest,
        report: RunReport,
        task_id: str,
        reason: str,
        *,
        artifact_path: Path,
        subject: str,
        retire_path: Path | None = None,
    ) -> Never:
        """Journal a deterministic critical finding and publish it or fail safely."""
        finding = self._finding(
            manifest, task_id, reason, artifact_path=artifact_path, subject=subject
        )
        invalidated = self._invalidated_report(report, finding)
        core: dict[str, object] = {
            "finding": finding.model_dump(mode="json"),
            "report": invalidated.model_dump(mode="json"),
            "retire_path": retire_path.as_posix() if retire_path is not None else None,
        }
        self.artifacts.write_json(
            _CORRUPTION_JOURNAL_PATH,
            {**core, "record_hash": payload_hash(core)},
        )
        self._apply_journal(core)
        raise CheckpointCorruptionError(finding)

    def reconcile(self) -> Finding | None:
        """Complete an interrupted publication and return its durable finding."""
        if not (self.artifacts.root / _CORRUPTION_JOURNAL_PATH).exists():
            return None
        payload = self.artifacts.read_json(_CORRUPTION_JOURNAL_PATH)
        current_fields = {"finding", "report", "retire_path", "record_hash"}
        legacy_fields = {"finding", "report", "record_hash"}
        if frozenset(payload) not in {
            frozenset(current_fields),
            frozenset(legacy_fields),
        }:
            raise RuntimeError("corruption journal fields changed")
        record_hash = payload.pop("record_hash")
        if not isinstance(record_hash, str) or payload_hash(payload) != record_hash:
            raise RuntimeError("corruption journal hash mismatch")
        payload.setdefault("retire_path", None)
        finding_value = payload.get("finding")
        if not isinstance(finding_value, Mapping):
            raise TypeError("corruption journal finding is invalid")
        finding = Finding.model_validate(finding_value)
        self._apply_journal(payload)
        return finding

    def _apply_journal(self, payload: Mapping[str, object]) -> None:
        finding_value = payload.get("finding")
        report_value = payload.get("report")
        retire_value = payload.get("retire_path")
        if not isinstance(finding_value, Mapping) or not isinstance(
            report_value, Mapping
        ):
            raise TypeError("corruption journal payload is invalid")
        if retire_value is not None and not isinstance(retire_value, str):
            raise TypeError("corruption journal retire path is invalid")
        finding = Finding.model_validate(finding_value)
        report = RunReport.model_validate(report_value)
        self.artifacts.write_json(
            Path("findings") / f"{finding.id}.json", finding.model_dump(mode="json")
        )
        self.artifacts.write_json(
            Path("run-report.json"), report.model_dump(mode="json")
        )
        if isinstance(retire_value, str):
            retire_path = Path(retire_value)
            if retire_path.is_absolute() or ".." in retire_path.parts:
                raise RuntimeError("corruption journal retire path is unsafe")
            (self.artifacts.root / retire_path).unlink(missing_ok=True)
        (self.artifacts.root / _CORRUPTION_JOURNAL_PATH).unlink()

    def _invalidated_report(
        self, report: RunReport, finding: Finding
    ) -> RunReport:
        invalidated = report.model_copy(deep=True)
        target = self._corruption_target(report.status)
        if target is not report.status:
            invalidated.status = self.state_machine.transition(report.status, target)
        invalidated.finished_at = datetime.now(UTC)
        invalidated.findings = [
            *[item for item in invalidated.findings if item.id != finding.id],
            finding,
        ]
        return invalidated

    @staticmethod
    def _corruption_target(status: WorkflowStatus) -> WorkflowStatus:
        targets = {
            WorkflowStatus.PENDING: WorkflowStatus.REJECTED,
            WorkflowStatus.RUNNING: WorkflowStatus.FAILED,
            WorkflowStatus.FAILED: WorkflowStatus.FAILED,
            WorkflowStatus.REPAIR_PENDING: WorkflowStatus.REJECTED,
            WorkflowStatus.REVIEW_REQUIRED: WorkflowStatus.REJECTED,
            WorkflowStatus.APPROVED: WorkflowStatus.SUPERSEDED,
            WorkflowStatus.PASSED: WorkflowStatus.SUPERSEDED,
            WorkflowStatus.REJECTED: WorkflowStatus.REJECTED,
            WorkflowStatus.SUPERSEDED: WorkflowStatus.SUPERSEDED,
        }
        return targets[status]

    @staticmethod
    def _finding(
        manifest: RunManifest,
        task_id: str,
        reason: str,
        *,
        artifact_path: Path,
        subject: str,
    ) -> Finding:
        identity = hashlib.sha256(
            f"{manifest.run_id}\0{task_id}".encode()
        ).hexdigest()[:16]
        return Finding(
            id=f"checkpoint-corrupted-{identity}",
            code="CHECKPOINT_CORRUPTED",
            severity=FindingSeverity.CRITICAL,
            message=f"{subject} for task '{task_id}' is corrupted: {reason}",
            producer="run-engine",
            evidence=(str(artifact_path), reason),
        )


class ResumeTransitionPublisher:
    """Journal and reconcile the report-event-report resume transition."""

    journal_path = _RESUME_JOURNAL_PATH

    def __init__(
        self,
        artifacts: ArtifactStore,
        events: EventLog,
        state_machine: WorkflowStateMachine,
    ) -> None:
        self.artifacts = artifacts
        self.events = events
        self.state_machine = state_machine

    def publish(
        self,
        manifest: RunManifest,
        report: RunReport,
        completed_tasks: list[str],
    ) -> RunReport:
        """Persist a durable intent before publishing any recovery transition."""
        pending = self.reconcile()
        if pending is not None:
            return pending
        intermediate = report.model_copy(deep=True)
        if report.status is WorkflowStatus.FAILED:
            intermediate.status = self.state_machine.transition(
                report.status, WorkflowStatus.REPAIR_PENDING
            )
        elif report.status not in {
            WorkflowStatus.REPAIR_PENDING,
            WorkflowStatus.RUNNING,
        }:
            raise ValueError(f"run cannot be resumed from {report.status}")
        intermediate.completed_tasks = list(completed_tasks)

        running = intermediate.model_copy(deep=True)
        event_from = intermediate.status
        if event_from is WorkflowStatus.REPAIR_PENDING:
            running.status = self.state_machine.transition(
                event_from, WorkflowStatus.RUNNING
            )
        running.finished_at = None
        event = self._resume_event(manifest, event_from, completed_tasks)
        core: dict[str, object] = {
            "source_status": report.status.value,
            "intermediate_report": intermediate.model_dump(mode="json"),
            "event": event.model_dump(mode="json"),
            "running_report": running.model_dump(mode="json"),
        }
        self.artifacts.write_json(
            _RESUME_JOURNAL_PATH,
            {**core, "record_hash": payload_hash(core)},
        )
        return self._apply_journal(core)

    def reconcile(self) -> RunReport | None:
        """Complete an interrupted resume publication exactly once."""
        if not (self.artifacts.root / _RESUME_JOURNAL_PATH).exists():
            return None
        payload = self.artifacts.read_json(_RESUME_JOURNAL_PATH)
        expected = {
            "source_status",
            "intermediate_report",
            "event",
            "running_report",
            "record_hash",
        }
        if set(payload) != expected:
            raise RuntimeError("resume transition journal fields changed")
        record_hash = payload.pop("record_hash")
        if not isinstance(record_hash, str) or payload_hash(payload) != record_hash:
            raise RuntimeError("resume transition journal hash mismatch")
        return self._apply_journal(payload)

    def _apply_journal(self, payload: Mapping[str, object]) -> RunReport:
        source_value = payload.get("source_status")
        intermediate_value = payload.get("intermediate_report")
        event_value = payload.get("event")
        running_value = payload.get("running_report")
        if (
            not isinstance(source_value, str)
            or not isinstance(intermediate_value, Mapping)
            or not isinstance(event_value, Mapping)
            or not isinstance(running_value, Mapping)
        ):
            raise TypeError("resume transition journal payload is invalid")
        source_status = WorkflowStatus(source_value)
        intermediate = RunReport.model_validate(intermediate_value)
        event = EventRecord.model_validate(event_value)
        running = RunReport.model_validate(running_value)
        current = RunReport.model_validate(
            self.artifacts.read_json(Path("run-report.json"))
        )
        if current.status is source_status:
            self._write_report(intermediate)
        elif current.status not in {intermediate.status, running.status}:
            raise RuntimeError("resume transition report state diverged")
        self._append_event_once(event)
        self._write_report(running)
        (self.artifacts.root / _RESUME_JOURNAL_PATH).unlink()
        return running

    def _append_event_once(self, event: EventRecord) -> None:
        for existing in self.events.read_all():
            if existing.event_id == event.event_id:
                if existing != event:
                    raise RuntimeError("resume event identity collision")
                return
        self.events.append(event)

    def _resume_event(
        self,
        manifest: RunManifest,
        from_status: WorkflowStatus,
        completed_tasks: list[str],
    ) -> EventRecord:
        sequence = len(self.events.read_all()) + 1
        return EventRecord(
            event_id=f"{manifest.run_id}.{sequence:08d}",
            run_id=manifest.run_id,
            event_type="run.resumed",
            actor="run-engine",
            timestamp=datetime.now(UTC),
            from_status=from_status,
            to_status=WorkflowStatus.RUNNING,
            payload={"completed_tasks": list(completed_tasks)},
        )

    def _write_report(self, report: RunReport) -> None:
        self.artifacts.write_json(
            Path("run-report.json"), report.model_dump(mode="json")
        )
