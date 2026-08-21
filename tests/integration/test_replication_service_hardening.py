"""Dynamic regressions for autonomous replication controller boundaries."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock

import pytest
from pydantic import HttpUrl, TypeAdapter
from replication_service_fixtures import (
    FakeEngine,
    FixtureFetcher,
    ServiceCase,
    admission,
    approve,
    observation,
    pause_with_checkpoint,
    read_verification,
    replay_configuration,
)

from envresearch.models.artifact import (
    ArtifactRef,
    ProducerIdentity,
    ResearchArtifact,
    seal_artifact,
)
from envresearch.replication._attempt_support import AttemptCoordinator
from envresearch.replication._service_support import (
    persist_payload,
    read_ledger,
)
from envresearch.replication.container import ContainerResult
from envresearch.replication.contracts import ReplicationRunState
from envresearch.replication.intake import Tier2IntakeService
from envresearch.replication.ledger import ReplicationLedger, ReplicationRun
from envresearch.replication.service import ReplicationService


class CoordinatedFetcher(FixtureFetcher):
    def __init__(self, archive: Path) -> None:
        super().__init__(archive)
        self.started = Event()
        self.release = Event()
        self._counter = Lock()

    def fetch(self, url: HttpUrl, destination: Path, max_bytes: int) -> None:
        with self._counter:
            self.calls += 1
        self.started.set()
        if not self.release.wait(timeout=2):
            raise TimeoutError("test fetch release was not signalled")
        destination.write_bytes(self.archive.read_bytes())


class NoAuthorWriteEngine(FakeEngine):
    def run(  # type: ignore[no-untyped-def]
        self, plan, *, on_progress=None, on_started=None, on_stopped=None
    ):
        if plan.output_namespace != "author-reproduction":
            return super().run(
                plan,
                on_progress=on_progress,
                on_started=on_started,
                on_stopped=on_stopped,
            )
        del on_progress
        self.plans.append(plan)
        now = datetime.now(UTC)
        return ContainerResult(
            engine="fake-container",
            image_digest=plan.image_digest,
            exit_status=0,
            started_at=now,
            finished_at=now,
            stdout_sha256="3" * 64,
            stderr_sha256="4" * 64,
            stdout_truncated=False,
            stderr_truncated=False,
            peak_memory_bytes=1,
            storage_bytes=1,
        )


def test_concurrent_run_claims_once_and_returns_one_terminal_report(
    service_case: ServiceCase,
) -> None:
    approved = approve(service_case)
    fetcher = CoordinatedFetcher(service_case.archive)
    service = ReplicationService(
        service_case.store,
        Tier2IntakeService(service_case.store, fetcher=fetcher),
        FakeEngine(),
        replay_configuration(),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.run, approved)
        assert fetcher.started.wait(timeout=2)
        second = pool.submit(service.run, approved)
        fetcher.release.set()
        reports = (first.result(timeout=5), second.result(timeout=5))

    assert fetcher.calls == 1
    assert reports[0] == reports[1]
    assert reports[0].state is ReplicationRunState.PASSED


def test_attempt_claim_is_immutable_and_pending_publication_recovers(
    service_case: ServiceCase,
) -> None:
    approved = approve(service_case)
    expected = service_case.service.run(approved)
    base = service_case.store.root / "artifacts/replication/attempts/claims"
    current = base / f"current/{approved.content_hash}.json"
    pending = base / f".pending/{approved.content_hash}.json"
    assert current.is_file()
    payload = current.read_bytes()
    artifact = service_case.store.read_structured(
        current.relative_to(service_case.store.root),
        TypeAdapter(ResearchArtifact[object]),
    )
    report = base / f"reports/{artifact.envelope.content_hash}.json"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_bytes(payload)
    current.unlink()
    report.unlink()

    recovered = service_case.service.run(approved)

    assert recovered == expected
    assert current.read_bytes() == payload
    assert report.read_bytes() == payload
    assert not pending.exists()
    assert service_case.fetcher.calls == 1


def test_planted_legacy_output_cannot_satisfy_a_new_attempt(
    service_case: ServiceCase,
) -> None:
    approved = approve(service_case)
    planted = service_case.store.root / (
        f"artifacts/replication/runs/test-{approved.content_hash}/"
        "author-reproduction/output/results.csv"
    )
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text("estimate\n0.1\n", encoding="utf-8")
    service_case.service.engine = NoAuthorWriteEngine()

    report = service_case.service.run(approved)

    assert report.state is ReplicationRunState.EXCEPTION
    assert report.exception is not None
    assert report.exception.code == "OUTPUT_MISMATCH"


def test_ledger_binds_attempt_claim_and_exclusive_output_root(
    service_case: ServiceCase,
) -> None:
    report = service_case.service.run(approve(service_case))
    _, run = read_ledger(service_case.store, report.run_ref)

    assert run.attempt_ref.artifact_id == "tier2-replication-attempt-claim"
    assert run.output_root.startswith("artifacts/replication/runs/")


def test_resume_rejects_stale_files_in_its_bound_output_root(
    service_case: ServiceCase,
) -> None:
    _, acquired, runtime, paused = _paused_run(service_case)
    _, run = service_case.service.ledger.read_current(paused)
    stale = service_case.store.root / run.output_root / "output/results.csv"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("estimate\n0.1\n", encoding="utf-8")
    service_case.service.engine = NoAuthorWriteEngine()

    report = service_case.service.resume(paused)

    assert report.state is ReplicationRunState.EXCEPTION
    assert report.exception is not None
    assert report.exception.code == "RESUME_EVIDENCE_INVALID"
    del acquired, runtime


def test_resume_authenticates_runtime_before_transition(
    service_case: ServiceCase,
) -> None:
    _, _, runtime, paused = _paused_run(service_case)
    path = Path(f"artifacts/replication/runtime/{runtime.content_hash}.json")
    artifact = service_case.store.read_structured(
        path, TypeAdapter(ResearchArtifact[object])
    )
    forged = seal_artifact(
        artifact.model_copy(
            update={
                "envelope": artifact.envelope.model_copy(
                    update={
                        "producer": ProducerIdentity(component="attacker", version="1")
                    }
                )
            }
        )
    )
    service_case.store.write_structured(path, forged)

    report = service_case.service.resume(paused)

    assert report.state is ReplicationRunState.EXCEPTION
    assert report.exception is not None
    assert report.exception.code == "RESUME_EVIDENCE_INVALID"


def test_status_rejects_tampered_passed_verification(service_case: ServiceCase) -> None:
    report = service_case.service.run(approve(service_case))
    assert report.verification_ref is not None
    path = Path(
        f"artifacts/replication/verifications/{report.verification_ref.content_hash}.json"
    )
    artifact = service_case.store.read_structured(
        path, TypeAdapter(ResearchArtifact[object])
    )
    assert isinstance(artifact.payload, dict)
    payload = {**artifact.payload, "findings": [{"code": "FORGED", "message": "bad"}]}
    service_case.store.write_structured(
        path, seal_artifact(artifact.model_copy(update={"payload": payload}))
    )

    with pytest.raises(ValueError, match="verification"):
        service_case.service.status(report.run_ref)


def test_status_authenticates_completed_predecessor(service_case: ServiceCase) -> None:
    report = service_case.service.run(approve(service_case))
    predecessor = service_case.store.root / (
        "artifacts/replication/.versions/replication-ledger/"
        f"{report.run_ref.artifact_version - 1:04d}.yaml"
    )
    predecessor.write_text("invalid: [unterminated\n", encoding="utf-8")

    with pytest.raises(ValueError, match="predecessor"):
        service_case.service.status(report.run_ref)


@pytest.mark.parametrize("failure", [OSError("disk full"), ValueError("bad write")])
def test_known_persistence_failure_becomes_first_durable_exception(
    service_case: ServiceCase,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    approved = approve(service_case)
    original = service_case.store.write_structured
    failed = False

    def fail_once(relative: Path, artifact: ResearchArtifact[object]):
        nonlocal failed
        if not failed and relative.parts[:3] == ("artifacts", "replication", "outputs"):
            failed = True
            raise failure
        return original(relative, artifact)

    monkeypatch.setattr(service_case.store, "write_structured", fail_once)
    first = service_case.service.run(approved)
    repeated = service_case.service.run(approved)

    assert first == repeated
    assert first.state is ReplicationRunState.EXCEPTION
    assert first.exception is not None
    assert first.exception.code == "PERSISTENCE_FAILURE"


def test_execution_logs_are_redacted_bounded_and_verified(
    service_case: ServiceCase,
) -> None:
    report = service_case.service.run(approve(service_case))
    _, run = read_ledger(service_case.store, report.run_ref)
    assert run.derived_ref is not None
    refs = tuple(item.log_ref for item in run.author_outputs) + (run.derived_log_ref,)
    assert all(ref is not None for ref in refs)
    for reference in refs:
        assert reference is not None
        path = Path(f"artifacts/replication/logs/{reference.content_hash}.json")
        artifact = service_case.store.read_structured(
            path, TypeAdapter(ResearchArtifact[object])
        )
        encoded = json.dumps(artifact.payload)
        assert len(encoded.encode()) < 2_000
        assert "stdout" not in encoded or "[redacted]" in encoded
    verification = read_verification(service_case.store, report)
    assert set(refs).issubset(set(verification.envelope.input_artifacts))


def test_failed_verifier_report_is_persisted_and_exception_bound(
    service_case: ServiceCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = service_case.service.verifier.verify

    def corrupt_then_verify(run_ref: ArtifactRef):  # type: ignore[no-untyped-def]
        _, run = read_ledger(service_case.store, run_ref)
        output = run.author_outputs[0].artifact_ref
        path = service_case.store.root / (
            f"artifacts/replication/outputs/{output.content_hash}.json"
        )
        path.write_text("{}", encoding="utf-8")
        return original(run_ref)

    monkeypatch.setattr(service_case.service.verifier, "verify", corrupt_then_verify)
    report = service_case.service.run(approve(service_case))

    assert report.state is ReplicationRunState.EXCEPTION
    assert report.exception is not None
    verification_refs = tuple(
        ref
        for ref in report.exception.evidence_refs
        if ref.artifact_id == "tier2-replication-verification"
    )
    assert len(verification_refs) == 1
    assert (
        service_case.store.root
        / f"artifacts/replication/verifications/{verification_refs[0].content_hash}.json"
    ).is_file()


def test_public_status_recovers_pending_ledger_publication(
    service_case: ServiceCase,
) -> None:
    approved, acquired, runtime, started = _started_run(service_case)

    def fail_report(boundary: str) -> None:
        if boundary == "report":
            raise OSError("report")

    with pytest.raises(OSError, match="report"):
        ReplicationLedger(service_case.store, failure_injector=fail_report).heartbeat(
            started, observation()
        )
    pending_path = Path("artifacts/replication/.pending-ledger.yaml")
    pending = service_case.store.read_structured(
        pending_path, TypeAdapter(ResearchArtifact[ReplicationRun])
    )
    pending_ref = ArtifactRef(
        artifact_id=pending.envelope.artifact_id,
        artifact_version=pending.envelope.artifact_version,
        content_hash=pending.envelope.content_hash or "",
    )

    status = service_case.service.status(pending_ref)

    assert status.run_ref == pending_ref
    assert not (service_case.store.root / pending_path).exists()
    del approved, acquired, runtime


def _started_run(
    case: ServiceCase,
) -> tuple[ArtifactRef, ArtifactRef, ArtifactRef, ArtifactRef]:
    approved = approve(case)
    acquired = case.service.intake.acquire(approved, admission().approved_locator)
    observed = case.service.engine.preflight(case.proposal.runtime)
    runtime = persist_payload(
        case.store,
        "tier2-runtime-observation",
        "runtime",
        asdict(observed),
        (approved, acquired),
        "tier2-container",
    )
    coordinator = AttemptCoordinator(case.store, approved)
    with coordinator.locked():
        attempt_ref, claim = coordinator.claim()
        coordinator.allocate_root(claim)
    return (
        approved,
        acquired,
        runtime,
        case.service.ledger.start(
            approved, acquired, runtime, attempt_ref, claim.output_root
        ),
    )


def _paused_run(
    case: ServiceCase,
) -> tuple[ArtifactRef, ArtifactRef, ArtifactRef, ArtifactRef]:
    approved, acquired, runtime, started = _started_run(case)
    paused = pause_with_checkpoint(case, started)
    return approved, acquired, runtime, paused
