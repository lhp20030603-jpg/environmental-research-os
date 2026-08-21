"""Tests for restricted, non-shell benchmark command execution."""

from __future__ import annotations

import os
import signal
import sys
import time
from datetime import UTC
from pathlib import Path

import pytest

from envresearch.models.benchmark import CommandSpec
from envresearch.runner.command import CommandRunner


def test_runner_rejects_unapproved_executable(tmp_path: Path) -> None:
    """An unapproved program must never be passed to the operating system."""
    runner = CommandRunner(allowed_executables={"python"})
    spec = CommandSpec(argv=["bash", "-c", "echo unsafe"])

    with pytest.raises(PermissionError, match="executable 'bash' is not allowed"):
        runner.run(spec, tmp_path)


@pytest.mark.parametrize("executable", ["./python", "/usr/bin/python", "python3"])
def test_runner_rejects_executable_allowlist_bypasses(
    tmp_path: Path, executable: str
) -> None:
    """Only the exact approved executable token can select a program."""
    runner = CommandRunner(allowed_executables={"python"})
    spec = CommandSpec(argv=[executable, "-c", "print('unsafe')"])

    with pytest.raises(PermissionError, match="is not allowed"):
        runner.run(spec, tmp_path)


def test_runner_pins_python_token_without_ambient_path_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PATH shadow cannot replace the interpreter bound during construction."""
    shadow_directory = tmp_path / "shadow"
    shadow_directory.mkdir()
    shadow_python = shadow_directory / "python"
    shadow_python.write_text("#!/bin/sh\necho shadowed\n", encoding="utf-8")
    shadow_python.chmod(0o755)
    monkeypatch.setenv("PATH", str(shadow_directory))
    runner = CommandRunner(allowed_executables={"python"})
    spec = CommandSpec(argv=["python", "-c", "print('pinned')"])

    result = runner.run(spec, tmp_path)

    assert result.return_code == 0
    assert result.stdout == "pinned\n"


def test_runner_supports_explicit_trusted_executable_mapping(tmp_path: Path) -> None:
    """Non-Python runtimes can be pinned with an absolute trusted path."""
    runner = CommandRunner({"trusted-python": Path(sys.executable)})
    spec = CommandSpec(argv=["trusted-python", "-c", "print('mapped')"])

    result = runner.run(spec, tmp_path)

    assert result.return_code == 0
    assert result.stdout == "mapped\n"


def test_runner_accepts_a_generator_of_python_tokens(tmp_path: Path) -> None:
    """The documented iterable input remains valid when it is single-pass."""
    runner = CommandRunner(token for token in ("python",))
    spec = CommandSpec(argv=["python", "-c", "print('generated')"])

    result = runner.run(spec, tmp_path)

    assert result.return_code == 0
    assert result.stdout == "generated\n"


def test_runner_rejects_relative_trusted_executable_mapping() -> None:
    """Configured executable paths must be trusted absolute paths."""
    with pytest.raises(ValueError, match="must be an absolute path"):
        CommandRunner({"trusted-python": Path("python")})


def test_runner_executes_arguments_without_a_shell(tmp_path: Path) -> None:
    """Shell metacharacters remain literal command arguments."""
    runner = CommandRunner(allowed_executables={"python"})
    spec = CommandSpec(
        argv=[
            "python",
            "-c",
            "import sys; print(sys.argv[1])",
            "$(echo should-not-run); literal",
        ]
    )

    result = runner.run(spec, tmp_path)

    assert result.return_code == 0
    assert result.stdout == "$(echo should-not-run); literal\n"
    assert result.timed_out is False


def test_runner_returns_canonical_utc_result_for_success(tmp_path: Path) -> None:
    """Successful commands retain their output and UTC execution metadata."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner = CommandRunner(allowed_executables={"python"})
    spec = CommandSpec(
        argv=["python", "-c", "import os; print(os.getcwd())"], cwd=Path(".")
    )

    result = runner.run(spec, workspace)

    assert result.argv == spec.argv
    assert result.cwd == workspace.resolve()
    assert result.return_code == 0
    assert result.stdout == f"{workspace.resolve()}\n"
    assert result.stderr == ""
    assert result.started_at.tzinfo is UTC
    assert result.finished_at.tzinfo is UTC
    assert result.finished_at >= result.started_at
    assert result.timed_out is False


def test_runner_returns_nonzero_exit_as_a_structured_result(tmp_path: Path) -> None:
    """A failing benchmark command is data, rather than a raised subprocess error."""
    runner = CommandRunner(allowed_executables={"python"})
    spec = CommandSpec(
        argv=[
            "python",
            "-c",
            "import sys; print('failure', file=sys.stderr); sys.exit(7)",
        ]
    )

    result = runner.run(spec, tmp_path)

    assert result.return_code == 7
    assert result.stdout == ""
    assert result.stderr == "failure\n"
    assert result.timed_out is False


def test_runner_reports_output_truncation_without_unbounded_memory_capture(
    tmp_path: Path,
) -> None:
    """Oversized output is capped and exposes deterministic truncation metadata."""
    runner = CommandRunner(allowed_executables={"python"}, max_output_bytes=8)
    spec = CommandSpec(
        argv=[
            "python",
            "-c",
            "import sys; print('x' * 12, end=''); print('y' * 12, end='', file=sys.stderr)",
        ]
    )

    result = runner.run(spec, tmp_path)

    assert result.return_code == 0
    assert result.stdout == "x" * 8
    assert result.stderr == "y" * 8
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True


@pytest.mark.skipif(
    os.name != "posix", reason="process-group termination is POSIX-only"
)
def test_runner_returns_timeout_result_and_terminates_process_group(
    tmp_path: Path,
) -> None:
    """Timeouts return data and do not leave a spawned child process running."""
    runner = CommandRunner(allowed_executables={"python"})
    child_pid_file = tmp_path / "child.pid"
    spec = CommandSpec(
        argv=[
            "python",
            "-c",
            (
                "from pathlib import Path; import subprocess, sys, time; "
                "child = subprocess.Popen([sys.executable, '-c', "
                "'import time; time.sleep(60)']); "
                "Path('child.pid').write_text(str(child.pid), encoding='utf-8'); "
                "time.sleep(60)"
            ),
        ],
        timeout_seconds=1,
    )

    result = runner.run(spec, tmp_path)

    assert result.return_code is None
    assert result.timed_out is True
    assert child_pid_file.is_file()
    _assert_process_stops(int(child_pid_file.read_text(encoding="utf-8")))


@pytest.mark.skipif(
    os.name != "posix", reason="process-group termination is POSIX-only"
)
def test_runner_timeout_does_not_wait_for_escaped_descendant_output(
    tmp_path: Path,
) -> None:
    """An escaped descendant retaining stdout cannot make timeout cleanup hang."""
    runner = CommandRunner(allowed_executables={"python"}, timeout_cleanup_seconds=0.2)
    child_pid_file = tmp_path / "escaped-child.pid"
    spec = CommandSpec(
        argv=[
            "python",
            "-c",
            (
                "from pathlib import Path; import subprocess, sys, time; "
                "child = subprocess.Popen([sys.executable, '-c', "
                "'import time; print(\"escaped\", flush=True); time.sleep(60)'], "
                "start_new_session=True); "
                "Path('escaped-child.pid').write_text(str(child.pid), encoding='utf-8'); "
                "time.sleep(60)"
            ),
        ],
        timeout_seconds=1,
    )

    started = time.monotonic()
    result = runner.run(spec, tmp_path)
    elapsed = time.monotonic() - started
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    try:
        assert result.return_code is None
        assert result.timed_out is True
        assert elapsed < 3
    finally:
        _kill_process(child_pid)


def test_runner_rejects_forged_cwd_escape(tmp_path: Path) -> None:
    """Runner-side confinement remains effective if validation is bypassed."""
    runner = CommandRunner(allowed_executables={"python"})
    forged_spec = CommandSpec.model_construct(
        argv=["python", "-c", "print('unsafe')"], cwd=Path("../outside")
    )

    with pytest.raises(ValueError, match="path escapes workspace"):
        runner.run(forged_spec, tmp_path)


def test_runner_rejects_cwd_symlink_escape(tmp_path: Path) -> None:
    """A relative cwd symlink cannot redirect commands outside the workspace."""
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    runner = CommandRunner(allowed_executables={"python"})
    spec = CommandSpec(argv=["python", "-c", "print('unsafe')"], cwd=Path("escape"))

    with pytest.raises(ValueError, match="path escapes workspace"):
        runner.run(spec, workspace)


def test_runner_uses_clean_environment_and_keeps_execution_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Commands receive declared variables and PATH, but no inherited secrets."""
    monkeypatch.setenv("RUNNER_TEST_SECRET", "do-not-inherit")
    runner = CommandRunner(allowed_executables={"python"})
    spec = CommandSpec(
        argv=[
            "python",
            "-c",
            (
                "import os; "
                "print(os.environ.get('RUNNER_TEST_SECRET', '')); "
                "print(os.environ['TZ']); "
                "print(bool(os.environ.get('PATH')))"
            ),
        ],
        env={"TZ": "UTC"},
    )

    result = runner.run(spec, tmp_path)

    assert result.return_code == 0
    assert result.stdout == "\nUTC\nTrue\n"


@pytest.mark.parametrize(
    "name",
    ["API_KEY", "api_key", "TOKEN", "token", "DB_PASSWORD", "db_password"],
)
def test_runner_rejects_secret_environment_names_before_process_execution(
    tmp_path: Path, name: str
) -> None:
    """Secret-like declarations cannot reach a trusted child process or its output."""
    secret = "not-for-output"
    runner = CommandRunner(allowed_executables={"python"})
    forged_spec = CommandSpec.model_construct(
        argv=["python", "-c", "from pathlib import Path; Path('executed').touch()"],
        env={name: secret},
    )

    with pytest.raises(ValueError, match="is not allowed") as raised:
        runner.run(forged_spec, tmp_path)

    assert secret not in str(raised.value)
    assert not (tmp_path / "executed").exists()


@pytest.mark.parametrize(
    "name",
    [
        "PATH",
        "pathext",
        "SYSTEMROOT",
        "COMSPEC",
        "LD_PRELOAD",
        "DYLD_INSERT_LIBRARIES",
        "PYTHONPATH",
        "PYTHONHOME",
        "BASH_ENV",
        "ENV",
        "IFS",
    ],
)
def test_runner_rejects_system_or_loader_sensitive_environment_overrides(
    tmp_path: Path, name: str
) -> None:
    """Declared command variables cannot alter executable or loader behavior."""
    runner = CommandRunner(allowed_executables={"python"})
    spec = CommandSpec.model_construct(
        argv=["python", "-c", "print('unsafe')"], env={name: "value"}
    )

    with pytest.raises(ValueError, match="is not allowed"):
        runner.run(spec, tmp_path)


@pytest.mark.parametrize("name", ["", "9LEADING", "NOT-VALID", "HAS SPACE"])
def test_runner_rejects_malformed_environment_variable_names(
    tmp_path: Path, name: str
) -> None:
    """Environment names must be portable identifiers before process creation."""
    runner = CommandRunner(allowed_executables={"python"})
    spec = CommandSpec.model_construct(
        argv=["python", "-c", "print('unsafe')"], env={name: "value"}
    )

    with pytest.raises(ValueError, match="invalid"):
        runner.run(spec, tmp_path)


def _assert_process_stops(pid: int) -> None:
    """Poll a timeout child briefly, tolerating normal asynchronous reaping."""
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail(f"timed-out process {pid} is still running")


def _kill_process(pid: int) -> None:
    """Clean up a deliberately escaped child used by the timeout regression."""
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    _assert_process_stops(pid)
