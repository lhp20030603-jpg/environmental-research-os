"""EOF and bounded-wait regressions for the reviewed subprocess monitor."""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime

import pytest

from envresearch.replication._runtime_subprocess import SubprocessCommandExecutor
from envresearch.replication.container import ContainerInactivityError


def test_pipe_eof_does_not_disable_inactivity_monitoring() -> None:
    heartbeats: list[tuple[datetime, int, int]] = []
    executor = SubprocessCommandExecutor(progress_interval_seconds=0.05)
    started = time.monotonic()

    with pytest.raises(ContainerInactivityError, match="no progress"):
        executor.execute(
            (
                sys.executable,
                "-c",
                "import os,time; os.close(1); os.close(2); time.sleep(2)",
            ),
            inactivity_seconds=1,
            on_progress=lambda *values: heartbeats.append(values),
        )

    assert time.monotonic() - started < 1.5
    assert heartbeats


def test_monitor_never_enters_bare_wait_after_both_pipes_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_stdout, write_stdout = os.pipe()
    read_stderr, write_stderr = os.pipe()
    os.close(write_stdout)
    os.close(write_stderr)
    waits: list[float | None] = []

    class StubbornProcess:
        pid = 4242
        stdout = os.fdopen(read_stdout, "rb", buffering=0)
        stderr = os.fdopen(read_stderr, "rb", buffering=0)

        @staticmethod
        def poll() -> None:
            return None

        @staticmethod
        def wait(timeout=None):  # type: ignore[no-untyped-def]
            waits.append(timeout)
            raise AssertionError("monitor entered process.wait")

    monotonic_values = iter((0.0, 0.0, 0.0, 1.1, 1.1, 1.1))
    executor = SubprocessCommandExecutor(
        popen=lambda *args, **kwargs: StubbornProcess(),  # type: ignore[arg-type]
        monotonic=lambda: next(monotonic_values, 1.1),
        birth_probe=lambda pid, pgid: "a" * 64,
        progress_interval_seconds=0.5,
    )
    monkeypatch.setattr(executor, "_terminate", lambda process, identity=None: None)

    with pytest.raises(ContainerInactivityError):
        executor.execute(("/reviewed/docker", "run"), inactivity_seconds=1)

    assert waits == []
