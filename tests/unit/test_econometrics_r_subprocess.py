"""Production no-shell bounded subprocess executor tests."""

import os
import sys
import time
from pathlib import Path

import pytest

from envresearch.econometrics import r_subprocess
from envresearch.econometrics.r_runtime import RExecutionFailed
from envresearch.econometrics.r_subprocess import BoundedRSubprocessExecutor


def _execute(
    executor: BoundedRSubprocessExecutor,
    argv: tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
    max_output_bytes: int,
    max_workspace_bytes: int,
):
    """Run a test command with its executable identity held open."""
    argv = (str(Path(argv[0]).resolve(strict=True)), *argv[1:])
    descriptor = os.open(argv[0], os.O_RDONLY)
    try:
        return executor.execute(
            argv,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            max_workspace_bytes=max_workspace_bytes,
            executable_fd=descriptor,
            pass_fds=(descriptor,),
        )
    finally:
        os.close(descriptor)


def test_executor_runs_exact_argv_with_explicit_environment(tmp_path: Path) -> None:
    """No shell or inherited environment participates in execution."""
    os.environ["ENVRESEARCH_FORBIDDEN_PARENT_VALUE"] = "secret"
    executor = BoundedRSubprocessExecutor(poll_seconds=0.01)

    result = _execute(
        executor,
        (
            sys.executable,
            "-c",
            (
                "import os; print(os.getenv('SAFE')); "
                "print(os.getenv('ENVRESEARCH_FORBIDDEN_PARENT_VALUE'))"
            ),
        ),
        cwd=tmp_path,
        env={"SAFE": "yes"},
        timeout_seconds=2,
        max_output_bytes=1024,
        max_workspace_bytes=4096,
    )

    assert result.return_code == 0
    assert result.stdout == b"yes\nNone\n"
    assert result.stderr == b""
    assert not tuple(tmp_path.glob(".r-command-*"))


def test_executor_stops_output_and_workspace_growth(tmp_path: Path) -> None:
    """Live command evidence cannot exceed either declared byte ceiling."""
    executor = BoundedRSubprocessExecutor(poll_seconds=0.01)
    with pytest.raises(RExecutionFailed, match="output budget"):
        _execute(
            executor,
            (sys.executable, "-c", "print('x' * 4096)"),
            cwd=tmp_path,
            env={},
            timeout_seconds=2,
            max_output_bytes=128,
            max_workspace_bytes=8192,
        )

    with pytest.raises(RExecutionFailed, match="workspace budget"):
        _execute(
            executor,
            (
                sys.executable,
                "-c",
                "from pathlib import Path; Path('growth.bin').write_bytes(b'x'*8192)",
            ),
            cwd=tmp_path,
            env={},
            timeout_seconds=2,
            max_output_bytes=1024,
            max_workspace_bytes=4096,
        )


def test_executor_enforces_inactivity_without_unbounded_wait(tmp_path: Path) -> None:
    """A silent trusted process is terminated at the declared inactivity bound."""
    executor = BoundedRSubprocessExecutor(poll_seconds=0.01)

    with pytest.raises(RExecutionFailed, match="inactivity limit"):
        _execute(
            executor,
            (sys.executable, "-c", "import time; time.sleep(2)"),
            cwd=tmp_path,
            env={},
            timeout_seconds=1,
            max_output_bytes=1024,
            max_workspace_bytes=4096,
        )


def test_executor_stops_descendants_on_inactivity(tmp_path: Path) -> None:
    """The timeout boundary contains descendants, not only the R parent."""
    executor = BoundedRSubprocessExecutor(poll_seconds=0.01)
    pid_path = tmp_path / "child.pid"
    program = (
        "import subprocess,sys,time,pathlib; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "pathlib.Path('child.pid').write_text(str(p.pid)); time.sleep(30)"
    )

    with pytest.raises(RExecutionFailed, match="inactivity limit"):
        _execute(
            executor,
            (sys.executable, "-c", program),
            cwd=tmp_path,
            env={},
            timeout_seconds=1,
            max_output_bytes=1024,
            max_workspace_bytes=4096,
        )

    child_pid = int(pid_path.read_text())
    for _ in range(50):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("descendant survived the R process-group timeout")


def test_executor_contains_descendant_after_parent_success(tmp_path: Path) -> None:
    """A successful parent cannot leave a background process behind."""
    executor = BoundedRSubprocessExecutor(poll_seconds=0.01)
    program = (
        "import subprocess,sys; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "print(p.pid, flush=True)"
    )

    result = _execute(
        executor,
        (sys.executable, "-c", program),
        cwd=tmp_path,
        env={},
        timeout_seconds=2,
        max_output_bytes=1024,
        max_workspace_bytes=4096,
    )

    child_pid = int(result.stdout.strip())
    for _ in range(50):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("descendant survived successful R parent containment")


def test_containment_reaps_terminated_leader_before_group_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linux zombie leaders are reaped while process-group absence is polled."""
    state = {"reaped": False}
    signals: list[int] = []

    class ZombieLeader:
        pid = 4242

        def wait(self, timeout: int) -> int:
            assert timeout == 0
            state["reaped"] = True
            return -15

    clock = iter((0.0, 3.0, 4.0, 7.0))
    monkeypatch.setattr(
        r_subprocess, "_group_exists", lambda _group_id: not state["reaped"]
    )
    monkeypatch.setattr(
        r_subprocess.os,
        "killpg",
        lambda _group_id, sent_signal: signals.append(sent_signal),
    )
    monkeypatch.setattr(r_subprocess.time, "monotonic", lambda: next(clock))

    r_subprocess._contain_process_group(ZombieLeader())  # type: ignore[arg-type]

    assert state["reaped"] is True
    assert signals == [r_subprocess.signal.SIGTERM]
