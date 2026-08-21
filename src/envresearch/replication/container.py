"""Fail-closed Docker and Podman boundary for Tier-2 replication packages."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from envresearch.models.benchmark import CommandSpec, validate_command_environment
from envresearch.replication import _container_models as models
from envresearch.replication import _container_validation as validation
from envresearch.replication._container_identity import (
    bind_owner,
    read_container_id,
)
from envresearch.replication._container_lifecycle import (
    ContainerCleanupError as _ContainerCleanupError,
)
from envresearch.replication._container_lifecycle import (
    cleanup_container,
    contain_runtime,
)
from envresearch.replication._container_support import (
    _bounded_log,
    _require_within_budget,
    _sha256,
    _validate_execution,
    _validate_generated_files,
)
from envresearch.replication._runtime_control import EngineBinding
from envresearch.replication._runtime_identity import ProcessIdentity
from envresearch.replication._runtime_owner import (
    RuntimeLaunchIdentity,
    RuntimeOwnership,
)
from envresearch.replication.contracts import ContainerRuntimeProfile, ReplicationBudget

_TMPFS = "/tmp:rw,noexec,nosuid,size=512m"
_DEFAULT_MAX_LOG_BYTES = 1024 * 1024
_DEFAULT_PREFLIGHT_INACTIVITY_SECONDS = 60
ProgressCallback = Callable[[datetime, int, int], None]
ProcessStartedCallback = Callable[[ProcessIdentity], None]
RuntimeStartedCallback = Callable[[RuntimeLaunchIdentity | RuntimeOwnership], None]
RuntimeStoppedCallback = Callable[[], None]
allocate_output_namespace = validation.allocate_output_namespace
CommandExecution = models.CommandExecution
ContainerCleanupError = _ContainerCleanupError
ContainerResult = models.ContainerResult
RuntimeObservation = models.RuntimeObservation


class ContainerInactivityError(TimeoutError):
    """The runtime emitted no progress within the approved inactivity window."""


class ContainerControl(Protocol):
    """Force-remove and verify absence of one exact tracked container."""

    def authenticate(self, executable: str, owner: RuntimeOwnership) -> bool: ...

    def cleanup(self, executable: str, owner: RuntimeOwnership) -> None: ...

    def sample(
        self, executable: str, owner: RuntimeOwnership
    ) -> models.ResourceMeasurement: ...


class ProcessGroupControl(Protocol):
    """Terminate and verify absence of one authenticated process group."""

    def authenticate(self, owner: RuntimeOwnership) -> bool: ...

    def cleanup(self, owner: RuntimeOwnership) -> None: ...


class CommandExecutor(Protocol):
    """Narrow process boundary; no local command implementation is supplied."""

    def execute(
        self,
        argv: tuple[str, ...],
        *,
        inactivity_seconds: int,
        on_progress: ProgressCallback | None = None,
        on_started: ProcessStartedCallback | None = None,
        resource_sampler: Callable[[], models.ResourceMeasurement] | None = None,
    ) -> CommandExecution:
        """Run the supplied container command with an inactivity cap."""


@dataclass(frozen=True, slots=True)
class ContainerPlan:
    """Immutable isolated-container plan with a validated resource budget."""

    image_digest: str
    user: str
    input_root: Path
    output_root: Path
    argv: tuple[str, ...]
    output_namespace: str
    budget: ReplicationBudget
    generated_files: Mapping[str, str] = field(default_factory=dict)
    environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validation.validate_profile_values(self.image_digest, self.user)
        validation.validate_budget(self.budget)
        if not self.argv or any(not argument.strip() for argument in self.argv):
            raise ValueError("container argv must be nonempty")
        if not self.output_namespace.strip():
            raise ValueError("output namespace must be nonblank")
        validate_command_environment(self.environment)
        _validate_generated_files(self.generated_files)
        input_root, output_root = validation.validate_trusted_roots(
            self.input_root, self.output_root
        )
        object.__setattr__(self, "input_root", input_root)
        object.__setattr__(self, "output_root", output_root)
        object.__setattr__(self, "argv", tuple(self.argv))
        object.__setattr__(
            self, "generated_files", MappingProxyType(dict(self.generated_files))
        )
        object.__setattr__(
            self, "environment", MappingProxyType(dict(self.environment))
        )

    @classmethod
    def for_author_reproduction(
        cls,
        profile: ContainerRuntimeProfile,
        input_root: Path,
        output_root: Path,
        budget: ReplicationBudget,
        *,
        command: CommandSpec | None = None,
    ) -> ContainerPlan:
        """Create the separate namespace used only for author reproduction."""
        command = command or CommandSpec(argv=["Rscript", "/input/reproduce.R"])
        return cls(
            image_digest=profile.image_digest,
            user=profile.nonroot_uid_gid,
            input_root=input_root,
            output_root=output_root,
            argv=tuple(command.argv),
            output_namespace="author-reproduction",
            budget=budget,
            environment=command.env,
        )


class ContainerEngine(Protocol):
    """The only runtime interface exposed to Tier-2 orchestration."""

    @property
    def identity(self) -> str: ...

    @property
    def executable_sha256(self) -> str: ...

    @property
    def endpoint(self) -> str: ...

    def preflight(self, profile: ContainerRuntimeProfile) -> RuntimeObservation: ...

    def run(
        self,
        plan: ContainerPlan,
        *,
        on_progress: ProgressCallback | None = None,
        on_started: RuntimeStartedCallback | None = None,
        on_stopped: RuntimeStoppedCallback | None = None,
    ) -> ContainerResult: ...

    def contain(
        self, owner: RuntimeOwnership | None, names: tuple[str, ...]
    ) -> None: ...


class _ContainerEngine:
    """Shared Docker-compatible command construction with no fallback path."""

    binary: str

    def __init__(
        self,
        executor: CommandExecutor,
        *,
        executable: Path | None = None,
        binding: EngineBinding | None = None,
        container_control: ContainerControl | None = None,
        process_group_control: ProcessGroupControl | None = None,
        max_log_bytes: int = _DEFAULT_MAX_LOG_BYTES,
        preflight_inactivity_seconds: int = _DEFAULT_PREFLIGHT_INACTIVITY_SECONDS,
        nonce_factory: Callable[[], str] | None = None,
    ) -> None:
        if max_log_bytes < 1 or preflight_inactivity_seconds < 1:
            raise ValueError("container engine limits must be positive")
        self._executor = executor
        self._container_control = container_control
        self._process_group_control = process_group_control
        self._binding = binding
        self._executable = (
            validation.require_executable(executable)
            if executable is not None
            else self.binary
        )
        if binding is not None:
            if (
                binding.configuration.identity != self.binary
                or str(binding.configuration.executable) != self._executable
            ):
                raise ValueError("reviewed engine binding differs from runtime")
            binding.require_current()
        self._max_log_bytes = max_log_bytes
        self._preflight_inactivity_seconds = preflight_inactivity_seconds
        self._nonce_factory = nonce_factory

    def preflight(self, profile: ContainerRuntimeProfile) -> RuntimeObservation:
        """Fail closed if profile validation or the engine version probe fails."""
        self._require_reviewed_current()
        validation.validate_profile_values(
            profile.image_digest, profile.nonroot_uid_gid
        )
        execution = self._executor.execute(
            (self._executable, "version", "--format", "{{.Server.Version}}"),
            inactivity_seconds=self._preflight_inactivity_seconds,
        )
        stdout, bounded_stdout = _bounded_log(execution.stdout, self._max_log_bytes)
        stderr, bounded_stderr = _bounded_log(execution.stderr, self._max_log_bytes)
        stdout_truncated = execution.stdout_truncated or bounded_stdout
        stderr_truncated = execution.stderr_truncated or bounded_stderr
        if execution.return_code != 0 or not stdout.strip():
            raise RuntimeError(f"container engine '{self.binary}' is unavailable")
        _validate_execution(execution)
        return RuntimeObservation(
            engine=self.binary,
            executable_sha256=self.executable_sha256,
            endpoint=self.endpoint,
            version=stdout.strip(),
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            stdout_sha256=_sha256(stdout),
            stderr_sha256=_sha256(stderr),
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            peak_memory_bytes=execution.peak_memory_bytes,
            storage_bytes=execution.storage_bytes,
            resource_status=execution.resource_status,
            oom_killed=execution.oom_killed,
        )

    @property
    def identity(self) -> str:
        """Return the canonical engine name, separate from its executable path."""
        return self.binary

    @property
    def executable_sha256(self) -> str:
        """Return the reviewed executable digest bound to runtime evidence."""
        if self._binding is None:
            return "0" * 64
        return self._binding.configuration.executable_sha256

    @property
    def endpoint(self) -> str:
        """Return the explicit reviewed local daemon endpoint."""
        if self._binding is None:
            return ""
        return self._binding.configuration.endpoint

    def build_argv(self, plan: ContainerPlan) -> tuple[str, ...]:
        """Construct exactly two safe bind mounts and hard resource constraints."""
        from envresearch.replication._container_command import prepare_run

        self._require_reviewed_current()
        validation.validate_plan(plan)
        _, argv = prepare_run(
            self._executable,
            self.binary,
            plan,
            _TMPFS,
            self._nonce_factory,
        )
        return argv

    def run(
        self,
        plan: ContainerPlan,
        *,
        on_progress: ProgressCallback | None = None,
        on_started: RuntimeStartedCallback | None = None,
        on_stopped: RuntimeStoppedCallback | None = None,
    ) -> ContainerResult:
        """Use the injected executor and reject observed budget violations."""
        from envresearch.replication._container_command import prepare_run

        self._require_reviewed_current()
        validation.validate_plan(plan)
        launch, argv = prepare_run(
            self._executable,
            self.binary,
            plan,
            _TMPFS,
            self._nonce_factory,
        )
        owner: RuntimeOwnership | None = None
        if on_started is not None:
            on_started(launch)

        def bind_process(process: ProcessIdentity) -> None:
            nonlocal owner
            owner = bind_owner(launch, process, read_container_id(launch))
            if on_started is not None:
                on_started(owner)

        def sample_resources() -> models.ResourceMeasurement:
            if owner is None or self._container_control is None:
                return models.ResourceMeasurement(None, None, None)
            return self._container_control.sample(self._executable, owner)

        try:
            execution = self._executor.execute(
                argv,
                inactivity_seconds=plan.budget.inactivity_seconds,
                on_progress=on_progress,
                on_started=bind_process,
                resource_sampler=sample_resources,
            )
            _validate_execution(execution)
            _require_within_budget(execution, plan.budget)
        except BaseException as error:
            if owner is None:
                raise ContainerCleanupError(
                    "runtime owner could not be bound before containment"
                ) from error
            cleanup_container(
                self._container_control, self._executable, owner, cause=error
            )
            if on_stopped is not None and not isinstance(error, ContainerCleanupError):
                on_stopped()
            raise
        if owner is None:
            raise ContainerCleanupError("runtime owner was not bound by executor")
        cleanup_container(self._container_control, self._executable, owner)
        if on_stopped is not None:
            on_stopped()
        stdout, bounded_stdout = _bounded_log(execution.stdout, self._max_log_bytes)
        stderr, bounded_stderr = _bounded_log(execution.stderr, self._max_log_bytes)
        stdout_truncated = execution.stdout_truncated or bounded_stdout
        stderr_truncated = execution.stderr_truncated or bounded_stderr
        return ContainerResult(
            engine=self.binary,
            image_digest=plan.image_digest,
            exit_status=execution.return_code,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            stdout_sha256=_sha256(stdout),
            stderr_sha256=_sha256(stderr),
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            peak_memory_bytes=execution.peak_memory_bytes,
            storage_bytes=execution.storage_bytes,
            resource_status=execution.resource_status,
            oom_killed=execution.oom_killed,
        )

    def contain(self, owner: RuntimeOwnership | None, names: tuple[str, ...]) -> None:
        """Contain a crash-retained owner before any recovery publication."""
        self._require_reviewed_current()
        contain_runtime(
            self._process_group_control,
            self._container_control,
            self._executable,
            self.binary,
            owner,
            names,
        )

    def _require_reviewed_current(self) -> None:
        if self._binding is not None:
            self._binding.require_current()


class DockerEngine(_ContainerEngine):
    """Docker implementation with no local execution fallback."""

    binary = "docker"


class PodmanEngine(_ContainerEngine):
    """Podman implementation with the identical isolation contract."""

    binary = "podman"
