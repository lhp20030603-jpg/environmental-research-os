"""Reviewed generated-script-only boundary for trusted local R analyses."""

from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from envresearch.econometrics._r_authority import require_authority_runtime
from envresearch.econometrics._r_environment import minimal_r_environment
from envresearch.econometrics._r_failure_codes import registered_failure_code
from envresearch.econometrics._r_inputs import inspect_executable, inspect_script
from envresearch.econometrics._r_library_snapshot import execution_library_snapshot
from envresearch.econometrics._r_owned_files import (
    RRuntimeInvalid,
    open_owned_file,
    publish_owned_file,
)
from envresearch.econometrics.contracts import ResourceBudget
from envresearch.econometrics.managed_r_library import MethodAuthorityInvalid
from envresearch.econometrics.r_evidence import (
    EnvironmentEntry,
    GeneratedRScript,
    PackageAuthority,
    RCommandResult,
    RExecutionEvidence,
    RRuntimeIdentity,
)

FORBIDDEN_R = re.compile(
    r"\b(?:source|install\.packages|download\.file|url|curl|system2?|pipe|"
    r"socketconnection|eval|parse|get)\s*\(|\b(?:httr|httr2|remotes|pak|renv)::",
    re.IGNORECASE,
)

__all__ = [
    "FORBIDDEN_R",
    "ManagedAuthorityLibrary",
    "RCommandExecutor",
    "RExecutionFailed",
    "RPackageAuthorityInvalid",
    "RRuntimeInvalid",
    "TrustedLocalRRunner",
]


class RExecutionFailed(RuntimeError):
    """A trusted local R process failed within the approved boundary."""

    def __init__(self, message: str, *, code: str = "R_EXECUTION_FAILED") -> None:
        super().__init__(message)
        self.code = code


class RPackageAuthorityInvalid(RRuntimeInvalid):
    """Managed package evidence or its closed dependency graph changed."""

    code = "R_PACKAGE_AUTHORITY_INVALID"


class RCommandExecutor(Protocol):
    """Injected no-shell command boundary used by the trusted runner."""

    def execute(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
        max_output_bytes: int,
        max_workspace_bytes: int,
        executable_fd: int,
        pass_fds: tuple[int, ...],
    ) -> RCommandResult: ...


class ManagedAuthorityLibrary(Protocol):
    """Small reauthentication surface required by the R runner."""

    root: Path

    def verify(self, authorities: Any) -> Any: ...


class TrustedLocalRRunner:
    """Execute only exact Research-OS-generated scripts with reviewed local R."""

    def __init__(
        self,
        *,
        identity: RRuntimeIdentity,
        workspace: Path,
        executor: RCommandExecutor,
        budget: ResourceBudget,
        approved_scripts: Mapping[str, str],
        managed_library: ManagedAuthorityLibrary | None = None,
        package_authorities: Sequence[PackageAuthority] = (),
    ) -> None:
        self.identity = identity
        self.workspace = workspace
        self.executor = executor
        self.budget = budget
        self.approved_scripts = dict(approved_scripts)
        self.managed_library = managed_library
        self.package_authorities = tuple(package_authorities)

    @classmethod
    def review(
        cls,
        *,
        executable: Path,
        expected_sha256: str,
        workspace: Path,
        executor: RCommandExecutor,
        budget: ResourceBudget,
        approved_scripts: Mapping[str, str],
        managed_library: ManagedAuthorityLibrary | None = None,
        package_authorities: Sequence[PackageAuthority] = (),
    ) -> TrustedLocalRRunner:
        """Authenticate one executable and record its bounded version output."""
        canonical_workspace = _prepare_workspace(workspace)
        if bool(managed_library) != bool(package_authorities):
            raise RRuntimeInvalid(
                "managed library and package authorities must be supplied together"
            )
        _, digest, executable_bytes = inspect_executable(executable)
        if digest != expected_sha256:
            raise RRuntimeInvalid("R executable identity changed from reviewed digest")
        runtime_copy = _snapshot_executable(
            canonical_workspace, executable_bytes, digest
        )
        runtime_fd, copied_metadata = open_owned_file(
            canonical_workspace,
            "runtime",
            runtime_copy.name,
            digest,
            128 * 1024 * 1024,
        )
        try:
            environment = minimal_r_environment(runtime_copy, canonical_workspace)
            version_result = executor.execute(
                (str(runtime_copy), "--version"),
                cwd=canonical_workspace,
                env=environment,
                timeout_seconds=budget.inactivity_seconds,
                max_output_bytes=budget.max_output_bytes,
                max_workspace_bytes=budget.max_workspace_bytes,
                executable_fd=runtime_fd,
                pass_fds=(runtime_fd,),
            )
        except (OSError, TimeoutError) as error:
            raise RRuntimeInvalid(
                "R executable version could not be verified"
            ) from error
        finally:
            os.close(runtime_fd)
        _require_bounded_output(version_result, budget.max_output_bytes)
        version = (
            (version_result.stdout + version_result.stderr)
            .decode("utf-8", errors="replace")
            .strip()
        )
        if version_result.return_code != 0 or not version or "R" not in version:
            raise RRuntimeInvalid("R executable version could not be verified")
        return cls(
            identity=RRuntimeIdentity(
                source_executable=executable,
                executable=runtime_copy,
                sha256=digest,
                version=version,
                device=copied_metadata.st_dev,
                inode=copied_metadata.st_ino,
                size_bytes=copied_metadata.st_size,
            ),
            workspace=canonical_workspace,
            executor=executor,
            budget=budget,
            approved_scripts=approved_scripts,
            managed_library=managed_library,
            package_authorities=package_authorities,
        )

    def run(self, script: GeneratedRScript) -> RExecutionEvidence:
        """Reauthenticate the runtime and execute one exact generated script."""
        runtime_fd, metadata = open_owned_file(
            self.workspace,
            "runtime",
            self.identity.executable.name,
            self.identity.sha256,
            128 * 1024 * 1024,
        )
        if (
            metadata.st_dev != self.identity.device
            or metadata.st_ino != self.identity.inode
            or metadata.st_size != self.identity.size_bytes
        ):
            os.close(runtime_fd)
            raise RRuntimeInvalid("R executable identity changed after review")
        try:
            self._verify_package_authorities()
            script_bytes = inspect_script(script, self.workspace, self.budget)
            if self.approved_scripts.get(script.template_id) != script.sha256:
                raise RRuntimeInvalid("script is not an approved generated script")
            if FORBIDDEN_R.search(script_bytes.decode("utf-8")):
                raise RRuntimeInvalid(
                    "generated script contains a forbidden R capability"
                )
            execution_script = _snapshot_script(self.workspace, script, script_bytes)
            script_fd, _ = open_owned_file(
                self.workspace,
                "execution",
                execution_script.path.name,
                script.sha256,
                self.budget.max_workspace_bytes,
            )
            try:
                if self.managed_library is None:
                    environment = minimal_r_environment(
                        self.identity.executable, self.workspace
                    )
                    result, argv = self._execute(script_fd, runtime_fd, environment)
                else:
                    with execution_library_snapshot(
                        self.managed_library.root,
                        self.package_authorities,
                        self.workspace,
                    ) as library:
                        environment = minimal_r_environment(
                            self.identity.executable,
                            self.workspace,
                            managed_library=library,
                        )
                        result, argv = self._execute(script_fd, runtime_fd, environment)
            finally:
                os.close(script_fd)
        except TimeoutError as error:
            raise RExecutionFailed(
                "R execution exceeded its inactivity limit"
            ) from error
        except OSError as error:
            raise RExecutionFailed("R execution could not start") from error
        finally:
            os.close(runtime_fd)
        _require_bounded_output(result, self.budget.max_output_bytes)
        self._verify_package_authorities()
        workspace_bytes = _workspace_size(
            self.workspace, self.budget.max_workspace_bytes
        )
        if result.return_code != 0:
            output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
            missing_package = "there is no package called" in output.lower()
            scientific_code = registered_failure_code(script.template_id, output)
            raise RExecutionFailed(
                f"R execution returned exit code {result.return_code}",
                code=(
                    scientific_code
                    or (
                        "R_PACKAGE_UNAVAILABLE"
                        if missing_package
                        else "R_EXECUTION_FAILED"
                    )
                ),
            )
        return RExecutionEvidence(
            runtime=self.identity,
            script=execution_script,
            argv=argv,
            environment=tuple(
                EnvironmentEntry(name=name, value=value)
                for name, value in sorted(environment.items())
            ),
            return_code=result.return_code,
            stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
            stderr_sha256=hashlib.sha256(result.stderr).hexdigest(),
            redacted_stdout=_redact(result.stdout, self.workspace),
            redacted_stderr=_redact(result.stderr, self.workspace),
            workspace_bytes=workspace_bytes,
            package_authorities=self.package_authorities,
        )

    def _execute(
        self, script_fd: int, runtime_fd: int, environment: Mapping[str, str]
    ) -> tuple[RCommandResult, tuple[str, ...]]:
        argv = (
            str(self.identity.executable),
            "--vanilla",
            f"/dev/fd/{script_fd}",
        )
        result = self.executor.execute(
            argv,
            cwd=self.workspace,
            env=environment,
            timeout_seconds=self.budget.inactivity_seconds,
            max_output_bytes=self.budget.max_output_bytes,
            max_workspace_bytes=self.budget.max_workspace_bytes,
            executable_fd=runtime_fd,
            pass_fds=(runtime_fd, script_fd),
        )
        return result, argv

    def _verify_package_authorities(self) -> None:
        """Reauthenticate every package source, record, and installed tree."""
        if self.managed_library is None:
            return
        try:
            verified = self.managed_library.verify(self.package_authorities)
        except (MethodAuthorityInvalid, OSError, ValueError) as error:
            raise RPackageAuthorityInvalid("R package authority is invalid") from error
        if tuple(verified) != self.package_authorities:
            raise RPackageAuthorityInvalid("R package authority is invalid")
        try:
            require_authority_runtime(verified, self.identity.version)
        except ValueError as error:
            raise RPackageAuthorityInvalid(str(error)) from error


def _prepare_workspace(workspace: Path) -> Path:
    """Create one absolute non-symlink owned workspace."""
    if not workspace.is_absolute() or (workspace.exists() and workspace.is_symlink()):
        raise RRuntimeInvalid("R workspace must be an absolute non-symlink directory")
    workspace.mkdir(parents=True, exist_ok=True)
    if not workspace.is_dir():
        raise RRuntimeInvalid("R workspace must be an absolute non-symlink directory")
    return workspace.resolve()


def _snapshot_executable(workspace: Path, data: bytes, digest: str) -> Path:
    """Create the exact executable pathname used by the executor."""
    return publish_owned_file(workspace, "runtime", f"Rscript-{digest}", data, 0o555)


def _snapshot_script(
    workspace: Path, script: GeneratedRScript, data: bytes
) -> GeneratedRScript:
    """Copy authenticated script bytes into the protected execution area."""
    destination = publish_owned_file(
        workspace, "execution", f"{script.sha256}.R", data, 0o444
    )
    copied = GeneratedRScript(
        template_id=script.template_id,
        path=destination,
        sha256=script.sha256,
    )
    return copied


def _require_bounded_output(result: RCommandResult, max_bytes: int) -> None:
    """Reject executor results that exceed the declared combined log ceiling."""
    if len(result.stdout) + len(result.stderr) > max_bytes:
        raise RExecutionFailed("R execution exceeded its output budget")


def _workspace_size(workspace: Path, max_bytes: int) -> int:
    """Bounded walk accepting owned directories and regular files only."""
    total = 0
    entries = 0
    max_entries = max(1024, max_bytes // 16)
    deadline = time.monotonic() + 5.0
    pending: list[tuple[Path, int]] = [(workspace, 0)]
    while pending:
        directory, depth = pending.pop()
        if depth > 16 or time.monotonic() > deadline:
            raise RExecutionFailed("R workspace verification exceeded its bound")
        try:
            children = os.scandir(directory)
        except OSError as error:
            raise RExecutionFailed("R workspace could not be verified") from error
        with children:
            for child in children:
                if time.monotonic() > deadline:
                    raise RExecutionFailed(
                        "R workspace verification exceeded its bound"
                    )
                entries += 1
                if entries > max_entries:
                    raise RExecutionFailed("R workspace contains too many entries")
                if child.is_symlink():
                    raise RExecutionFailed("R workspace contains a symlink")
                if child.is_dir(follow_symlinks=False):
                    pending.append((Path(child.path), depth + 1))
                elif child.is_file(follow_symlinks=False):
                    total += child.stat(follow_symlinks=False).st_size
                else:
                    raise RExecutionFailed("R workspace contains a nonregular entry")
                if total > max_bytes:
                    raise RExecutionFailed("R execution exceeded its workspace budget")
    return total


def _redact(data: bytes, workspace: Path) -> str:
    """Decode bounded logs and redact the owned absolute workspace path."""
    return data.decode("utf-8", errors="replace").replace(str(workspace), "<workspace>")
