"""Plan anchoring and verified per-task checkpoints."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Never

from pydantic import BaseModel, ConfigDict, Field, field_validator

from envresearch.kernel.events import EventLog
from envresearch.kernel.task_identity import (
    TaskDefinition,
    definition_hash,
    payload_hash,
    plan_description,
)
from envresearch.storage.artifacts import ArtifactStore
from envresearch.storage.hashing import sha256_file
from envresearch.storage.paths import safe_join

_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA_VERSION = "1.0"
CorruptionHandler = Callable[[str, str, Path, str], Never]


class TaskExecution(BaseModel):
    """Validated durable contents of one passed-task checkpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    task_id: str
    status: Literal["passed"]
    completed_at: datetime
    artifact_hashes: dict[str, str]
    task_index: int = Field(ge=0)
    definition_hash: str
    plan_hash: str
    checkpoint_hash: str

    @field_validator("task_id")
    @classmethod
    def require_safe_task_id(cls, value: str) -> str:
        if not _SAFE_TASK_ID.fullmatch(value):
            raise ValueError("task ID must be a safe filename segment")
        return value

    @field_validator("completed_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
            raise ValueError("timestamps must be UTC-aware")
        return value

    @field_validator("definition_hash", "plan_hash", "checkpoint_hash")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("hash must be a lowercase SHA-256 digest")
        return value

    @field_validator("artifact_hashes")
    @classmethod
    def require_artifact_hashes(cls, value: dict[str, str]) -> dict[str, str]:
        for relative, digest in value.items():
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError("artifact hash paths must be safe and relative")
            if not _SHA256.fullmatch(digest):
                raise ValueError("artifact hash must be a lowercase SHA-256 digest")
        return value


class CheckpointManager:
    """Publish plans/checkpoints and verify the completed task prefix."""

    def __init__(
        self,
        artifacts: ArtifactStore,
        events: EventLog,
        corrupt: CorruptionHandler,
    ) -> None:
        self.artifacts = artifacts
        self.events = events
        self.workspace = artifacts.root
        self.corrupt = corrupt

    def publish_plan(
        self, tasks: tuple[TaskDefinition, ...], plan_hash: str
    ) -> None:
        core: dict[str, object] = {
            "schema_version": _SCHEMA_VERSION,
            "plan_hash": plan_hash,
            "tasks": plan_description(tasks),
        }
        plan_path = Path("task-plan.json")
        if (self.workspace / plan_path).exists():
            self._verify_plan_document(tasks, plan_hash)
        else:
            self.artifacts.write_json(
                plan_path, {**core, "record_hash": payload_hash(core)}
            )
        anchor_path = Path("task-plan-anchor.json")
        if (self.workspace / anchor_path).exists():
            self._verify_plan_anchor(tasks, plan_hash)
        else:
            self.artifacts.write_json(
                anchor_path,
                {"schema_version": _SCHEMA_VERSION, "plan_hash": plan_hash},
            )

    def verify_plan(
        self, tasks: tuple[TaskDefinition, ...], plan_hash: str
    ) -> None:
        self._verify_plan_document(tasks, plan_hash)
        self._verify_plan_anchor(tasks, plan_hash)
        recorded_hashes = {
            event.payload.get("plan_hash")
            for event in self.events.read_all()
            if event.event_type == "task.started"
        }
        if recorded_hashes and recorded_hashes != {plan_hash}:
            self._plan_corruption(tasks, "task plan changed")

    def prepare_checkpoint(
        self, task: TaskDefinition, task_index: int, plan_hash: str
    ) -> TaskExecution:
        """Build exact checkpoint contents without publishing them."""
        core: dict[str, object] = {
            "schema_version": _SCHEMA_VERSION,
            "task_id": task.task_id,
            "status": "passed",
            "completed_at": datetime.now(UTC).isoformat(),
            "artifact_hashes": self._artifact_hashes(task),
            "task_index": task_index,
            "definition_hash": definition_hash(task),
            "plan_hash": plan_hash,
        }
        provisional = TaskExecution.model_validate(
            {**core, "checkpoint_hash": "0" * 64}
        )
        canonical = provisional.model_dump(mode="json", exclude={"checkpoint_hash"})
        execution = provisional.model_copy(
            update={"checkpoint_hash": payload_hash(canonical)}
        )
        return execution

    def verified_prefix(
        self, tasks: tuple[TaskDefinition, ...], plan_hash: str
    ) -> list[str]:
        completed: list[str] = []
        gap_found = False
        for task_index, task in enumerate(tasks):
            path = self.workspace / checkpoint_path(task.task_id)
            if not path.exists():
                if self._has_passed_event(task.task_id):
                    self._checkpoint_corruption(
                        task.task_id, "recorded checkpoint is missing"
                    )
                gap_found = True
                continue
            if gap_found:
                self._checkpoint_corruption(
                    task.task_id, "checkpoint exists after an unfinished task"
                )
            execution = self._read_checkpoint(task.task_id)
            self._verify_execution(task, task_index, plan_hash, execution)
            completed.append(task.task_id)
        return completed

    def _verify_execution(
        self,
        task: TaskDefinition,
        task_index: int,
        plan_hash: str,
        execution: TaskExecution,
    ) -> None:
        if execution.task_id != task.task_id:
            self._checkpoint_corruption(task.task_id, "checkpoint task ID changed")
        if execution.task_index != task_index:
            self._checkpoint_corruption(task.task_id, "task order changed")
        if execution.definition_hash != definition_hash(task):
            self._checkpoint_corruption(task.task_id, "task definition changed")
        if execution.plan_hash != plan_hash:
            self._checkpoint_corruption(task.task_id, "task plan changed")
        payload = execution.model_dump(mode="json", exclude={"checkpoint_hash"})
        if payload_hash(payload) != execution.checkpoint_hash:
            self._checkpoint_corruption(task.task_id, "checkpoint hash mismatch")
        anchors = [
            event.payload.get("checkpoint_hash")
            for event in self.events.read_all()
            if event.event_type == "task.passed"
            and event.payload.get("task_id") == task.task_id
        ]
        if anchors != [execution.checkpoint_hash]:
            self._checkpoint_corruption(
                task.task_id, "checkpoint does not match recorded pass event"
            )
        self._verify_artifacts(task, execution)

    def _read_checkpoint(self, task_id: str) -> TaskExecution:
        try:
            payload = self.artifacts.read_json(checkpoint_path(task_id))
            return TaskExecution.model_validate(payload)
        except (OSError, UnicodeError, ValueError, TypeError) as error:
            self._checkpoint_corruption(
                task_id, f"checkpoint is unreadable: {error}"
            )

    def _verify_artifacts(
        self, task: TaskDefinition, execution: TaskExecution
    ) -> None:
        expected_paths = {path.as_posix() for path in task.artifact_paths}
        if set(execution.artifact_hashes) != expected_paths:
            self._checkpoint_corruption(task.task_id, "associated artifact set changed")
        for relative, expected_hash in execution.artifact_hashes.items():
            try:
                path = safe_join(self.workspace, Path(relative))
            except ValueError as error:
                self._checkpoint_corruption(
                    task.task_id, f"associated artifact escaped workspace: {error}"
                )
            if not path.is_file():
                self._checkpoint_corruption(
                    task.task_id, f"associated artifact is missing: {relative}"
                )
            if sha256_file(path) != expected_hash:
                self._checkpoint_corruption(
                    task.task_id,
                    f"associated artifact hash mismatch: {relative}",
                )

    def _artifact_hashes(self, task: TaskDefinition) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for relative in task.artifact_paths:
            path = safe_join(self.workspace, relative)
            if not path.is_file():
                raise FileNotFoundError(f"declared task artifact is missing: {relative}")
            hashes[relative.as_posix()] = sha256_file(path)
        return hashes

    def _verify_plan_document(
        self, tasks: tuple[TaskDefinition, ...], plan_hash: str
    ) -> None:
        try:
            payload = self.artifacts.read_json(Path("task-plan.json"))
        except (OSError, UnicodeError, ValueError, TypeError) as error:
            self._plan_corruption(tasks, f"task plan is unreadable: {error}")
        if set(payload) != {"schema_version", "plan_hash", "tasks", "record_hash"}:
            self._plan_corruption(tasks, "task plan fields changed")
        record_hash = payload.pop("record_hash")
        if not isinstance(record_hash, str) or payload_hash(payload) != record_hash:
            self._plan_corruption(tasks, "task plan hash mismatch")
        if payload != {
            "schema_version": _SCHEMA_VERSION,
            "plan_hash": plan_hash,
            "tasks": plan_description(tasks),
        }:
            self._plan_corruption(tasks, "task plan changed")

    def _verify_plan_anchor(
        self, tasks: tuple[TaskDefinition, ...], plan_hash: str
    ) -> None:
        try:
            anchor = self.artifacts.read_json(Path("task-plan-anchor.json"))
        except (OSError, UnicodeError, ValueError, TypeError) as error:
            self._plan_corruption(tasks, f"task plan anchor is unreadable: {error}")
        if anchor != {"schema_version": _SCHEMA_VERSION, "plan_hash": plan_hash}:
            self._plan_corruption(tasks, "task plan anchor mismatch")

    def _has_passed_event(self, task_id: str) -> bool:
        return any(
            event.event_type == "task.passed"
            and event.payload.get("task_id") == task_id
            for event in self.events.read_all()
        )

    def _checkpoint_corruption(self, task_id: str, reason: str) -> Never:
        self.corrupt(task_id, reason, checkpoint_path(task_id), "Checkpoint")

    def _plan_corruption(
        self, tasks: tuple[TaskDefinition, ...], reason: str
    ) -> Never:
        task_id = tasks[0].task_id if tasks else "task-plan"
        self.corrupt(task_id, reason, Path("task-plan.json"), "Task plan")


def checkpoint_path(task_id: str) -> Path:
    """Return the confined relative checkpoint path for a validated task ID."""
    return Path("checkpoints") / f"{task_id}.json"
