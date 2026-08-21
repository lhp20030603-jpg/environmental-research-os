"""Tests for independent read-only Tier-2 artifact verification."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import HttpUrl, TypeAdapter
from replication_verify_fixtures import (
    admitted_refs,
    attempt_args,
    completed_ledger,
    store,
)

from envresearch.models.artifact import (
    ArtifactEnvelope,
    ArtifactRef,
    ProducerIdentity,
    ResearchArtifact,
    seal_artifact,
)
from envresearch.models.enums import ArtifactLifecycle
from envresearch.replication._service_support import (
    persist_attempt,
    read_attempt,
    read_ledger,
)
from envresearch.replication._verification_models import seal_verification
from envresearch.replication.contracts import (
    ReplicationException,
    ReplicationRunState,
)
from envresearch.replication.ledger import ReplicationLedger, ReplicationRun
from envresearch.replication.verify import (
    ReplicationVerifier,
    VerificationReport,
)

URL = TypeAdapter(HttpUrl).validate_python
SHA256 = "a" * 64


def test_verifier_rejects_a_ledger_with_a_replaced_output_artifact(
    tmp_path: Path,
) -> None:
    """Replacing current output evidence must invalidate its exact reference."""
    completed, output_path = completed_ledger(tmp_path)
    raw = json.loads(output_path.read_text(encoding="utf-8"))
    raw["payload"]["sha256"] = "f" * 64
    output_path.write_text(json.dumps(raw), encoding="utf-8")

    report = ReplicationVerifier(store(tmp_path)).verify(completed)

    assert report.passed is False
    assert report.findings[0].code == "OUTPUT_REFERENCE_INVALID"


def test_verifier_reopens_current_ledger_history_and_report(tmp_path: Path) -> None:
    """A replaced immutable ledger generation cannot pass via the current alias."""
    completed, _ = completed_ledger(tmp_path)
    history = (
        tmp_path
        / "artifacts/replication/.versions/replication-ledger"
        / f"{completed.artifact_version:04d}.yaml"
    )
    history.write_text("invalid: [unterminated\n", encoding="utf-8")

    report = ReplicationVerifier(store(tmp_path)).verify(completed)

    assert report.passed is False
    assert report.findings[0].code == "LEDGER_HISTORY_INVALID"


def test_verifier_reports_every_exact_reference_on_the_green_path(
    tmp_path: Path,
) -> None:
    """Omitting one referenced artifact from verification must make promotion fail."""
    completed, _ = completed_ledger(tmp_path)

    report = ReplicationVerifier(store(tmp_path)).verify(completed)

    assert report.passed is True
    assert report.findings == ()
    assert report.run_ref == completed
    assert len(report.verified_refs) == 11


def test_only_zero_finding_verification_promotes_a_completed_run(
    tmp_path: Path,
) -> None:
    completed, _ = completed_ledger(tmp_path)
    ledger = ReplicationLedger(store(tmp_path))
    before = store(tmp_path).read_structured(
        Path("artifacts/replication/replication-ledger.yaml"),
        TypeAdapter(ResearchArtifact[ReplicationRun]),
    )
    assert before.payload.state is ReplicationRunState.RUNNING

    positive = ReplicationVerifier(store(tmp_path)).verify(completed)
    passed = ledger.publish_verification(completed, positive)

    after = store(tmp_path).read_structured(
        Path("artifacts/replication/replication-ledger.yaml"),
        TypeAdapter(ResearchArtifact[ReplicationRun]),
    )
    assert after.payload.state is ReplicationRunState.PASSED
    assert after.payload.verification_ref is not None
    assert ledger.load_current(passed) == passed


def test_publish_rejects_a_caller_forged_zero_finding_report(tmp_path: Path) -> None:
    completed, output_path = completed_ledger(tmp_path)
    raw = json.loads(output_path.read_text(encoding="utf-8"))
    raw["payload"]["sha256"] = "f" * 64
    output_path.write_text(json.dumps(raw), encoding="utf-8")
    observed = ReplicationVerifier(store(tmp_path)).verify(completed)
    forged_payload = observed.artifact.payload.model_copy(update={"findings": ()})
    forged = VerificationReport(
        artifact=seal_artifact(
            observed.artifact.model_copy(update={"payload": forged_payload})
        )
    )

    with pytest.raises(ValueError, match="independent verifier"):
        ReplicationLedger(store(tmp_path)).publish_verification(completed, forged)


def test_exception_preserves_the_first_typed_failure(tmp_path: Path) -> None:
    approved, acquired, runtime = admitted_refs(tmp_path)
    ledger = ReplicationLedger(store(tmp_path))
    started = ledger.start(
        approved, acquired, runtime, *attempt_args(tmp_path, approved)
    )
    first = ledger.exception(
        started,
        ReplicationException(code="NO_CONTAINER_ENGINE", message="engine unavailable"),
    )

    repeated = ledger.exception(
        first,
        ReplicationException(code="RETRY_FAILURE", message="must not replace first"),
    )

    report = store(tmp_path).read_structured(
        Path("artifacts/replication/replication-ledger.yaml"),
        TypeAdapter(ResearchArtifact[ReplicationRun]),
    )
    assert repeated == first
    assert report.payload.exception is not None
    assert report.payload.exception.code == "NO_CONTAINER_ENGINE"


def test_attempt_alias_rejects_another_subjects_sealed_exception(
    tmp_path: Path,
) -> None:
    first, second, _ = admitted_refs(tmp_path)
    exception = ReplicationException(code="ARCHIVE_REJECTED", message="bad archive")
    persist_attempt(store(tmp_path), first, exception, ())
    second_ref = persist_attempt(store(tmp_path), second, exception, ())
    second_path = Path(
        f"artifacts/replication/attempts/reports/{second_ref.content_hash}.json"
    )
    replacement = store(tmp_path).read_structured(
        second_path, TypeAdapter(ResearchArtifact[object])
    )
    store(tmp_path).write_structured(
        Path(f"artifacts/replication/attempts/current/{first.content_hash}.json"),
        replacement,
    )

    with pytest.raises(ValueError, match="attempt subject"):
        read_attempt(store(tmp_path), first)


def test_public_ledger_reader_rejects_a_forged_passed_alias(tmp_path: Path) -> None:
    approved, acquired, runtime = admitted_refs(tmp_path)
    forged = seal_artifact(
        ResearchArtifact(
            envelope=ArtifactEnvelope(
                artifact_id="attacker-ledger",
                artifact_version=1,
                run_id="forged",
                created_at=datetime.now(UTC),
                producer=ProducerIdentity(component="attacker", version="1"),
                validation_status=ArtifactLifecycle.VALIDATED,
            ),
            payload=ReplicationRun(
                attempt_ref=approved,
                output_root="artifacts/replication/runs/forged/forged",
                approved_intake_ref=approved,
                acquired_inventory_ref=acquired,
                runtime_ref=runtime,
                declared_outputs=("output/results.csv",),
                max_growth_bytes=0,
                state=ReplicationRunState.PASSED,
            ),
        )
    )
    store(tmp_path).write_structured(
        Path("artifacts/replication/replication-ledger.yaml"),
        cast(ResearchArtifact[object], forged),
    )

    with pytest.raises(ValueError, match="artifact ID"):
        read_ledger(store(tmp_path))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("artifact_id", "attacker-ledger"),
        ("producer", ProducerIdentity(component="attacker", version="1")),
        (
            "producer",
            ProducerIdentity(component="replication-ledger", version="attacker"),
        ),
        ("validation_status", ArtifactLifecycle.PRODUCED),
        ("artifact_version", 2),
    ],
)
def test_passed_ledger_authenticates_exact_completed_predecessor_identity(
    tmp_path: Path, field: str, value: object
) -> None:
    completed, _ = completed_ledger(tmp_path)
    ledger = ReplicationLedger(store(tmp_path))
    positive = ReplicationVerifier(store(tmp_path)).verify(completed)
    passed = ledger.publish_verification(completed, positive)
    current_path = Path("artifacts/replication/replication-ledger.yaml")
    current = store(tmp_path).read_structured(
        current_path, TypeAdapter(ResearchArtifact[ReplicationRun])
    )
    predecessor_path = Path(
        "artifacts/replication/.versions/replication-ledger/"
        f"{passed.artifact_version - 1:04d}.yaml"
    )
    predecessor = store(tmp_path).read_structured(
        predecessor_path, TypeAdapter(ResearchArtifact[ReplicationRun])
    )
    forged_predecessor = seal_artifact(
        predecessor.model_copy(
            update={"envelope": predecessor.envelope.model_copy(update={field: value})}
        )
    )
    forged_predecessor_ref = ArtifactRef(
        artifact_id=forged_predecessor.envelope.artifact_id,
        artifact_version=forged_predecessor.envelope.artifact_version,
        content_hash=forged_predecessor.envelope.content_hash or "",
    )
    verified_refs = (
        *positive.verified_refs[:-1],
        forged_predecessor_ref,
    )
    forged_report = seal_verification(forged_predecessor_ref, verified_refs, [])
    forged_verification_ref = ArtifactRef(
        artifact_id=forged_report.artifact.envelope.artifact_id,
        artifact_version=forged_report.artifact.envelope.artifact_version,
        content_hash=forged_report.artifact.envelope.content_hash or "",
    )
    forged_payload = current.payload.model_copy(
        update={"verification_ref": forged_verification_ref}
    )
    forged_current = seal_artifact(
        current.model_copy(
            update={
                "envelope": current.envelope.model_copy(
                    update={
                        "input_artifacts": (
                            *current.envelope.input_artifacts[:-1],
                            forged_verification_ref,
                        )
                    }
                ),
                "payload": forged_payload,
            }
        )
    )
    untyped_report = cast(ResearchArtifact[object], forged_report.artifact)
    store(tmp_path).write_structured(
        Path(
            "artifacts/replication/verifications/"
            f"{forged_verification_ref.content_hash}.json"
        ),
        untyped_report,
    )
    store(tmp_path).write_structured(
        predecessor_path, cast(ResearchArtifact[object], forged_predecessor)
    )
    for path in (
        current_path,
        Path("artifacts/replication/replication-report.json"),
        Path(
            "artifacts/replication/.versions/replication-ledger/"
            f"{passed.artifact_version:04d}.yaml"
        ),
    ):
        store(tmp_path).write_structured(
            path, cast(ResearchArtifact[object], forged_current)
        )

    with pytest.raises(ValueError, match="predecessor"):
        ledger.read_current()


def test_current_ledger_authenticates_the_full_producer_identity(
    tmp_path: Path,
) -> None:
    completed, _ = completed_ledger(tmp_path)
    ledger = ReplicationLedger(store(tmp_path))
    positive = ReplicationVerifier(store(tmp_path)).verify(completed)
    passed = ledger.publish_verification(completed, positive)
    current = store(tmp_path).read_structured(
        Path("artifacts/replication/replication-ledger.yaml"),
        TypeAdapter(ResearchArtifact[ReplicationRun]),
    )
    forged = seal_artifact(
        current.model_copy(
            update={
                "envelope": current.envelope.model_copy(
                    update={
                        "producer": ProducerIdentity(
                            component="replication-ledger", version="attacker"
                        )
                    }
                )
            }
        )
    )
    for path in (
        Path("artifacts/replication/replication-ledger.yaml"),
        Path("artifacts/replication/replication-report.json"),
        Path(
            "artifacts/replication/.versions/replication-ledger/"
            f"{passed.artifact_version:04d}.yaml"
        ),
    ):
        store(tmp_path).write_structured(path, cast(ResearchArtifact[object], forged))

    with pytest.raises(ValueError, match="producer"):
        ledger.read_current()
