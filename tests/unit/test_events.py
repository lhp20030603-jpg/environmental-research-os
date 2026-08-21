"""Tests for the append-only workflow event log."""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from envresearch.kernel.events import EventLog, EventLogCorruptionError, EventRecord
from envresearch.models.enums import WorkflowStatus


def event(event_id: str, target: WorkflowStatus) -> EventRecord:
    """Build one deterministic event for log serialization tests."""
    return EventRecord(
        event_id=event_id,
        run_id="run-001",
        event_type="status_changed",
        actor="workflow-engine",
        timestamp=datetime(2026, 8, 4, 9, 0, tzinfo=UTC),
        from_status=WorkflowStatus.PENDING,
        to_status=target,
        payload={"attempt": 1},
    )


def test_append_preserves_event_order_and_writes_one_json_object_per_line(
    tmp_path: Path,
) -> None:
    """Appended events must remain replayable in their durable order."""
    log = EventLog(tmp_path / "events.jsonl")
    first = event("event-001", WorkflowStatus.RUNNING)
    second = event("event-002", WorkflowStatus.REVIEW_REQUIRED)

    log.append(first)
    log.append(second)

    assert log.read_all() == [first, second]
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_id"] for line in lines] == [
        "event-001",
        "event-002",
    ]


def test_read_all_rejects_truncated_final_jsonl_line_with_its_line_number(
    tmp_path: Path,
) -> None:
    """A partial write must never be silently treated as a valid event history."""
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.append(event("event-001", WorkflowStatus.RUNNING))
    with path.open("ab") as file:
        file.write(b'{"event_id":"event-002"')

    with pytest.raises(EventLogCorruptionError, match="line 2"):
        log.read_all()


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2026, 8, 4, 9, 0),  # noqa: DTZ001
        datetime(2026, 8, 4, 9, 0, tzinfo=timezone(timedelta(hours=8))),
        datetime(2026, 8, 4, 9, 0, tzinfo=timezone(timedelta(0), "non-utc")),
    ],
)
def test_event_record_rejects_non_utc_timestamps(timestamp: datetime) -> None:
    """Events need UTC timestamps for a stable cross-system chronology."""
    with pytest.raises(ValidationError, match="UTC"):
        EventRecord(
            event_id="event-001",
            run_id="run-001",
            event_type="status_changed",
            actor="workflow-engine",
            timestamp=timestamp,
            from_status=WorkflowStatus.PENDING,
            to_status=WorkflowStatus.RUNNING,
        )


@pytest.mark.parametrize("timestamp", ["2026-08-04T09:00:00Z", "2026-08-04T09:00:00+00:00"])
def test_event_record_round_trips_canonical_utc_json_timestamps(
    timestamp: str,
) -> None:
    """Canonical UTC JSON encodings remain valid persisted event timestamps."""
    restored = EventRecord.model_validate_json(
        json.dumps(
            {
                "event_id": "event-001",
                "run_id": "run-001",
                "event_type": "status_changed",
                "actor": "workflow-engine",
                "timestamp": timestamp,
                "from_status": "pending",
                "to_status": "running",
                "payload": {},
            }
        )
    )

    assert restored.timestamp.utcoffset() == timedelta(0)
    assert EventRecord.model_validate_json(restored.model_dump_json()) == restored
