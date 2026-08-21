"""Private validation helpers for the container execution boundary."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from envresearch.models.benchmark import validate_command_environment
from envresearch.replication._container_paths import workspace_authority
from envresearch.replication.contracts import ReplicationBudget

if TYPE_CHECKING:
    from envresearch.replication.container import ContainerPlan

_NONROOT_UID_GID = re.compile(r"^[1-9][0-9]*:[1-9][0-9]*$")
_MOUNT_DELIMITERS = frozenset({",", "=", "\n", "\r"})


def allocate_output_namespace(output_root: Path, namespace: str) -> Path:
    """Create one validated, non-overlapping output child for a named workflow."""
    if (
        not namespace
        or "/" in namespace
        or "\\" in namespace
        or namespace in {".", ".."}
    ):
        raise ValueError("output namespace must be a safe single path component")
    base = require_existing_directory(output_root, "output root")
    require_safe_mount_path(base, "output root")
    workspace_authority(base, "runs", "output root")
    target = base / namespace
    if target.exists() and target.is_symlink():
        raise ValueError("output namespace must not be a symlink")
    target.mkdir(mode=0o700, exist_ok=True)
    return target.resolve()


def validate_profile_values(image_digest: str, user: str) -> None:
    """Recheck profile values against forged Pydantic instances."""
    name, separator, digest = image_digest.partition("@sha256:")
    if (
        not image_digest.strip()
        or not name
        or not separator
        or "@" in name
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise ValueError("image digest must be pinned with @sha256:")
    if not _NONROOT_UID_GID.fullmatch(user):
        raise ValueError("container user must be a non-root numeric UID:GID")


def container_name(plan: ContainerPlan) -> str:
    identity = hashlib.sha256(
        f"{plan.output_root}\0{plan.output_namespace}".encode()
    ).hexdigest()[:24]
    return f"envresearch-{identity}"


def workspace_container_names(output_root: Path) -> tuple[str, str]:
    """Derive both exact stage identities from one authenticated attempt root."""
    namespaces = ("author-reproduction", "derived-did-event-study")
    return (
        _workspace_container_name(output_root, namespaces[0]),
        _workspace_container_name(output_root, namespaces[1]),
    )


def _workspace_container_name(output_root: Path, namespace: str) -> str:
    value = f"{output_root / namespace}\0{namespace}".encode()
    return f"envresearch-{hashlib.sha256(value).hexdigest()[:24]}"


def validate_budget(budget: ReplicationBudget) -> None:
    """Recheck every resource limit before it becomes an engine argument."""
    for name in (
        "max_download_bytes",
        "max_storage_bytes",
        "max_memory_bytes",
        "inactivity_seconds",
    ):
        value = getattr(budget, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")


def validate_plan(plan: ContainerPlan) -> None:
    """Repeat all security checks immediately before command construction."""
    validate_profile_values(plan.image_digest, plan.user)
    validate_budget(plan.budget)
    validate_command_environment(plan.environment)
    validate_trusted_roots(plan.input_root, plan.output_root)


def validate_trusted_roots(input_root: Path, output_root: Path) -> tuple[Path, Path]:
    """Authorize only canonical children of one operator-supplied run root."""
    input_path = require_existing_directory(input_root, "input root")
    output_path = require_existing_directory(output_root, "output root")
    require_safe_mount_path(input_path, "input root")
    require_safe_mount_path(output_path, "output root")
    input_authority = workspace_authority(input_path, "acquired", "input root")
    output_authority = workspace_authority(output_path, "runs", "output root")
    if input_authority != output_authority:
        raise ValueError("input and output roots must share one run authority")
    if _overlap(input_path, output_path):
        raise ValueError("input and output roots must not overlap")
    return input_path, output_path


def require_existing_directory(path: Path, name: str) -> Path:
    """Resolve a directory only after rejecting any symlink in its spelling."""
    candidate = Path(path)
    for parent in (candidate, *candidate.parents):
        if parent.exists() and parent.is_symlink():
            raise ValueError(f"{name} must not contain a symlink")
    if not candidate.is_dir():
        raise ValueError(f"{name} must be an existing directory")
    return candidate.resolve()


def require_executable(path: Path) -> str:
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.is_symlink():
        raise ValueError("container executable must be an absolute regular file")
    resolved = candidate.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError("container executable must be an executable regular file")
    return str(resolved)


def require_safe_mount_path(path: Path, name: str) -> None:
    """Forbid delimiters that Docker/Podman interpret inside mount values."""
    if any(delimiter in str(path) for delimiter in _MOUNT_DELIMITERS):
        raise ValueError(f"{name} contains an unsafe mount delimiter")


def _overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents
