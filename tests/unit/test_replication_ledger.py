"""Tests for the immutable, resumable Tier-2 replication ledger."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Thread

import pytest
from pydantic import HttpUrl, TypeAdapter
from replication_ledger_fixtures import (
    _write,
    acquired_ref,
    approved_ref,
    attempt_args,
    completion_evidence,
    derived_ref,
    observation,
    run_inputs,
    runtime_ref,
    started_run,
    store,
)

from envresearch.models.artifact import ArtifactRef, ResearchArtifact
from envresearch.replication.ledger import (
    OutputResult,
    ReplicationLedger,
    ResourceObservation,
)

URL = TypeAdapter(HttpUrl).validate_python
SHA256 = "a" * 64


def test_resume_rejects_changed_acquired_archive(tmp_path: Path) -> None:
    ledger = ReplicationLedger(store(tmp_path))
    approved = approved_ref(tmp_path)
    acquired = acquired_ref(tmp_path, approved, "a" * 64)
    started = ledger.start(
        approved,
        acquired,
        runtime_ref(tmp_path, approved, acquired),
        *attempt_args(tmp_path, approved),
    )
    paused = ledger.pause(started, reason="inactivity")

    with pytest.raises(ValueError, match="acquired inventory"):
        ledger.resume(
            paused,
            acquired_ref(tmp_path, approved, "b" * 64),
            runtime_ref(tmp_path, approved, acquired),
        )


def test_completion_requires_every_declared_output_hash(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="declared output"):
        ReplicationLedger(store(tmp_path)).complete(
            started_run(tmp_path),
            author_outputs=(),
            derived_ref=derived_ref(),
            derived_log_ref=derived_ref(),
        )


def test_heartbeats_preserve_history_and_elapsed_time_does_not_fail(
    tmp_path: Path,
) -> None:
    ledger = ReplicationLedger(store(tmp_path))
    started = started_run(tmp_path)
    current = ledger.heartbeat(
        started,
        ResourceObservation(
            elapsed_seconds=999_999,
            storage_bytes=1,
            memory_bytes=1,
            heartbeat_at=datetime.now(UTC),
        ),
    )

    assert current != started
    assert ledger.load_current(current) == current
    assert current.artifact_version > started.artifact_version


def test_stale_heartbeat_is_rejected_after_another_transition(tmp_path: Path) -> None:
    ledger = ReplicationLedger(store(tmp_path))
    started = started_run(tmp_path)
    current = ledger.heartbeat(started, observation())

    with pytest.raises(ValueError, match="current"):
        ledger.heartbeat(started, observation())

    assert ledger.load_current(current) == current


def test_stale_complete_is_rejected_after_pause(tmp_path: Path) -> None:
    ledger = ReplicationLedger(store(tmp_path))
    started = started_run(tmp_path)
    ledger.pause(started, reason="inactivity")

    with pytest.raises(ValueError, match="current"):
        ledger.complete(
            started,
            author_outputs=(),
            derived_ref=derived_ref(),
            derived_log_ref=derived_ref(),
        )


def test_emergency_stop_is_the_only_manual_emergency_reason(tmp_path: Path) -> None:
    ledger = ReplicationLedger(store(tmp_path))
    paused = ledger.pause(started_run(tmp_path), reason="emergency-stop")

    assert paused.artifact_version > 1
    with pytest.raises(ValueError, match="allowed typed"):
        ReplicationLedger(store(tmp_path)).pause(paused, reason="operator whim")


def test_public_read_rejects_paused_generation_without_checkpoint(
    tmp_path: Path,
) -> None:
    ledger = ReplicationLedger(store(tmp_path))
    paused = ledger.pause(started_run(tmp_path), reason="inactivity")

    with pytest.raises(ValueError, match="checkpoint"):
        ledger.read_current(paused)


@pytest.mark.parametrize("boundary", ["history", "current", "report"])
def test_interrupted_publication_recovers_the_complete_artifact_triple(
    tmp_path: Path, boundary: str
) -> None:
    started = started_run(tmp_path)

    def fail_at(candidate: str) -> None:
        if candidate == boundary:
            raise OSError(f"injected {boundary} failure")

    with pytest.raises(OSError, match=boundary):
        ReplicationLedger(store(tmp_path), failure_injector=fail_at).heartbeat(
            started, observation()
        )

    recovered = ReplicationLedger(store(tmp_path)).start(*run_inputs(tmp_path))
    artifacts = tuple(
        store(tmp_path).read_structured(path, TypeAdapter(ResearchArtifact[object]))
        for path in (
            Path("artifacts/replication/replication-ledger.yaml"),
            Path("artifacts/replication/replication-report.json"),
        )
    )
    assert artifacts[0] == artifacts[1]
    assert recovered.artifact_version == 3


@pytest.mark.parametrize("boundary", ["history", "current", "report"])
def test_initial_start_recovery_promotes_pending_before_heartbeat(
    tmp_path: Path, boundary: str
) -> None:
    approved = approved_ref(tmp_path)
    acquired = acquired_ref(tmp_path, approved, "a" * 64)
    runtime = runtime_ref(tmp_path, approved, acquired)
    attempt = attempt_args(tmp_path, approved)

    with pytest.raises(OSError, match=boundary):
        ReplicationLedger(
            store(tmp_path),
            failure_injector=lambda stage: (
                (_ for _ in ()).throw(OSError(stage)) if stage == boundary else None
            ),
        ).start(approved, acquired, runtime, *attempt)

    recovered = ReplicationLedger(store(tmp_path)).start(
        approved, acquired, runtime, *attempt
    )
    assert (
        ReplicationLedger(store(tmp_path))
        .heartbeat(recovered, observation())
        .artifact_version
        > recovered.artifact_version
    )


def test_start_rejects_approved_payload_with_an_unbound_envelope(
    tmp_path: Path,
) -> None:
    valid = approved_ref(tmp_path)
    artifact = store(tmp_path).read_structured(
        Path(f"artifacts/replication/approved/{valid.content_hash}.json"),
        TypeAdapter(ResearchArtifact[object]),
    )
    bad = _write(tmp_path, "approved-tier2-intake", artifact.payload)
    acquired = acquired_ref(tmp_path, bad, "a" * 64)
    attempt = attempt_args(tmp_path, bad)

    with pytest.raises(ValueError, match="bind proposal"):
        ReplicationLedger(store(tmp_path)).start(
            bad, acquired, runtime_ref(tmp_path, bad, acquired), *attempt
        )


def test_restart_cannot_disable_the_persisted_growth_limit(tmp_path: Path) -> None:
    approved = approved_ref(tmp_path)
    acquired = acquired_ref(tmp_path, approved, "a" * 64)
    runtime = runtime_ref(tmp_path, approved, acquired)
    attempt = attempt_args(tmp_path, approved)
    started = ReplicationLedger(store(tmp_path), max_growth_bytes=1).start(
        approved, acquired, runtime, *attempt
    )
    current = ReplicationLedger(store(tmp_path)).heartbeat(started, observation())
    paused = ReplicationLedger(store(tmp_path)).heartbeat(
        current,
        ResourceObservation(
            elapsed_seconds=2,
            storage_bytes=3,
            memory_bytes=1,
            heartbeat_at=datetime.now(UTC),
        ),
    )
    assert paused.artifact_version > current.artifact_version


def test_concurrent_exact_current_transitions_allow_only_one_winner(
    tmp_path: Path,
) -> None:
    started = started_run(tmp_path)
    gate = Barrier(2)
    outcomes: list[ArtifactRef | Exception] = []

    def transition() -> None:
        gate.wait()
        try:
            outcomes.append(
                ReplicationLedger(store(tmp_path)).heartbeat(started, observation())
            )
        except ValueError as error:
            outcomes.append(error)

    threads = [Thread(target=transition), Thread(target=transition)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(isinstance(item, ArtifactRef) for item in outcomes) == 1
    assert sum(isinstance(item, ValueError) for item in outcomes) == 1


def test_completion_seals_ledger_and_report_with_declared_hashes(
    tmp_path: Path,
) -> None:
    ledger = ReplicationLedger(store(tmp_path))
    started = started_run(tmp_path)
    outputs, derived, derived_log = completion_evidence(tmp_path)
    completed = ledger.complete(
        started,
        author_outputs=outputs,
        derived_ref=derived,
        derived_log_ref=derived_log,
    )

    assert ledger.load_current(completed) == completed
    assert (tmp_path / "artifacts/replication/replication-ledger.yaml").exists()
    assert (tmp_path / "artifacts/replication/replication-report.json").exists()


def test_completion_rejects_a_declared_output_with_the_wrong_comparator(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="comparator"):
        ReplicationLedger(store(tmp_path)).complete(
            started_run(tmp_path),
            author_outputs=(
                OutputResult(
                    path="output/results.csv",
                    sha256="c" * 64,
                    comparator="exact",
                    comparison_passed=True,
                    raw_ref=derived_ref(),
                    artifact_ref=derived_ref(),
                    log_ref=derived_ref(),
                ),
            ),
            derived_ref=derived_ref(),
            derived_log_ref=derived_ref(),
        )
