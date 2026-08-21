"""Private unpredictable launch and inspect identity helpers."""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from envresearch.replication._container_lifecycle import ContainerCleanupError
from envresearch.replication._runtime_identity import ProcessIdentity, mount_sha256
from envresearch.replication._runtime_owner import (
    RuntimeLaunchIdentity,
    RuntimeOwnership,
)

if TYPE_CHECKING:
    from envresearch.replication.container import CommandExecution, ContainerPlan

_LABEL_NONCE = "io.envresearch.attempt-nonce"
_LABEL_IMAGE = "io.envresearch.image-digest"
_LABEL_INPUT = "io.envresearch.input-mount-sha256"
_LABEL_OUTPUT = "io.envresearch.output-mount-sha256"
_ABSENT = ("no such object", "no such container")


def prepare_launch(
    plan: ContainerPlan,
    engine: str,
    *,
    nonce_factory: Callable[[], str] = lambda: secrets.token_hex(32),
) -> RuntimeLaunchIdentity:
    """Allocate one exclusive, nonce-derived cidfile namespace."""
    nonce = nonce_factory()
    if not _canonical_sha256(nonce):
        raise ValueError("runtime launch nonce is not canonical")
    control_root = plan.output_root.parent / ".runtime"
    if control_root.exists() and (
        control_root.is_symlink() or not control_root.is_dir()
    ):
        raise ValueError("runtime control root is not a trusted directory")
    control_root.mkdir(mode=0o700, parents=False, exist_ok=True)
    control_root.chmod(0o700)
    cidfile = control_root / f"{nonce}.cid"
    if cidfile.exists() or cidfile.is_symlink():
        raise ValueError("runtime launch namespace already exists")
    return RuntimeLaunchIdentity(
        engine=engine,
        attempt_nonce=nonce,
        container_name=f"envresearch-{nonce[:24]}",
        cidfile_path=str(cidfile.resolve()),
        image_digest=plan.image_digest,
        input_mount_sha256=mount_sha256(plan.input_root),
        output_mount_sha256=mount_sha256(plan.output_root),
        prepared_at=datetime.now(UTC),
    )


def launch_arguments(launch: RuntimeLaunchIdentity) -> tuple[str, ...]:
    """Return the exact cidfile/name/label argv bound by the ledger."""
    return (
        "--cidfile",
        launch.cidfile_path,
        "--name",
        launch.container_name,
        "--label",
        f"{_LABEL_NONCE}={launch.attempt_nonce}",
        "--label",
        f"{_LABEL_IMAGE}={launch.image_digest}",
        "--label",
        f"{_LABEL_INPUT}={launch.input_mount_sha256}",
        "--label",
        f"{_LABEL_OUTPUT}={launch.output_mount_sha256}",
    )


def read_container_id(
    launch: RuntimeLaunchIdentity,
    *,
    timeout_seconds: float = 2.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> str:
    """Read one bounded canonical full CID from the exclusive cidfile."""
    deadline = monotonic() + timeout_seconds
    path = Path(launch.cidfile_path)
    while not path.exists() and monotonic() < deadline:
        sleeper(min(0.01, max(0.0, deadline - monotonic())))
    if path.is_symlink() or not path.is_file():
        raise ContainerCleanupError("tracked container cidfile is unavailable")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ContainerCleanupError(
            "tracked container cidfile is unreadable"
        ) from error
    if len(raw) not in {64, 65} or (len(raw) == 65 and not raw.endswith(b"\n")):
        raise ContainerCleanupError("tracked container cidfile is invalid")
    try:
        container_id = raw.removesuffix(b"\n").decode("ascii")
    except UnicodeDecodeError as error:
        raise ContainerCleanupError("tracked container cidfile is invalid") from error
    if not _canonical_sha256(container_id):
        raise ContainerCleanupError("tracked container cidfile is invalid")
    return container_id


def bind_owner(
    launch: RuntimeLaunchIdentity,
    process: ProcessIdentity,
    container_id: str,
) -> RuntimeOwnership:
    """Bind a prepared launch to its kernel process and actual full CID."""
    return RuntimeOwnership(
        engine=launch.engine,
        pid=process.pid,
        pgid=process.pgid,
        process_birth_sha256=process.birth_sha256,
        attempt_nonce=launch.attempt_nonce,
        container_name=launch.container_name,
        container_id=container_id,
        image_digest=launch.image_digest,
        input_mount_sha256=launch.input_mount_sha256,
        output_mount_sha256=launch.output_mount_sha256,
        started_at=datetime.now(UTC),
    )


def inspect_matches(execution: CommandExecution, owner: RuntimeOwnership) -> bool:
    """Validate a complete inspect record without mutating the container."""
    if execution.return_code != 0:
        if is_absent(execution.stderr):
            return False
        raise ContainerCleanupError("tracked container cleanup inspection failed")
    try:
        decoded: Any = json.loads(execution.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise ContainerCleanupError("tracked container identity is invalid") from error
    if isinstance(decoded, list) and len(decoded) == 1:
        decoded = decoded[0]
    if not isinstance(decoded, dict):
        raise ContainerCleanupError("tracked container identity is invalid")
    config = decoded.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    mounts = decoded.get("Mounts")
    expected_labels = {
        _LABEL_NONCE: owner.attempt_nonce,
        _LABEL_IMAGE: owner.image_digest,
        _LABEL_INPUT: owner.input_mount_sha256,
        _LABEL_OUTPUT: owner.output_mount_sha256,
    }
    if (
        decoded.get("Id") != owner.container_id
        or str(decoded.get("Name", "")).removeprefix("/") != owner.container_name
        or not isinstance(config, dict)
        or config.get("Image") != owner.image_digest
        or not isinstance(labels, dict)
        or any(labels.get(key) != value for key, value in expected_labels.items())
        or not _mounts_match(mounts, owner)
    ):
        raise ContainerCleanupError("tracked container identity is invalid")
    return True


def is_absent(stderr: str) -> bool:
    value = stderr.casefold()
    return any(marker in value for marker in _ABSENT)


def _mounts_match(value: object, owner: RuntimeOwnership) -> bool:
    if not isinstance(value, list):
        return False
    observed: dict[str, str] = {}
    for item in value:
        if not isinstance(item, dict):
            return False
        destination = item.get("Destination")
        source = item.get("Source")
        if destination in {"/input", "/output"} and isinstance(source, str):
            observed[destination] = mount_sha256(Path(source))
    return observed == {
        "/input": owner.input_mount_sha256,
        "/output": owner.output_mount_sha256,
    }


def _canonical_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
