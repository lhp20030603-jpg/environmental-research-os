"""Bounded no-shell subprocess implementation for trusted local R."""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import time
import uuid
from collections.abc import Mapping
from pathlib import Path

from envresearch.econometrics.r_evidence import RCommandResult
from envresearch.econometrics.r_runtime import RExecutionFailed


class BoundedRSubprocessExecutor:
    """Run exact argv with live output, inactivity, and workspace ceilings."""

    def __init__(self, *, poll_seconds: float = 0.05) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll interval must be positive")
        self.poll_seconds = poll_seconds

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
    ) -> RCommandResult:
        """Execute exact argv without a shell and return bounded raw output."""
        if not argv or not Path(argv[0]).is_absolute():
            raise RExecutionFailed("R executor requires an absolute executable")
        executable_identity = _require_executable_identity(argv[0], executable_fd)
        stdout_path = cwd / f".r-command-{uuid.uuid4().hex}.stdout"
        stderr_path = cwd / f".r-command-{uuid.uuid4().hex}.stderr"
        process: subprocess.Popen[bytes] | None = None
        try:
            with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
                process = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    env=dict(env),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    shell=False,
                    close_fds=True,
                    pass_fds=pass_fds,
                    start_new_session=True,
                )
                if (
                    _require_executable_identity(argv[0], executable_fd)
                    != executable_identity
                ):
                    _stop(process)
                    raise RExecutionFailed("R executable changed during process launch")
                self._monitor(
                    process,
                    cwd=cwd,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    timeout_seconds=timeout_seconds,
                    max_output_bytes=max_output_bytes,
                    max_workspace_bytes=max_workspace_bytes,
                )
                _contain_process_group(process)
            stdout_bytes = _read_bounded(stdout_path, max_output_bytes)
            remaining = max_output_bytes - len(stdout_bytes)
            stderr_bytes = _read_bounded(stderr_path, remaining)
            return RCommandResult(
                return_code=process.returncode,
                stdout=stdout_bytes,
                stderr=stderr_bytes,
            )
        except RExecutionFailed:
            if process is not None and process.poll() is None:
                _stop(process)
            raise
        except OSError as error:
            if process is not None and process.poll() is None:
                _stop(process)
            raise RExecutionFailed("R subprocess could not be executed") from error
        finally:
            stdout_path.unlink(missing_ok=True)
            stderr_path.unlink(missing_ok=True)

    def _monitor(
        self,
        process: subprocess.Popen[bytes],
        *,
        cwd: Path,
        stdout_path: Path,
        stderr_path: Path,
        timeout_seconds: int,
        max_output_bytes: int,
        max_workspace_bytes: int,
    ) -> None:
        """Poll a trusted process while enforcing live declared ceilings."""
        last_activity = time.monotonic()
        previous_output = 0
        while process.poll() is None:
            output_size = _file_size(stdout_path) + _file_size(stderr_path)
            if output_size != previous_output:
                previous_output = output_size
                last_activity = time.monotonic()
            if output_size > max_output_bytes:
                _stop(process)
                raise RExecutionFailed("R execution exceeded its output budget")
            if _workspace_bytes(cwd, max_workspace_bytes) > max_workspace_bytes:
                _stop(process)
                raise RExecutionFailed("R execution exceeded its workspace budget")
            if time.monotonic() - last_activity > timeout_seconds:
                _stop(process)
                raise RExecutionFailed("R execution exceeded its inactivity limit")
            time.sleep(self.poll_seconds)
        output_size = _file_size(stdout_path) + _file_size(stderr_path)
        if output_size > max_output_bytes:
            raise RExecutionFailed("R execution exceeded its output budget")
        if _workspace_bytes(cwd, max_workspace_bytes) > max_workspace_bytes:
            raise RExecutionFailed("R execution exceeded its workspace budget")


def _stop(process: subprocess.Popen[bytes]) -> None:
    """Boundedly terminate the complete process group created for local R."""
    _contain_process_group(process)


def _contain_process_group(process: subprocess.Popen[bytes]) -> None:
    """Prove the complete newly-created group absent before returning."""
    group_id = process.pid
    if not _group_exists(group_id):
        _reap_if_exited(process)
        return
    try:
        os.killpg(group_id, signal.SIGTERM)
    except ProcessLookupError:
        _reap_if_exited(process)
        return
    if not _wait_for_group_exit(process, group_id, timeout_seconds=2.0):
        try:
            os.killpg(group_id, signal.SIGKILL)
        except ProcessLookupError:
            _reap_if_exited(process)
            return
    if not _wait_for_group_exit(process, group_id, timeout_seconds=2.0):
        raise RExecutionFailed("R process group could not be stopped")


def _wait_for_group_exit(
    process: subprocess.Popen[bytes], group_id: int, *, timeout_seconds: float
) -> bool:
    """Reap the leader while waiting for its complete process group to vanish."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        _reap_if_exited(process)
        if not _group_exists(group_id):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)


def _reap_if_exited(process: subprocess.Popen[bytes]) -> None:
    """Non-blockingly reap one exited group leader on platforms retaining zombies."""
    try:
        process.wait(timeout=0)
    except subprocess.TimeoutExpired:
        pass


def _group_exists(group_id: int) -> bool:
    """Return whether any process remains in one newly-created group."""
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The original same-user group is gone; never signal a reused foreign group.
        return False
    return True


def _file_size(path: Path) -> int:
    """Return one current regular-file size."""
    try:
        metadata = path.stat()
    except FileNotFoundError:
        return 0
    if not stat.S_ISREG(metadata.st_mode):
        raise RExecutionFailed("R command output is not a regular file")
    return metadata.st_size


def _require_executable_identity(path: str, descriptor: int) -> tuple[int, int, int]:
    """Bind the launch pathname to the already-authenticated open file."""
    try:
        opened = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise RExecutionFailed(
            "R executable snapshot could not be authenticated"
        ) from error
    identity = (opened.st_dev, opened.st_ino, opened.st_size)
    if identity != (current.st_dev, current.st_ino, current.st_size):
        raise RExecutionFailed("R executable changed before process launch")
    return identity


def _read_bounded(path: Path, max_bytes: int) -> bytes:
    """Read one completed output file under its remaining ceiling."""
    if max_bytes < 0 or _file_size(path) > max_bytes:
        raise RExecutionFailed("R execution exceeded its output budget")
    return path.read_bytes()


def _workspace_bytes(root: Path, max_bytes: int) -> int:
    """Measure regular workspace bytes with a bounded entry count."""
    total = 0
    entries = 0
    pending: list[tuple[Path, int]] = [(root, 0)]
    max_entries = max(1024, max_bytes // 16)
    deadline = time.monotonic() + 5.0
    while pending:
        directory, depth = pending.pop()
        if depth > 16 or time.monotonic() > deadline:
            raise RExecutionFailed("R workspace measurement exceeded its bound")
        try:
            children = os.scandir(directory)
        except OSError as error:
            raise RExecutionFailed("R workspace could not be measured") from error
        with children:
            for child in children:
                if time.monotonic() > deadline:
                    raise RExecutionFailed("R workspace measurement exceeded its bound")
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
                    return total
    return total
