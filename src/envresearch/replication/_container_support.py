"""Private materialization and result helpers for the container boundary."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from envresearch.replication.contracts import ReplicationBudget

if TYPE_CHECKING:
    from envresearch.replication.container import CommandExecution, ContainerPlan


def _validate_generated_files(files: Mapping[str, str]) -> None:
    for path, contents in files.items():
        relative = Path(path)
        if not path or relative.is_absolute() or ".." in relative.parts or not contents:
            raise ValueError("generated files must use nonempty safe relative paths")


def _materialize_generated_files(plan: ContainerPlan) -> None:
    """Place generated commands beneath the sole read-only input mount safely."""
    if not plan.generated_files:
        return
    generated_root = plan.input_root / ".generated"
    if generated_root.exists() and generated_root.is_symlink():
        raise ValueError("generated file root must not be a symlink")
    generated_root.mkdir(mode=0o700, exist_ok=True)
    for relative, contents in plan.generated_files.items():
        target_parent = generated_root
        for part in Path(relative).parts[:-1]:
            target_parent = target_parent / part
            if target_parent.exists() and target_parent.is_symlink():
                raise ValueError("generated file parent must not be a symlink")
            target_parent.mkdir(mode=0o700, exist_ok=True)
        target = target_parent / Path(relative).name
        if target.exists():
            if target.is_symlink() or not target.is_file():
                raise ValueError("generated file target must be a regular file")
            if target.read_text() != contents:
                raise ValueError("generated file target already has different contents")
            continue
        target.write_text(contents, encoding="utf-8", newline="\n")


def _validate_execution(execution: CommandExecution) -> None:
    if execution.finished_at < execution.started_at:
        raise ValueError("container execution timestamps must be ordered")
    resources = (execution.peak_memory_bytes, execution.storage_bytes)
    measured = execution.resource_status == "measured"
    if execution.resource_status not in {"measured", "unknown"}:
        raise ValueError("container resource status is invalid")
    if measured and (
        any(type(value) is not int or value < 0 for value in resources)
        or type(execution.oom_killed) is not bool
    ):
        raise ValueError("measured container resources are invalid")
    if not measured and (resources != (None, None) or execution.oom_killed is not None):
        raise ValueError("unknown container resources are inconsistent")


def _require_within_budget(
    execution: CommandExecution, budget: ReplicationBudget
) -> None:
    """Fail closed if the engine reports exhausted memory or writable storage."""
    if execution.resource_status != "measured":
        raise RuntimeError(
            "container exceeded approved resource-evidence boundary: unknown"
        )
    if execution.oom_killed:
        raise RuntimeError("container exceeded approved memory budget: OOM killed")
    if execution.peak_memory_bytes is None or execution.storage_bytes is None:
        raise RuntimeError("container exceeded approved resource-evidence boundary")
    if execution.peak_memory_bytes > budget.max_memory_bytes:
        raise RuntimeError("container exceeded approved memory budget")
    if execution.storage_bytes > budget.max_storage_bytes:
        raise RuntimeError("container exceeded approved storage budget")


def _bounded_log(value: str, maximum_bytes: int) -> tuple[str, bool]:
    """Truncate UTF-8 safely before hashing and expose the exact event."""
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return value, False
    return encoded[:maximum_bytes].decode("utf-8", errors="ignore"), True


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
