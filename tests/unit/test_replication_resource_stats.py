"""Truthful measured, unknown, and OOM resource-evidence regressions."""

from __future__ import annotations

import sys
from datetime import datetime

import pytest

from envresearch.replication import _container_models as models
from envresearch.replication._runtime_subprocess import SubprocessCommandExecutor


def _measurement(memory: int | None, storage: int | None, oom: bool | None):
    value_type = getattr(models, "ResourceMeasurement", None)
    if value_type is None:
        pytest.fail("runtime boundary lacks explicit resource measurement values")
    return value_type(memory_bytes=memory, storage_bytes=storage, oom_killed=oom)


def test_fake_stats_drive_32mib_heartbeats_and_final_peak() -> None:
    samples = iter(
        (
            _measurement(32 * 1024**2, 4, False),
            _measurement(40 * 1024**2, 7, False),
            _measurement(36 * 1024**2, 9, False),
        )
    )
    last = _measurement(36 * 1024**2, 9, False)
    observed: list[tuple[datetime, int, int]] = []
    executor = SubprocessCommandExecutor(progress_interval_seconds=0.03)

    result = executor.execute(
        (sys.executable, "-c", "import time; time.sleep(0.11)"),
        inactivity_seconds=2,
        on_progress=lambda *values: observed.append(values),
        resource_sampler=lambda: next(samples, last),  # type: ignore[call-arg]
    )

    assert max(memory for _, memory, _ in observed) >= 40 * 1024**2
    assert result.peak_memory_bytes == 40 * 1024**2
    assert result.storage_bytes == 9


def test_unknown_resource_sample_is_not_converted_to_zero() -> None:
    executor = SubprocessCommandExecutor()

    result = executor.execute(
        (sys.executable, "-c", "pass"),
        inactivity_seconds=2,
        resource_sampler=lambda: _measurement(None, None, None),  # type: ignore[call-arg]
    )

    assert result.peak_memory_bytes is None
    assert result.storage_bytes is None
    assert result.resource_status == "unknown"  # type: ignore[attr-defined]


def test_oom_sample_is_preserved_as_typed_resource_evidence() -> None:
    executor = SubprocessCommandExecutor()

    result = executor.execute(
        (sys.executable, "-c", "pass"),
        inactivity_seconds=2,
        resource_sampler=lambda: _measurement(1, 1, True),  # type: ignore[call-arg]
    )

    assert result.oom_killed is True  # type: ignore[attr-defined]
