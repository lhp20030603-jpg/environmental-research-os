"""Append-only JSONL event history for workflow replay."""

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator

from envresearch.models.enums import WorkflowStatus


class EventRecord(BaseModel):
    """One immutable transition or audit event in a workflow run."""

    event_id: str
    run_id: str
    event_type: str
    actor: str
    timestamp: datetime
    from_status: WorkflowStatus
    to_status: WorkflowStatus
    payload: dict[str, object] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """Reject timestamps that would make replay ordering ambiguous."""
        if value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
            raise ValueError("timestamps must be UTC-aware")
        return value


class EventLogCorruptionError(ValueError):
    """Raised when a durable event log contains an unreadable record."""


class EventLog:
    """Persist event records as a durable, append-only JSONL file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: EventRecord) -> None:
        """Append one serialized event and force it to durable storage."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = (event.model_dump_json() + "\n").encode("utf-8")
        with self.path.open("ab") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())

    def append_next(
        self,
        *,
        run_id: str,
        event_type: str,
        actor: str,
        from_status: WorkflowStatus,
        to_status: WorkflowStatus,
        payload: Mapping[str, object],
    ) -> None:
        """Build and append the next ordinary run event."""
        sequence = len(self.read_all()) + 1
        self.append(
            EventRecord(
                event_id=f"{run_id}.{sequence:08d}",
                run_id=run_id,
                event_type=event_type,
                actor=actor,
                timestamp=datetime.now(UTC),
                from_status=from_status,
                to_status=to_status,
                payload=dict(payload),
            )
        )

    def read_all(self) -> list[EventRecord]:
        """Return every persisted event, rejecting any corrupt JSONL line."""
        if not self.path.exists():
            return []

        events: list[EventRecord] = []
        with self.path.open("rb") as file:
            for line_number, raw_line in enumerate(file, start=1):
                try:
                    line = raw_line.decode("utf-8")
                    value = json.loads(line)
                    events.append(EventRecord.model_validate(value))
                except (
                    json.JSONDecodeError,
                    UnicodeError,
                    ValidationError,
                    TypeError,
                ) as error:
                    raise EventLogCorruptionError(
                        f"event log corruption at line {line_number}: {error}"
                    ) from error
        return events


def append_event_once(events: EventLog, event: EventRecord) -> None:
    """Append *event* once, rejecting reuse of its identity for other content."""
    for existing in events.read_all():
        if existing.event_id == event.event_id:
            if existing != event:
                raise RuntimeError("event identity collision")
            return
    events.append(event)
