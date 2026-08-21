from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter
from replication_service_fixtures import (
    FakeEngine,
    ServiceCase,
    admission,
    approve,
    pause_with_checkpoint,
)

from envresearch.models.artifact import (
    ArtifactRef,
    ProducerIdentity,
    ResearchArtifact,
    seal_artifact,
)
from envresearch.replication._attempt_support import AttemptCoordinator
from envresearch.replication._service_support import persist_payload
from envresearch.replication.contracts import ReplicationRunState
from envresearch.replication.verify import (
    ReplicationVerifier,
    VerificationPayload,
    VerificationReport,
)


def test_dry_intake_is_read_only(service_case: ServiceCase, tmp_path: Path) -> None:
    normalized = service_case.service.dry_intake(service_case.proposal)

    assert normalized == service_case.proposal
    assert not (tmp_path / "artifacts").exists()


def test_green_path_completes_without_a_second_human_gate(
    service_case: ServiceCase,
) -> None:
    proposal_ref = service_case.service.intake.record_proposal(service_case.proposal)
    approved = service_case.service.approve_external_admission(
        proposal_ref, admission()
    )

    report = service_case.service.run(approved)
    repeated = service_case.service.run(approved)

    assert (report.state, report.exception) == (ReplicationRunState.PASSED, None)
    assert report.verification_ref is not None
    assert (repeated, service_case.fetcher.calls) == (report, 1)
    engine = service_case.service.engine
    assert isinstance(engine, FakeEngine)
    assert approved.content_hash in engine.plans[0].input_root.parts


def test_forged_approval_is_rejected_before_acquisition(
    service_case: ServiceCase,
) -> None:
    proposal_ref = service_case.service.intake.record_proposal(service_case.proposal)
    approved = service_case.service.approve_external_admission(
        proposal_ref, admission()
    )
    path = Path(f"artifacts/replication/approved/{approved.content_hash}.json")
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
    forged_ref = ArtifactRef(
        artifact_id=forged.envelope.artifact_id,
        artifact_version=forged.envelope.artifact_version,
        content_hash=forged.envelope.content_hash or "",
    )
    service_case.store.write_structured(
        Path(f"artifacts/replication/approved/{forged_ref.content_hash}.json"), forged
    )

    report = service_case.service.run(forged_ref)

    assert report.state is ReplicationRunState.EXCEPTION
    assert report.exception is not None
    assert report.exception.code == "EXTERNAL_ADMISSION_REQUIRED"
    assert service_case.fetcher.calls == 0


def test_existing_attempt_namespace_is_rejected_before_acquisition(
    service_case: ServiceCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    approved = approve(service_case)
    original = AttemptCoordinator.claim

    def claim_with_planted_root(
        coordinator: AttemptCoordinator,
    ):  # type: ignore[no-untyped-def]
        reference, claim = original(coordinator)
        root = coordinator.store.root / claim.output_root
        root.mkdir(parents=True)
        return reference, claim

    monkeypatch.setattr(AttemptCoordinator, "claim", claim_with_planted_root)
    report = service_case.service.run(approved)

    assert report.exception is not None
    assert report.exception.code == "OUTPUT_NAMESPACE_INVALID"
    assert service_case.fetcher.calls == 0


def test_runtime_persistence_failure_becomes_a_durable_typed_exception(
    service_case: ServiceCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    approved = approve(service_case)
    original = service_case.store.write_structured
    failed = False

    def fail_runtime_once(relative: Path, artifact: ResearchArtifact[object]):
        nonlocal failed
        if not failed and relative.parts[:3] == (
            "artifacts",
            "replication",
            "runtime",
        ):
            failed = True
            raise OSError("runtime persistence failed")
        return original(relative, artifact)

    monkeypatch.setattr(service_case.store, "write_structured", fail_runtime_once)
    first = service_case.service.run(approved)
    repeated = service_case.service.run(approved)

    assert first == repeated
    assert first.exception is not None
    assert first.exception.code == "ADMITTED_EVIDENCE_INVALID"


def test_resume_with_replaced_checkpoint_evidence_becomes_exception(
    service_case: ServiceCase,
) -> None:
    approved = approve(service_case)
    acquired = service_case.service.intake.acquire(
        approved, admission().approved_locator
    )
    observation = service_case.service.engine.preflight(service_case.proposal.runtime)
    runtime = persist_payload(
        service_case.store,
        "tier2-runtime-observation",
        "runtime",
        asdict(observation),
        (approved, acquired),
        "tier2-container",
    )
    coordinator = AttemptCoordinator(service_case.store, approved)
    with coordinator.locked():
        attempt_ref, claim = coordinator.claim()
        coordinator.allocate_root(claim)
    started = service_case.service.ledger.start(
        approved, acquired, runtime, attempt_ref, claim.output_root
    )
    paused = pause_with_checkpoint(service_case, started)
    inventory_path = service_case.store.root / (
        f"artifacts/replication/inventories/{acquired.content_hash}.json"
    )
    inventory_path.write_text("{}", encoding="utf-8")

    report = service_case.service.resume(paused)

    assert report.state is ReplicationRunState.EXCEPTION
    assert report.exception is not None
    assert report.exception.code == "RESUME_EVIDENCE_INVALID"


@pytest.mark.parametrize("service_case", ["network"], indirect=True)
def test_undeclared_network_request_becomes_an_exception_not_a_human_gate(
    service_case: ServiceCase,
) -> None:
    report = service_case.service.run(approve(service_case))

    assert report.state is ReplicationRunState.EXCEPTION
    assert report.exception is not None
    assert report.exception.code == "UNDECLARED_EXTERNAL_ACCESS"


@pytest.mark.parametrize(
    ("service_case", "expected_code"),
    [
        ("no-engine", "NO_CONTAINER_ENGINE"),
        ("mismatch", "OUTPUT_MISMATCH"),
        ("resource", "RESOURCE_EXHAUSTION"),
        ("resource-error", "RESOURCE_EXHAUSTION"),
    ],
    indirect=["service_case"],
)
def test_known_boundary_failures_become_durable_exceptions(
    service_case: ServiceCase, expected_code: str
) -> None:
    approved = approve(service_case)
    first = service_case.service.run(approved)
    repeated = service_case.service.run(approved)

    assert first.state is ReplicationRunState.EXCEPTION
    assert first.exception is not None
    assert first.exception.code == expected_code
    assert repeated == first
    assert service_case.fetcher.calls == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"stdout_sha256": "raw secret from stdout"},
        {"stderr_sha256": "A" * 64},
        {"stdout_truncated": 1},
        {"exit_status": False},
        {"engine": "raw secret engine identity"},
        {"engine": "other-engine"},
        {"image_digest": f"attacker/r@sha256:{'b' * 64}"},
        {"started_at": datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)},
        {
            "started_at": datetime(2026, 1, 1, tzinfo=UTC),
            "finished_at": datetime(2026, 1, 1, tzinfo=UTC) - timedelta(seconds=1),
        },
        {"peak_memory_bytes": -1},
        {"storage_bytes": -1},
    ],
)
def test_engine_result_is_validated_before_log_persistence(
    service_case: ServiceCase,
    monkeypatch: pytest.MonkeyPatch,
    updates: dict[str, object],
) -> None:
    engine = service_case.service.engine
    assert isinstance(engine, FakeEngine)
    original = engine.run

    def forged_result(  # type: ignore[no-untyped-def]
        plan, *, on_progress=None, on_started=None, on_stopped=None
    ):
        result = original(
            plan,
            on_progress=on_progress,
            on_started=on_started,
            on_stopped=on_stopped,
        )
        return replace(result, **updates)

    monkeypatch.setattr(engine, "run", forged_result)
    report = service_case.service.run(approve(service_case))

    assert report.exception is not None
    assert report.exception.code == "CONTAINER_EVIDENCE_INVALID"
    assert not tuple(
        (service_case.store.root / "artifacts/replication/logs").glob("*.json")
    )


def test_engine_rejects_a_container_result_impostor_before_persistence(
    service_case: ServiceCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = service_case.service.engine
    assert isinstance(engine, FakeEngine)
    original = engine.run

    def forged_result(  # type: ignore[no-untyped-def]
        plan, *, on_progress=None, on_started=None, on_stopped=None
    ):
        result = original(
            plan,
            on_progress=on_progress,
            on_started=on_started,
            on_stopped=on_stopped,
        )
        return SimpleNamespace(**asdict(result))

    monkeypatch.setattr(engine, "run", forged_result)
    report = service_case.service.run(approve(service_case))

    assert report.exception is not None
    assert report.exception.code == "CONTAINER_EVIDENCE_INVALID"
    assert not tuple(
        (service_case.store.root / "artifacts/replication/logs").glob("*.json")
    )


def test_second_verifier_failure_is_persisted_and_bound_to_exception(
    service_case: ServiceCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = ReplicationVerifier.verify
    calls = 0

    def mutate_after_first(
        verifier: ReplicationVerifier, run_ref: ArtifactRef
    ) -> VerificationReport:
        nonlocal calls
        report = original(verifier, run_ref)
        calls += 1
        if calls == 1:
            run = service_case.service.ledger.read_current(run_ref)[1]
            output_ref = run.author_outputs[0].artifact_ref
            path = service_case.store.root / (
                f"artifacts/replication/outputs/{output_ref.content_hash}.json"
            )
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["payload"]["sha256"] = "f" * 64
            path.write_text(json.dumps(raw), encoding="utf-8")
        return report

    monkeypatch.setattr(ReplicationVerifier, "verify", mutate_after_first)
    report = service_case.service.run(approve(service_case))

    assert calls == 2
    assert report.exception is not None
    assert report.exception.code == "VERIFICATION_FAILED"
    assert len(report.exception.evidence_refs) == 1
    failed_ref = report.exception.evidence_refs[0]
    stored_raw = service_case.store.read_structured(
        Path(f"artifacts/replication/verifications/{failed_ref.content_hash}.json"),
        TypeAdapter(ResearchArtifact[object]),
    )
    stored = TypeAdapter(ResearchArtifact[VerificationPayload]).validate_python(
        stored_raw.model_copy(
            update={
                "payload": VerificationPayload.model_validate_json(
                    json.dumps(stored_raw.payload)
                )
            }
        )
    )
    assert VerificationReport(artifact=stored).passed is False
    assert (
        len(
            tuple(
                (service_case.store.root / "artifacts/replication/verifications").glob(
                    "*.json"
                )
            )
        )
        == 2
    )
