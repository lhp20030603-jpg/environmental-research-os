"""Non-reusable process and container identity containment regressions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from envresearch.replication._runtime_owner import RuntimeOwnership
from envresearch.replication._runtime_subprocess import (
    PosixProcessGroupControl,
    SubprocessContainerControl,
)
from envresearch.replication.container import CommandExecution, ContainerCleanupError

IMAGE = f"example/r@sha256:{'d' * 64}"


def _owner() -> RuntimeOwnership:
    payload = {
        "engine": "docker",
        "pid": 4242,
        "pgid": 4242,
        "process_birth_sha256": "a" * 64,
        "attempt_nonce": "b" * 64,
        "container_name": f"envresearch-{'b' * 24}",
        "container_id": "c" * 64,
        "image_digest": IMAGE,
        "input_mount_sha256": "e" * 64,
        "output_mount_sha256": "f" * 64,
        "started_at": datetime(2026, 8, 10, tzinfo=UTC),
    }
    try:
        return RuntimeOwnership.model_validate(payload)
    except ValidationError as error:
        pytest.fail(f"runtime owner lacks non-reusable identity fields: {error}")


def test_process_birth_mismatch_never_signals_reused_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "envresearch.replication._runtime_subprocess.os.killpg",
        lambda pgid, signal: signals.append((pgid, signal)),
    )
    control = PosixProcessGroupControl(
        birth_probe=lambda pid, pgid: "0" * 64  # type: ignore[call-arg]
    )

    with pytest.raises(ContainerCleanupError, match="birth identity"):
        control.cleanup(_owner())  # type: ignore[arg-type]

    assert signals == []


class _InspectMismatchExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def execute(self, argv, *, inactivity_seconds, **kwargs):  # type: ignore[no-untyped-def]
        del inactivity_seconds, kwargs
        self.calls.append(argv)
        now = datetime.now(UTC)
        return CommandExecution(
            argv,
            0,
            '{"Id":"' + "c" * 64 + '","Config":{"Labels":{}}}',
            "",
            now,
            now,
            1,
            1,
        )


def test_container_identity_mismatch_is_inspected_before_touching_container() -> None:
    executor = _InspectMismatchExecutor()
    control = SubprocessContainerControl(executor)

    with pytest.raises(ContainerCleanupError, match="identity"):
        control.cleanup("/reviewed/docker", _owner())  # type: ignore[arg-type]

    assert executor.calls
    assert executor.calls[0][1] == "inspect"
    assert all("rm" not in call for call in executor.calls)
