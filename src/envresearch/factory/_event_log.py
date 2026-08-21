"""Crash-recoverable publication for the shared factory event history."""

from __future__ import annotations

from pathlib import Path

from envresearch.econometrics._store_files import StoreFiles
from envresearch.kernel.events import EventLog, EventRecord


def append_event_atomically(events: EventLog, event: EventRecord) -> None:
    """Append once through atomic whole-file replacement under the factory lock."""
    existing = events.read_all()
    for item in existing:
        if item.event_id == event.event_id:
            if item != event:
                raise RuntimeError("event identity collision")
            return
    current = events.path.read_bytes() if events.path.exists() else b""
    line = (event.model_dump_json() + "\n").encode("utf-8")
    StoreFiles(events.path.parent).write(Path(events.path.name), current + line, 0o600)


__all__ = ["append_event_atomically"]
