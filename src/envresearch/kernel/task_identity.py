"""Stable, explicit identities for resumable task definitions."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from envresearch.kernel.task_identity_graph import (
    TaskIdentityError,
    callback_identity,
)
from envresearch.models.benchmark import CommandSpec

__all__ = [
    "TaskDefinition",
    "TaskIdentityError",
    "definition_hash",
    "payload_hash",
    "plan_description",
    "plan_hash",
    "validate_unique_tasks",
]

_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True, slots=True)
class TaskDefinition:
    """One callback or trusted command plus its recovery identity."""

    task_id: str
    action: Callable[[], object] | None = None
    artifact_paths: tuple[Path, ...] = ()
    version: str | None = None
    required_gate: str | None = None
    command: CommandSpec | None = None
    _callback_identity_digest: str | None = field(
        init=False, repr=False, compare=False, default=None
    )

    def __post_init__(self) -> None:
        if not _SAFE_TASK_ID.fullmatch(self.task_id):
            raise ValueError("task ID must be a safe filename segment")
        if self.version is not None and not self.version.strip():
            raise ValueError("task version must not be blank")
        if (self.action is None) == (self.command is None):
            raise ValueError("task requires exactly one callback or command")
        if self.required_gate is not None and not self.required_gate.strip():
            raise ValueError("required gate ID must not be blank")
        normalized_paths = tuple(Path(path) for path in self.artifact_paths)
        if len(set(normalized_paths)) != len(normalized_paths):
            raise ValueError("task artifact paths must be unique")
        for path in normalized_paths:
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError("task artifact paths must be safe relative paths")
        object.__setattr__(self, "artifact_paths", normalized_paths)
        if self.action is not None:
            snapshot = callback_identity(self.action, self.version)
            object.__setattr__(
                self, "_callback_identity_digest", payload_hash(snapshot)
            )


def definition_hash(task: TaskDefinition) -> str:
    """Return a process-stable hash or reject implicit state omission."""
    identity: dict[str, object] = {
        "task_id": task.task_id,
        "version": task.version,
        "required_gate": task.required_gate,
        "artifact_paths": [path.as_posix() for path in task.artifact_paths],
    }
    if task.command is not None:
        identity["command"] = task.command.model_dump(mode="json")
    else:
        assert task._callback_identity_digest is not None
        identity["callback_digest"] = task._callback_identity_digest
    return payload_hash(identity)


def payload_hash(payload: Mapping[str, object]) -> str:
    """Hash canonical JSON metadata used by plans and checkpoints."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def plan_description(
    tasks: tuple[TaskDefinition, ...],
) -> list[dict[str, object]]:
    """Describe an ordered plan without serializing executable callables."""
    return [
        {
            "task_id": task.task_id,
            "task_index": index,
            "definition_hash": definition_hash(task),
        }
        for index, task in enumerate(tasks)
    ]


def plan_hash(tasks: tuple[TaskDefinition, ...]) -> str:
    """Hash the complete ordered task plan."""
    return payload_hash({"tasks": plan_description(tasks)})


def validate_unique_tasks(tasks: tuple[TaskDefinition, ...]) -> None:
    """Reject plans whose task IDs would share a durable checkpoint identity."""
    seen: set[str] = set()
    for task in tasks:
        if task.task_id in seen:
            raise ValueError(f"duplicate task ID: {task.task_id}")
        seen.add(task.task_id)
