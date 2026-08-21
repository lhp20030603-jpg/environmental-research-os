"""Concurrent bounded capture for child-process byte streams."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import BinaryIO, cast

_READ_SIZE = 64 * 1024


class BoundedPipeCapture:
    """Drain one pipe while retaining only a fixed-size byte prefix."""

    def __init__(self, stream: BinaryIO, max_bytes: int) -> None:
        self._stream = stream
        self._max_bytes = max_bytes
        self._captured = bytearray()
        self._truncated = False
        self._read_failed = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._drain, daemon=True)

    def start(self) -> None:
        """Start draining without blocking command execution."""
        self._thread.start()

    def finish(self, timeout: float) -> tuple[str, bool]:
        """Return a stable prefix without waiting past *timeout* for pipe EOF."""
        self._thread.join(max(timeout, 0.0))
        incomplete = self._thread.is_alive()
        if not incomplete:
            self._stream.close()
        with self._lock:
            captured = bytes(self._captured)
            truncated = self._truncated or self._read_failed or incomplete
        return captured.decode("utf-8", errors="replace"), truncated

    def _drain(self) -> None:
        try:
            read_chunk = cast(
                Callable[[int], bytes],
                getattr(self._stream, "read1", self._stream.read),
            )
            while chunk := read_chunk(_READ_SIZE):
                with self._lock:
                    remaining = self._max_bytes - len(self._captured)
                    if remaining > 0:
                        self._captured.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        self._truncated = True
        except (OSError, ValueError):
            with self._lock:
                self._read_failed = True
        finally:
            self._stream.close()
