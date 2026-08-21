"""Tests for stock CLI Docker/Podman selection without live execution."""

from __future__ import annotations

import hashlib
import os
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from envresearch.replication._runtime_evidence import restore_runtime_observation
from envresearch.replication._runtime_identity import ProcessIdentity
from envresearch.replication._runtime_owner import RuntimeOwnership
from envresearch.replication._runtime_subprocess import SubprocessCommandExecutor
from envresearch.replication.cli import _service_for_root
from envresearch.replication.container import (
    CommandExecution,
    ContainerInactivityError,
    ContainerPlan,
    DockerEngine,
)
from envresearch.replication.contracts import (
    ContainerRuntimeProfile,
    ReplicationBudget,
)


def _trusted_roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    authority = tmp_path / "run-root"
    input_root = authority / "artifacts/replication/acquired/archive/approval"
    output_root = authority / "artifacts/replication/runs/approval/attempt"
    input_root.mkdir(parents=True)
    output_root.mkdir(parents=True)
    return authority, input_root, output_root


def _author_plan(input_root: Path, output_root: Path) -> ContainerPlan:
    return ContainerPlan.for_author_reproduction(
        ContainerRuntimeProfile(
            profile_id="r-did-v1",
            image_digest=f"example/r@sha256:{'a' * 64}",
            nonroot_uid_gid="1000:1000",
        ),
        input_root,
        output_root,
        ReplicationBudget(
            max_download_bytes=1024,
            max_storage_bytes=1024,
            max_memory_bytes=1024,
            inactivity_seconds=1,
        ),
    )


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def execute(
        self,
        argv: tuple[str, ...],
        *,
        inactivity_seconds: int,
        on_progress=None,
        on_started=None,
        resource_sampler=None,
    ) -> CommandExecution:
        del inactivity_seconds, on_progress, resource_sampler
        self.calls.append(argv)
        if on_started is not None:
            Path(argv[argv.index("--cidfile") + 1]).write_text(
                "c" * 64, encoding="ascii"
            )
            on_started(ProcessIdentity(4242, 4242, "b" * 64))
        now = datetime.now(UTC)
        return CommandExecution(
            argv=argv,
            return_code=0,
            stdout="27.2.1\n",
            stderr="",
            started_at=now,
            finished_at=now,
            peak_memory_bytes=0,
            storage_bytes=0,
        )


class InactiveExecutor(FakeExecutor):
    def execute(  # type: ignore[no-untyped-def]
        self,
        argv,
        *,
        inactivity_seconds,
        on_progress=None,
        on_started=None,
        resource_sampler=None,
    ):
        del inactivity_seconds, on_progress, resource_sampler
        self.calls.append(argv)
        Path(argv[argv.index("--cidfile") + 1]).write_text("c" * 64, encoding="ascii")
        if on_started is not None:
            on_started(ProcessIdentity(4242, 4242, "b" * 64))
        raise ContainerInactivityError("inactive")


class RecordingContainerControl:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, RuntimeOwnership]] = []

    def cleanup(self, executable: str, owner: RuntimeOwnership) -> None:
        self.calls.append((executable, owner))
        if self.fail:
            raise RuntimeError("container cleanup verification failed")


def _reviewed_engine(
    path: Path, *, endpoint: str = "unix:///var/run/docker.sock"
) -> dict[str, str]:
    return {
        "identity": "docker",
        "executable": str(path),
        "executable_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "endpoint": endpoint,
    }


def test_stock_factory_uses_only_exact_reviewed_engine_configuration(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "docker"
    executable.write_text("reviewed", encoding="utf-8")
    executable.chmod(0o700)
    executor = FakeExecutor()
    service = _service_for_root(
        tmp_path / "authority",
        engine_configurations=(_reviewed_engine(executable),),
        executor=executor,
        container_control=RecordingContainerControl(),
    )

    observation = service.engine.preflight(
        ContainerRuntimeProfile(
            profile_id="r-did-v1",
            image_digest=f"example/r@sha256:{'a' * 64}",
            nonroot_uid_gid="1000:1000",
        )
    )

    assert executor.calls[0][0] == str(executable.resolve())
    assert observation.engine == "docker"
    assert (
        observation.executable_sha256
        == _reviewed_engine(executable)["executable_sha256"]
    )
    assert observation.endpoint == "unix:///var/run/docker.sock"


def test_path_impostor_cannot_replace_missing_reviewed_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    impostor = tmp_path / "path-bin/docker"
    impostor.parent.mkdir()
    impostor.write_text("impostor", encoding="utf-8")
    impostor.chmod(0o700)
    monkeypatch.setenv("PATH", str(impostor.parent))
    missing = tmp_path / "reviewed/docker"
    configuration = {
        "identity": "docker",
        "executable": str(missing),
        "executable_sha256": "1" * 64,
        "endpoint": "unix:///var/run/docker.sock",
    }

    service = _service_for_root(
        tmp_path / "authority",
        engine_configurations=(configuration,),
        executor=FakeExecutor(),
        container_control=RecordingContainerControl(),
    )

    assert type(service.engine).__name__ == "_UnavailableEngine"


@pytest.mark.parametrize("unsafe", ["symlink", "writable", "remote"])
def test_unreviewed_executable_or_remote_endpoint_fails_closed(
    tmp_path: Path,
    unsafe: str,
) -> None:
    target = tmp_path / "docker-real"
    target.write_text("reviewed", encoding="utf-8")
    target.chmod(0o700)
    executable = target
    endpoint = "unix:///var/run/docker.sock"
    if unsafe == "symlink":
        executable = tmp_path / "docker"
        executable.symlink_to(target)
    elif unsafe == "writable":
        target.chmod(0o722)
    else:
        endpoint = "tcp://attacker.example:2375"

    with pytest.raises(ValueError):
        _service_for_root(
            tmp_path / "authority",
            engine_configurations=(_reviewed_engine(executable, endpoint=endpoint),),
            executor=FakeExecutor(),
            container_control=RecordingContainerControl(),
        )


def test_reviewed_executable_replacement_is_rejected_before_spawn(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "docker"
    executable.write_text("reviewed", encoding="utf-8")
    executable.chmod(0o700)
    executor = FakeExecutor()
    service = _service_for_root(
        tmp_path / "authority",
        engine_configurations=(_reviewed_engine(executable),),
        executor=executor,
        container_control=RecordingContainerControl(),
    )
    executable.write_text("replaced", encoding="utf-8")

    with pytest.raises(ValueError, match="executable"):
        service.engine.preflight(
            ContainerRuntimeProfile(
                profile_id="r-did-v1",
                image_digest=f"example/r@sha256:{'a' * 64}",
                nonroot_uid_gid="1000:1000",
            )
        )

    assert executor.calls == []


def test_subprocess_uses_minimal_explicit_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class SpawnStopped(RuntimeError):
        pass

    def refuse_spawn(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        raise SpawnStopped(str(argv))

    monkeypatch.setenv("DOCKER_HOST", "tcp://attacker.example:2375")
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")
    monkeypatch.setenv("DOCKER_CONFIG", "/attacker/credentials")
    reviewed = {
        "LANG": "C",
        "LC_ALL": "C",
        "DOCKER_HOST": "unix:///var/run/docker.sock",
    }
    executor = SubprocessCommandExecutor(environment=reviewed, popen=refuse_spawn)

    with pytest.raises(SpawnStopped):
        executor.execute(("/reviewed/docker", "version"), inactivity_seconds=1)

    assert captured["env"] == reviewed
    assert set(captured["env"]) == {"LANG", "LC_ALL", "DOCKER_HOST"}  # type: ignore[arg-type]
    assert os.environ["DOCKER_HOST"].startswith("tcp://")


def test_engine_forces_tracked_container_cleanup_after_inactivity(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "docker"
    executable.write_text("reviewed", encoding="utf-8")
    executable.chmod(0o700)
    executor = InactiveExecutor()
    control = RecordingContainerControl()
    engine = DockerEngine(
        executor,
        executable=executable,
        container_control=control,
    )
    _, input_root, output_root = _trusted_roots(tmp_path)

    with pytest.raises(ContainerInactivityError, match="inactive"):
        engine.run(_author_plan(input_root, output_root))

    argv = executor.calls[-1]
    tracked_name = argv[argv.index("--name") + 1]
    assert control.calls[0][0] == str(executable.resolve())
    assert control.calls[0][1].container_name == tracked_name


def test_engine_verifies_tracked_container_absence_after_success(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "docker"
    executable.write_text("reviewed", encoding="utf-8")
    executable.chmod(0o700)
    executor = FakeExecutor()
    control = RecordingContainerControl()
    engine = DockerEngine(
        executor,
        executable=executable,
        container_control=control,
    )
    _, input_root, output_root = _trusted_roots(tmp_path)

    result = engine.run(_author_plan(input_root, output_root))

    assert result.exit_status == 0
    tracked_name = executor.calls[-1][executor.calls[-1].index("--name") + 1]
    assert control.calls[0][0] == str(executable.resolve())
    assert control.calls[0][1].container_name == tracked_name


def test_subprocess_failure_terminates_and_verifies_the_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[int] = []
    killed = False

    class PipeFailureProcess:
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

    def kill_group(pid: int, sent: int) -> None:
        nonlocal killed
        assert pid == 4242
        signals.append(sent)
        if sent == signal.SIGKILL:
            killed = True

    def birth_probe(pid: int, pgid: int) -> str:
        del pid, pgid
        if killed:
            raise ProcessLookupError
        return "a" * 64

    monkeypatch.setattr(
        "envresearch.replication._runtime_subprocess.os.killpg", kill_group
    )
    executor = SubprocessCommandExecutor(
        popen=lambda *args, **kwargs: PipeFailureProcess(),
        birth_probe=birth_probe,
    )  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="pipes"):
        executor.execute(("/reviewed/docker", "version"), inactivity_seconds=1)

    assert signals == [signal.SIGKILL]


def test_subprocess_reports_periodic_progress_while_child_is_blocked() -> None:
    observations: list[tuple[datetime, int, int]] = []
    executor = SubprocessCommandExecutor(progress_interval_seconds=0.02)

    result = executor.execute(
        (sys.executable, "-c", "import time; time.sleep(0.12)"),
        inactivity_seconds=2,
        on_progress=lambda at, memory, storage: observations.append(
            (at, memory, storage)
        ),
    )

    assert result.return_code == 0
    assert len(observations) >= 2
    assert all(at.tzinfo is UTC for at, _, _ in observations)
    assert all(memory >= 0 and storage >= 0 for _, memory, storage in observations)


@pytest.mark.parametrize(
    "update",
    [
        {"version": ""},
        {"stdout_sha256": "RAW"},
        {"stdout_truncated": 1},
        {"peak_memory_bytes": -1},
        {"started_at": "2026-08-10T08:00:00+08:00"},
        {
            "started_at": "2026-08-10T00:00:01Z",
            "finished_at": "2026-08-10T00:00:00Z",
        },
        {"unexpected": "field"},
    ],
)
def test_persisted_runtime_observation_schema_fails_closed(
    update: dict[str, object],
) -> None:
    payload: dict[str, object] = {
        "engine": "docker",
        "executable_sha256": "e" * 64,
        "endpoint": "unix:///var/run/docker.sock",
        "version": "27.2.1",
        "started_at": "2026-08-10T00:00:00Z",
        "finished_at": "2026-08-10T00:00:01Z",
        "stdout_sha256": "1" * 64,
        "stderr_sha256": "2" * 64,
        "stdout_truncated": False,
        "stderr_truncated": False,
        "peak_memory_bytes": 0,
        "storage_bytes": 0,
    }

    with pytest.raises((TypeError, ValueError)):
        restore_runtime_observation({**payload, **update}, "docker")
