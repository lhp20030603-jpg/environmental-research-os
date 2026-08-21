"""Restricted command execution for trusted local benchmark packages.

This runner prevents accidental command-selection, environment, and workspace
boundary mistakes in v0.1 benchmark packages. It is not a hostile-code sandbox:
untrusted packages require external container or operating-system isolation.
Windows Job Objects, cgroups, escaping-descendant containment, and
race-resistant filesystem sandboxing are intentionally outside this scope.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, cast

from envresearch.models.benchmark import CommandSpec, validate_command_environment
from envresearch.runner.capture import BoundedPipeCapture
from envresearch.storage.paths import safe_join

_EXECUTABLE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SYSTEM_ENVIRONMENT_VARIABLES = ("PATH", "SYSTEMROOT", "SystemRoot")
_DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024
_DEFAULT_TIMEOUT_CLEANUP_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Structured outcome of one command, including bounded capture metadata."""

    argv: list[str]
    cwd: Path
    return_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    started_at: datetime
    finished_at: datetime
    timed_out: bool


class CommandRunner:
    """Run trusted-package commands through pinned executable paths only.

    This API is not a security sandbox for hostile package code. On Windows,
    timeout cleanup terminates only the direct process; containment of child
    processes requires external isolation.
    """

    def __init__(
        self,
        allowed_executables: Iterable[str] | Mapping[str, Path],
        *,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
        timeout_cleanup_seconds: float = _DEFAULT_TIMEOUT_CLEANUP_SECONDS,
    ) -> None:
        if max_output_bytes < 1:
            raise ValueError("max_output_bytes must be at least 1")
        if timeout_cleanup_seconds <= 0:
            raise ValueError("timeout_cleanup_seconds must be positive")

        self._pinned_executables = self._pin_executables(allowed_executables)
        self._max_output_bytes = max_output_bytes
        self._timeout_cleanup_seconds = timeout_cleanup_seconds
        self._system_environment = {
            name: os.environ[name]
            for name in _SYSTEM_ENVIRONMENT_VARIABLES
            if name in os.environ
        }

    def run(self, spec: CommandSpec, workspace: Path) -> CommandResult:
        """Execute *spec* within *workspace* and return a structured outcome."""
        executable = spec.argv[0]
        pinned_executable = self._pinned_executables.get(executable)
        if pinned_executable is None:
            raise PermissionError(f"executable '{executable}' is not allowed")

        self._validate_environment(spec.env)
        resolved_cwd = safe_join(workspace, spec.cwd)
        environment = {**self._system_environment, **spec.env}
        started_at = datetime.now(UTC)
        process = subprocess.Popen(
            [str(pinned_executable), *spec.argv[1:]],
            cwd=resolved_cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_capture = BoundedPipeCapture(
            cast(BinaryIO, process.stdout), self._max_output_bytes
        )
        stderr_capture = BoundedPipeCapture(
            cast(BinaryIO, process.stderr), self._max_output_bytes
        )
        stdout_capture.start()
        stderr_capture.start()
        try:
            process.wait(timeout=spec.timeout_seconds)
        except subprocess.TimeoutExpired:
            self._terminate_after_timeout(process)
            return_code: int | None = None
            timed_out = True
        else:
            return_code = process.returncode
            timed_out = False

        capture_deadline = time.monotonic() + self._timeout_cleanup_seconds
        stdout, stdout_truncated = stdout_capture.finish(
            capture_deadline - time.monotonic()
        )
        stderr, stderr_truncated = stderr_capture.finish(
            capture_deadline - time.monotonic()
        )

        return CommandResult(
            argv=list(spec.argv),
            cwd=resolved_cwd,
            return_code=return_code,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            timed_out=timed_out,
        )

    @staticmethod
    def _pin_executables(
        allowed_executables: Iterable[str] | Mapping[str, Path],
    ) -> dict[str, Path]:
        """Bind tokens to trusted absolute executable paths at construction."""
        configured_executables: Iterable[tuple[str, Path]]
        if isinstance(allowed_executables, Mapping):
            configured_executables = allowed_executables.items()
        else:
            tokens = frozenset(allowed_executables)
            unsupported_tokens = tokens - {"python"}
            if unsupported_tokens:
                raise ValueError(
                    "non-Python executable tokens require an explicit path mapping"
                )
            configured_executables = ((token, Path(sys.executable)) for token in tokens)

        pinned_executables: dict[str, Path] = {}
        for token, executable_path in configured_executables:
            if not _EXECUTABLE_TOKEN.fullmatch(token):
                raise ValueError("executable token must be a bare command name")
            if not executable_path.is_absolute():
                raise ValueError("trusted executable path must be an absolute path")
            resolved_path = executable_path.resolve()
            if not resolved_path.is_file() or not os.access(resolved_path, os.X_OK):
                raise ValueError("trusted executable path must be executable")
            pinned_executables[token] = resolved_path
        return pinned_executables

    @staticmethod
    def _validate_environment(environment: Mapping[str, str]) -> None:
        """Recheck default-deny manifest variables before process creation."""
        validate_command_environment(environment)

    def _terminate_after_timeout(self, process: subprocess.Popen[bytes]) -> None:
        """Try bounded cleanup; POSIX groups do not contain escaped descendants."""
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()

        try:
            process.wait(timeout=self._timeout_cleanup_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=self._timeout_cleanup_seconds)
            except subprocess.TimeoutExpired:
                pass
