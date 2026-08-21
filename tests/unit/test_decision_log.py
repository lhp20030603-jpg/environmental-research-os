"""Tests for the durable generic research decision audit log."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from envresearch.kernel.decision_log import (
    DecisionLog,
    DecisionLogCorruptionError,
    DecisionLogEntry,
)


def decision_entry(
    decision_id: str, *, reason: str = "best feasible contribution"
) -> DecisionLogEntry:
    """Build a fixed generic gate-decision record for persistence tests."""
    return DecisionLogEntry(
        decision_id=decision_id,
        timestamp=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
        actor="human-owner",
        decision_kind="gate",
        status="approved",
        subject="gate-1",
        reason=reason,
        metadata={"selected_candidate_id": "charter-air"},
    )


def test_decision_log_appends_canonical_record_and_is_idempotent(
    tmp_path: Path,
) -> None:
    """Equivalent retries leave exactly one durable, readable decision record."""
    log = DecisionLog(tmp_path / "decision-log.jsonl")
    entry = decision_entry("decision-1")

    log.append(entry)
    log.append(entry)

    assert log.read_all() == [entry]
    physical = json.loads((tmp_path / "decision-log.jsonl").read_bytes())
    chain = physical.pop("_journal")
    assert physical == entry.model_dump(mode="json")
    assert chain["sequence"] == 1
    assert len(chain["record_sha256"]) == 64


def test_decision_log_rejects_event_id_reuse_with_changed_content(
    tmp_path: Path,
) -> None:
    """A reused durable identity cannot silently overwrite its audit meaning."""
    log = DecisionLog(tmp_path / "decision-log.jsonl")
    log.append(decision_entry("decision-1", reason="first"))

    with pytest.raises(RuntimeError, match="decision identity collision"):
        log.append(decision_entry("decision-1", reason="changed"))


def test_decision_log_rejects_noncanonical_or_tampered_records(tmp_path: Path) -> None:
    """Replay rejects legacy JSON and edits to an authenticated physical record."""
    path = tmp_path / "decision-log.jsonl"
    entry = decision_entry("decision-1")
    path.write_text(entry.model_dump_json() + "\n", encoding="utf-8")

    with pytest.raises(DecisionLogCorruptionError, match="journal|authentication"):
        DecisionLog(path).read_all()

    protected = tmp_path / "protected" / "decision-log.jsonl"
    log = DecisionLog(protected)
    log.append(entry)
    value = json.loads(protected.read_bytes())
    value["reason"] = "changed"
    protected.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    )

    with pytest.raises(DecisionLogCorruptionError, match="authentication") as raised:
        log.read_all()
    assert str(protected) in str(raised.value)
    assert "line 1" in str(raised.value)


def test_decision_log_rejects_identical_duplicate_identity(tmp_path: Path) -> None:
    """A second physical occurrence of an identity corrupts strict replay."""
    path = tmp_path / "decision-log.jsonl"
    log = DecisionLog(path)
    log.append(decision_entry("decision-1"))
    physical = path.read_bytes()
    with path.open("ab") as file:
        file.write(physical)

    with pytest.raises(DecisionLogCorruptionError, match="chain|sequence") as raised:
        log.read_all()
    assert str(path) in str(raised.value)
    assert "line 2" in str(raised.value)


@pytest.mark.parametrize("payload", [b"\n", b"{bad}\n", b'{"decision_id":"one"}\n'])
def test_decision_log_reports_corrupt_lines_with_location(
    tmp_path: Path, payload: bytes
) -> None:
    """Blank, malformed, and invalid records are never ignored during replay."""
    path = tmp_path / "decision-log.jsonl"
    path.write_bytes(payload)
    path.chmod(0o600)

    with pytest.raises(DecisionLogCorruptionError, match="line 1") as raised:
        DecisionLog(path).read_all()
    assert str(path) in str(raised.value)


def test_decision_log_rejects_non_json_metadata() -> None:
    """Decision metadata is restricted to finite JSON-compatible values."""
    with pytest.raises(ValidationError):
        DecisionLogEntry(
            decision_id="decision-1",
            timestamp=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
            actor="human-owner",
            decision_kind="gate",
            status="approved",
            subject="gate-1",
            reason="best feasible contribution",
            metadata={"score": float("nan")},
        )


def test_decision_log_entry_isolated_and_revalidated_at_append(tmp_path: Path) -> None:
    """Caller mutation and forged models cannot alter or bypass audit metadata."""
    metadata = {"conditions": ["IRB approval"]}
    payload = decision_entry("decision-1").model_dump(mode="python")
    payload["metadata"] = metadata
    entry = DecisionLogEntry.model_validate(payload)
    metadata["conditions"].append("data agreement")
    log = DecisionLog(tmp_path / "decision-log.jsonl")
    log.append(entry)
    assert log.read_all()[0].metadata == {"conditions": ["IRB approval"]}

    forged = decision_entry("decision-2")
    object.__setattr__(forged, "metadata", {"score": float("nan")})
    with pytest.raises(ValidationError):
        log.append(forged)


@pytest.mark.parametrize(
    "update",
    [
        {"actor": "  "},
        {"timestamp": datetime(2026, 8, 5, 9, 0)},  # noqa: DTZ001
        {"unexpected": "field"},
    ],
)
def test_decision_log_entry_strictly_rejects_blank_utc_and_extra_fields(
    update: dict[str, object],
) -> None:
    """Identity-bearing records require valid principals, UTC time, and schema."""
    payload = decision_entry("decision-1").model_dump(mode="python")
    payload.update(update)

    with pytest.raises(ValidationError):
        DecisionLogEntry.model_validate(payload)
