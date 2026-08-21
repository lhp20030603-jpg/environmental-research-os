"""Private no-shell subprocess boundary for reviewed container engines."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime

from envresearch.replication._container_identity import inspect_matches, is_absent
from envresearch.replication._container_models import ResourceMeasurement
from envresearch.replication._runtime_control import (
    bind_engine,
    restore_engine_configuration,
)
from envresearch.replication._runtime_identity import (
    BirthProbe,
    ProcessIdentity,
    capture_process_identity,
    process_birth_sha256,
)
from envresearch.replication._runtime_owner import RuntimeOwnership
from envresearch.replication._runtime_stats import parse_resource_measurement
from envresearch.replication._subprocess_capture import (
    CapturedProcess,
    drain_process,
)
from envresearch.replication.container import (
    CommandExecution,
    CommandExecutor,
    ContainerCleanupError,
    ContainerControl,
    ContainerEngine,
    DockerEngine,
    PodmanEngine,
    ProcessGroupControl,
    ProcessStartedCallback,
    ProgressCallback,
)

_SUPPORTED = {"docker": DockerEngine, "podman": PodmanEngine}
_MAX_CAPTURE_BYTES = 1024 * 1024
_ENVIRONMENT_KEYS = {"LANG", "LC_ALL", "DOCKER_HOST", "CONTAINER_HOST"}


class SubprocessCommandExecutor:
    """Execute one exact argv and bound output by an inactivity deadline."""

    def __init__(
        self,
        *,
        max_capture_bytes: int = _MAX_CAPTURE_BYTES,
        progress_interval_seconds: float = 1.0,
        environment: Mapping[str, str] | None = None,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        monotonic: Callable[[], float] = time.monotonic,
        birth_probe: BirthProbe = process_birth_sha256,
    ) -> None:
        if type(max_capture_bytes) is not int or max_capture_bytes < 1:
            raise ValueError("subprocess capture limit must be a positive integer")
        if (
            isinstance(progress_interval_seconds, bool)
            or not isinstance(progress_interval_seconds, (int, float))
            or progress_interval_seconds <= 0
            or progress_interval_seconds > 60
        ):
            raise ValueError("progress interval must be within 0 and 60 seconds")
        self._max_capture_bytes = max_capture_bytes
        self._progress_interval_seconds = float(progress_interval_seconds)
        self._environment = _require_environment(environment or {})
        self._popen = popen
        self._monotonic = monotonic
        self._birth_probe = birth_probe

    def execute(
        self,
        argv: tuple[str, ...],
        *,
        inactivity_seconds: int,
        on_progress: ProgressCallback | None = None,
        on_started: ProcessStartedCallback | None = None,
        resource_sampler: Callable[[], ResourceMeasurement] | None = None,
    ) -> CommandExecution:
        _require_argv(argv, inactivity_seconds)
        started_at = datetime.now(UTC)
        process = self._popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
            env=self._environment,
        )
        identity: ProcessIdentity | None = None
        try:
            identity = capture_process_identity(process.pid, self._birth_probe)
            if process.stdout is None or process.stderr is None:
                raise RuntimeError("container subprocess pipes are unavailable")
            if on_started is not None:
                on_started(identity)
            capture = self._drain(
                process,
                process.stdout,
                process.stderr,
                inactivity_seconds,
                on_progress,
                resource_sampler,
            )
            PosixProcessGroupControl(self._birth_probe).cleanup(identity)
        except BaseException:
            self._terminate(process, identity)
            raise
        return CommandExecution(
            argv=argv,
            return_code=capture.return_code,
            stdout=capture.stdout.decode("utf-8", errors="replace"),
            stderr=capture.stderr.decode("utf-8", errors="replace"),
            started_at=started_at,
            finished_at=datetime.now(UTC),
            peak_memory_bytes=capture.peak_memory_bytes,
            storage_bytes=capture.storage_bytes,
            stdout_truncated=capture.stdout_truncated,
            stderr_truncated=capture.stderr_truncated,
            resource_status=capture.resource_status,  # type: ignore[arg-type]
            oom_killed=capture.oom_killed,
        )

    def _drain(
        self,
        process: subprocess.Popen[bytes],
        stdout: object,
        stderr: object,
        inactivity_seconds: int,
        on_progress: ProgressCallback | None,
        resource_sampler: Callable[[], ResourceMeasurement] | None,
    ) -> CapturedProcess:
        return drain_process(
            process,
            stdout,  # type: ignore[arg-type]
            stderr,  # type: ignore[arg-type]
            inactivity_seconds=inactivity_seconds,
            progress_interval_seconds=self._progress_interval_seconds,
            max_capture_bytes=self._max_capture_bytes,
            on_progress=on_progress,
            resource_sampler=resource_sampler,
            monotonic=self._monotonic,
        )

    def _terminate(
        self,
        process: subprocess.Popen[bytes],
        identity: ProcessIdentity | None = None,
    ) -> None:
        if identity is not None:
            control = PosixProcessGroupControl(self._birth_probe)
            control.terminate(identity)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired as error:
                raise ContainerCleanupError(
                    "container client process did not terminate after SIGKILL"
                ) from error
            control.verify_absent(identity)
            return
        try:
            process.terminate()
        except (AttributeError, ProcessLookupError):
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except (AttributeError, ProcessLookupError):
                return
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired as error:
                raise ContainerCleanupError(
                    "container client process did not terminate after SIGKILL"
                ) from error


class SubprocessContainerControl:
    """Force-remove then independently prove absence of one tracked container."""

    def __init__(self, executor: CommandExecutor) -> None:
        self._executor = executor

    def authenticate(self, executable: str, owner: RuntimeOwnership) -> bool:
        if (
            type(executable) is not str
            or not executable.startswith("/")
            or type(owner) is not RuntimeOwnership
        ):
            raise ValueError("tracked container cleanup identity is invalid")
        inspected = self._executor.execute(
            (executable, "inspect", owner.container_id), inactivity_seconds=30
        )
        return inspect_matches(inspected, owner)

    def cleanup(self, executable: str, owner: RuntimeOwnership) -> None:
        if not self.authenticate(executable, owner):
            return
        removed = self._executor.execute(
            (executable, "rm", "--force", owner.container_id),
            inactivity_seconds=30,
        )
        if removed.return_code != 0 and not is_absent(removed.stderr):
            raise ContainerCleanupError("container cleanup removal command failed")
        inspected = self._executor.execute(
            (executable, "inspect", owner.container_id), inactivity_seconds=30
        )
        if inspected.return_code == 0:
            raise ContainerCleanupError("tracked container remains after force removal")
        if not is_absent(inspected.stderr):
            raise ContainerCleanupError(
                "container cleanup absence could not be verified"
            )

    def sample(self, executable: str, owner: RuntimeOwnership) -> ResourceMeasurement:
        """Measure an authenticated container through the reviewed executor."""
        inspected = self._executor.execute(
            (executable, "inspect", "--size", owner.container_id),
            inactivity_seconds=30,
        )
        if not inspect_matches(inspected, owner):
            return ResourceMeasurement(None, None, None)
        stats = self._executor.execute(
            (
                executable,
                "stats",
                "--no-stream",
                "--format",
                "{{json .}}",
                owner.container_id,
            ),
            inactivity_seconds=30,
        )
        return parse_resource_measurement(inspected, stats)


class PosixProcessGroupControl:
    """Terminate and independently verify one exact POSIX process group."""

    def __init__(self, birth_probe: BirthProbe = process_birth_sha256) -> None:
        self._birth_probe = birth_probe

    def authenticate(self, owner: RuntimeOwnership | ProcessIdentity) -> bool:
        pid = owner.pid
        pgid = owner.pgid
        expected = (
            owner.process_birth_sha256
            if isinstance(owner, RuntimeOwnership)
            else owner.birth_sha256
        )
        if type(pid) is not int or type(pgid) is not int or pid < 1 or pgid < 1:
            raise ValueError("runtime process-group identity is invalid")
        try:
            observed = self._birth_probe(pid, pgid)
        except ProcessLookupError:
            return False
        if observed != expected:
            raise ContainerCleanupError("runtime process birth identity differs")
        return True

    def cleanup(self, owner: RuntimeOwnership | ProcessIdentity) -> None:
        self.terminate(owner)
        deadline = time.monotonic() + 2
        while self.authenticate(owner) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.verify_absent(owner)

    def terminate(self, owner: RuntimeOwnership | ProcessIdentity) -> None:
        if not self.authenticate(owner):
            return
        try:
            os.killpg(owner.pgid, signal.SIGKILL)
        except ProcessLookupError:
            return

    def verify_absent(self, owner: RuntimeOwnership | ProcessIdentity) -> None:
        if self.authenticate(owner):
            raise ContainerCleanupError("container client process group remains alive")


def select_container_engine(
    configurations: Sequence[object],
    *,
    executor: CommandExecutor | None = None,
    container_control: ContainerControl | None = None,
    process_group_control: ProcessGroupControl | None = None,
) -> ContainerEngine | None:
    """Select only the first available exact reviewed local runtime binding."""
    restored = tuple(restore_engine_configuration(item) for item in configurations)
    identities = tuple(item.identity for item in restored)
    if len(set(identities)) != len(identities):
        raise ValueError("reviewed engine identities must be unique")
    for configuration in restored:
        try:
            binding = bind_engine(configuration)
        except FileNotFoundError:
            continue
        selected_executor = executor or SubprocessCommandExecutor(
            environment=binding.environment
        )
        selected_control = container_control or SubprocessContainerControl(
            selected_executor
        )
        return _SUPPORTED[configuration.identity](
            selected_executor,
            executable=configuration.executable,
            binding=binding,
            container_control=selected_control,
            process_group_control=process_group_control or PosixProcessGroupControl(),
        )
    return None


def _require_environment(values: Mapping[str, str]) -> dict[str, str]:
    environment = dict(values)
    if (
        any(
            type(key) is not str or type(value) is not str
            for key, value in environment.items()
        )
        or not set(environment) <= _ENVIRONMENT_KEYS
        or any(not value or "\x00" in value for value in environment.values())
        or environment.get("LANG", "C") != "C"
        or environment.get("LC_ALL", "C") != "C"
        or {"DOCKER_HOST", "CONTAINER_HOST"} <= set(environment)
    ):
        raise ValueError("subprocess environment is not a minimal reviewed mapping")
    return environment


def _require_argv(argv: tuple[str, ...], inactivity_seconds: int) -> None:
    if (
        type(argv) is not tuple
        or not argv
        or any(type(item) is not str or not item or "\x00" in item for item in argv)
    ):
        raise ValueError("subprocess argv must be an exact nonempty string tuple")
    if type(inactivity_seconds) is not int or inactivity_seconds < 1:
        raise ValueError("subprocess inactivity limit must be a positive integer")


def _process_group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True
