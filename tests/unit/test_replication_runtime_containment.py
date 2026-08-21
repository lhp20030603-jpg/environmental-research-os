"""Containment failure regressions for the reviewed subprocess boundary."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from envresearch.replication._runtime_identity import mount_sha256
from envresearch.replication._runtime_owner import RuntimeOwnership
from envresearch.replication._runtime_subprocess import (
    SubprocessCommandExecutor,
    SubprocessContainerControl,
)
from envresearch.replication._subprocess_capture import CapturedProcess
from envresearch.replication.container import CommandExecution, ContainerCleanupError


class _PipeFailureProcess:
    pid = 4242
    stdout = None
    stderr = None

    @staticmethod
    def poll() -> None:
        return None

    @staticmethod
    def wait(timeout=None) -> int:  # type: ignore[no-untyped-def]
        del timeout
        return 0


def _birth_probe(killed: list[bool]):
    def probe(pid: int, pgid: int) -> str:
        del pid, pgid
        if killed[0]:
            raise ProcessLookupError
        return "a" * 64

    return probe


def test_termination_escalates_for_a_stubborn_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[int] = []
    killed = [False]

    def kill_group(pid: int, sent: int) -> None:
        assert pid == 4242
        signals.append(sent)
        if sent == 9:
            killed[0] = True

    monkeypatch.setattr(
        "envresearch.replication._runtime_subprocess.os.killpg", kill_group
    )
    executor = SubprocessCommandExecutor(
        popen=lambda *args, **kwargs: _PipeFailureProcess(),
        birth_probe=_birth_probe(killed),
    )  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="pipes"):
        executor.execute(("/reviewed/docker", "version"), inactivity_seconds=1)

    assert signals == [9]


def test_process_group_kill_wait_remains_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float | None] = []

    class SlowProcess(_PipeFailureProcess):
        @staticmethod
        def wait(timeout=None) -> int:  # type: ignore[no-untyped-def]
            waits.append(timeout)
            if len(waits) == 1:
                raise subprocess.TimeoutExpired("runtime", timeout)
            return 0

    monkeypatch.setattr(
        "envresearch.replication._runtime_subprocess.os.killpg",
        lambda pid, sent: None,
    )
    executor = SubprocessCommandExecutor(
        popen=lambda *args, **kwargs: SlowProcess(),
        birth_probe=lambda pid, pgid: "a" * 64,
    )  # type: ignore[arg-type]

    with pytest.raises(ContainerCleanupError, match="SIGKILL"):
        executor.execute(("/reviewed/docker", "version"), inactivity_seconds=1)

    assert waits == [2]


def test_uncontained_process_group_is_a_typed_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "envresearch.replication._runtime_subprocess.os.killpg",
        lambda pid, sent: None,
    )
    executor = SubprocessCommandExecutor(
        popen=lambda *args, **kwargs: _PipeFailureProcess(),
        birth_probe=lambda pid, pgid: "a" * 64,
    )  # type: ignore[arg-type]

    with pytest.raises(ContainerCleanupError, match="process group"):
        executor.execute(("/reviewed/docker", "version"), inactivity_seconds=1)


def test_successful_client_still_contains_residual_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[int] = []
    killed = [False]

    class SuccessfulProcess(_PipeFailureProcess):
        stdout = object()
        stderr = object()

    def kill_group(pid: int, sent: int) -> None:
        signals.append(sent)
        if sent == 9:
            killed[0] = True

    monkeypatch.setattr(
        "envresearch.replication._runtime_subprocess.os.killpg", kill_group
    )
    executor = SubprocessCommandExecutor(
        popen=lambda *args, **kwargs: SuccessfulProcess(),
        birth_probe=_birth_probe(killed),
    )  # type: ignore[arg-type]
    monkeypatch.setattr(
        executor,
        "_drain",
        lambda *args, **kwargs: CapturedProcess(
            b"", b"", False, False, 0, None, None, "unknown", None
        ),
    )

    result = executor.execute(("/reviewed/docker", "version"), inactivity_seconds=1)

    assert result.return_code == 0
    assert signals == [9]


def _owner() -> RuntimeOwnership:
    image = f"example/r@sha256:{'d' * 64}"
    return RuntimeOwnership(
        engine="docker",
        pid=4242,
        pgid=4242,
        process_birth_sha256="a" * 64,
        attempt_nonce="b" * 64,
        container_name=f"envresearch-{'b' * 24}",
        container_id="c" * 64,
        image_digest=image,
        input_mount_sha256=mount_sha256(Path("/input-source")),
        output_mount_sha256=mount_sha256(Path("/output-source")),
        started_at=datetime.now(UTC),
    )


def _inspect() -> str:
    owner = _owner()
    return json.dumps(
        {
            "Id": owner.container_id,
            "Name": f"/{owner.container_name}",
            "Config": {
                "Image": owner.image_digest,
                "Labels": {
                    "io.envresearch.attempt-nonce": owner.attempt_nonce,
                    "io.envresearch.image-digest": owner.image_digest,
                    "io.envresearch.input-mount-sha256": owner.input_mount_sha256,
                    "io.envresearch.output-mount-sha256": owner.output_mount_sha256,
                },
            },
            "Mounts": [
                {"Source": "/input-source", "Destination": "/input"},
                {"Source": "/output-source", "Destination": "/output"},
            ],
        }
    )


class _SequencedExecutor:
    def __init__(self, responses: list[tuple[int, str, str]]) -> None:
        self.responses = responses

    def execute(self, argv, *, inactivity_seconds, on_progress=None):  # type: ignore[no-untyped-def]
        del inactivity_seconds, on_progress
        return_code, stdout, stderr = self.responses.pop(0)
        now = datetime.now(UTC)
        return CommandExecution(argv, return_code, stdout, stderr, now, now, 0, 0)


@pytest.mark.parametrize(
    "responses",
    [
        [(1, "", "permission denied")],
        [(0, _inspect(), ""), (1, "", "permission denied")],
        [
            (0, _inspect(), ""),
            (0, "", ""),
            (1, "", "daemon connection failed"),
        ],
    ],
)
def test_container_cleanup_rejects_unproven_absence(
    responses: list[tuple[int, str, str]],
) -> None:
    control = SubprocessContainerControl(_SequencedExecutor(responses))

    with pytest.raises(RuntimeError, match="cleanup"):
        control.cleanup("/reviewed/docker", _owner())


def test_container_cleanup_accepts_only_removed_known_absent_container() -> None:
    control = SubprocessContainerControl(
        _SequencedExecutor(
            [
                (0, _inspect(), ""),
                (0, "", ""),
                (1, "", "Error: No such container: target"),
            ]
        )
    )

    control.cleanup("/reviewed/docker", _owner())
