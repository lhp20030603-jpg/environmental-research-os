"""Resource-bound regressions for concurrent command output capture."""

import tempfile
import time
from pathlib import Path

import pytest

from envresearch.models.benchmark import CommandSpec
from envresearch.runner.command import CommandRunner


def test_high_volume_capture_does_not_depend_on_temporary_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bounded capture must discard excess bytes instead of growing spool files."""
    def forbid_temporary_file(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unbounded temporary capture is forbidden")

    monkeypatch.setattr(tempfile, "TemporaryFile", forbid_temporary_file)
    runner = CommandRunner({"python"}, max_output_bytes=32)
    spec = CommandSpec(
        argv=[
            "python",
            "-c",
            (
                "import sys; "
                "sys.stdout.write('o' * 2000000); sys.stdout.flush(); "
                "sys.stderr.write('e' * 2000000); sys.stderr.flush()"
            ),
        ],
        timeout_seconds=10,
    )

    result = runner.run(spec, tmp_path)

    assert result.return_code == 0
    assert result.stdout == "o" * 32
    assert result.stderr == "e" * 32
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True


def test_high_volume_timeout_returns_with_bounded_separate_prefixes(
    tmp_path: Path,
) -> None:
    """Drainers must preserve timeout bounds while both streams exceed their caps."""
    runner = CommandRunner(
        {"python"}, max_output_bytes=16, timeout_cleanup_seconds=0.2
    )
    spec = CommandSpec(
        argv=[
            "python",
            "-c",
            (
                "import sys, time; "
                "sys.stdout.write('x' * 1000000); sys.stdout.flush(); "
                "sys.stderr.write('y' * 1000000); sys.stderr.flush(); "
                "time.sleep(60)"
            ),
        ],
        timeout_seconds=1,
    )

    started = time.monotonic()
    result = runner.run(spec, tmp_path)
    elapsed = time.monotonic() - started

    assert result.timed_out is True
    assert result.return_code is None
    assert result.stdout == "x" * 16
    assert result.stderr == "y" * 16
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True
    assert elapsed < 3
