"""Private EOF-safe bounded subprocess capture with truthful samples."""

from __future__ import annotations

import os
import selectors
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import IO, Protocol

from envresearch.replication._container_models import ResourceMeasurement
from envresearch.replication.container import ContainerInactivityError, ProgressCallback


class PollingProcess(Protocol):
    def poll(self) -> int | None: ...


@dataclass(frozen=True, slots=True)
class CapturedProcess:
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool
    return_code: int
    peak_memory_bytes: int | None
    storage_bytes: int | None
    resource_status: str
    oom_killed: bool | None


def drain_process(
    process: PollingProcess,
    stdout: IO[bytes],
    stderr: IO[bytes],
    *,
    inactivity_seconds: int,
    progress_interval_seconds: float,
    max_capture_bytes: int,
    on_progress: ProgressCallback | None,
    resource_sampler: Callable[[], ResourceMeasurement] | None,
    monotonic: Callable[[], float],
) -> CapturedProcess:
    """Poll until both process completion and both stream EOFs are observed."""
    selector = selectors.DefaultSelector()
    selector.register(stdout, selectors.EVENT_READ, "stdout")
    selector.register(stderr, selectors.EVENT_READ, "stderr")
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    deadline = monotonic() + inactivity_seconds
    next_progress = monotonic() + progress_interval_seconds
    measurements: list[ResourceMeasurement] = []
    try:
        return_code = process.poll()
        while selector.get_map() or return_code is None:
            now = monotonic()
            wait = max(0.0, min(deadline - now, next_progress - now))
            events = selector.select(wait)
            now = monotonic()
            if now >= next_progress:
                measurement = _sample(resource_sampler)
                measurements.append(measurement)
                if on_progress is not None and (
                    measurement.status == "measured" or resource_sampler is None
                ):
                    on_progress(
                        datetime.now(UTC),
                        _measured(measurement.memory_bytes)
                        if measurement.status == "measured"
                        else 0,
                        _measured(measurement.storage_bytes)
                        if measurement.status == "measured"
                        else 0,
                    )
                next_progress = now + progress_interval_seconds
            return_code = process.poll()
            if not events:
                if return_code is None:
                    if now >= deadline:
                        raise ContainerInactivityError(
                            "container produced no progress within approved inactivity"
                        )
                    continue
                events = selector.select(0)
                if not events:
                    break
            for key, _ in events:
                stream = str(key.data)
                chunk = os.read(key.fd, 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                deadline = monotonic() + inactivity_seconds
                target = captured[stream]
                available = max_capture_bytes - len(target)
                target.extend(chunk[: max(0, available)])
                truncated[stream] |= len(chunk) > max(0, available)
            return_code = process.poll()
    finally:
        selector.close()
    if return_code is None:
        raise RuntimeError("container subprocess completion was not observed")
    measurements.append(_sample(resource_sampler))
    peak, storage, status, oom = _summarize(measurements)
    return CapturedProcess(
        bytes(captured["stdout"]),
        bytes(captured["stderr"]),
        truncated["stdout"],
        truncated["stderr"],
        return_code,
        peak,
        storage,
        status,
        oom,
    )


def _sample(
    sampler: Callable[[], ResourceMeasurement] | None,
) -> ResourceMeasurement:
    if sampler is None:
        return ResourceMeasurement(None, None, None)
    value = sampler()
    if type(value) is not ResourceMeasurement:
        raise TypeError("resource sampler returned an invalid measurement")
    return value


def _summarize(
    values: list[ResourceMeasurement],
) -> tuple[int | None, int | None, str, bool | None]:
    if not values or any(value.status == "unknown" for value in values):
        return (
            None,
            None,
            "unknown",
            (True if any(value.oom_killed is True for value in values) else None),
        )
    memory = max(_measured(value.memory_bytes) for value in values)
    storage = max(_measured(value.storage_bytes) for value in values)
    return (
        memory,
        storage,
        "measured",
        any(value.oom_killed is True for value in values),
    )


def _measured(value: int | None) -> int:
    if value is None:
        raise TypeError("resource measurement unexpectedly became unknown")
    return value
