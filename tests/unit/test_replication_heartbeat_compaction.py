"""Bounded heartbeat-ledger growth regression."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from replication_ledger_fixtures import started_run, store

from envresearch.replication.ledger import ReplicationLedger, ResourceObservation


def test_long_runtime_keeps_each_heartbeat_generation_bounded(tmp_path: Path) -> None:
    ledger = ReplicationLedger(store(tmp_path))
    current = started_run(tmp_path)
    started = datetime(2026, 8, 10, tzinfo=UTC)
    sizes: list[int] = []

    for offset in range(64):
        current = ledger.observe_progress(
            current,
            ResourceObservation(
                elapsed_seconds=offset,
                storage_bytes=offset,
                memory_bytes=offset,
                heartbeat_at=started + timedelta(seconds=offset),
            ),
        )
        path = tmp_path / (
            "artifacts/replication/.versions/replication-ledger/"
            f"{current.artifact_version:04d}.yaml"
        )
        sizes.append(path.stat().st_size)

    _, run = ledger.read_current(current)
    assert len(run.observations) <= 8
    assert run.observation_count == 64
    assert run.observation_chain_sha256 != "0" * 64
    assert sizes[-1] <= sizes[8] * 2
