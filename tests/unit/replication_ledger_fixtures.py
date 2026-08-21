"""Offline evidence builders for replication ledger tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import HttpUrl, TypeAdapter

from envresearch.models.artifact import ArtifactRef, ResearchArtifact
from envresearch.replication._attempt_support import AttemptCoordinator
from envresearch.replication.contracts import (
    AcquiredPackageInventory,
    ApprovedTier2Intake,
    ContainerRuntimeProfile,
    ExternalAdmission,
    ReplicationBudget,
    Tier2ExpectedOutput,
    Tier2IntakeProposal,
)
from envresearch.replication.ledger import (
    OutputResult,
    ReplicationLedger,
    ResourceObservation,
)
from envresearch.storage.research_artifacts import ResearchArtifactStore

URL = TypeAdapter(HttpUrl).validate_python
SHA256 = "a" * 64


def store(tmp_path: Path) -> ResearchArtifactStore:
    return ResearchArtifactStore(tmp_path)


def started_run(tmp_path: Path) -> ArtifactRef:
    approved = approved_ref(tmp_path)
    acquired = acquired_ref(tmp_path, approved, "a" * 64)
    return ReplicationLedger(store(tmp_path)).start(
        approved,
        acquired,
        runtime_ref(tmp_path, approved, acquired),
        *attempt_args(tmp_path, approved),
    )


def attempt_args(tmp_path: Path, approved: ArtifactRef) -> tuple[ArtifactRef, str]:
    coordinator = AttemptCoordinator(store(tmp_path), approved)
    with coordinator.locked():
        reference, claim = coordinator.claim()
    return reference, claim.output_root


def approved_ref(tmp_path: Path) -> ArtifactRef:
    proposal = Tier2IntakeProposal(
        schema_version="tier2-intake-v1",
        package_id="fixture",
        canonical_url=URL("https://example.com/package.tar.gz"),
        declared_version="1",
        doi=None,
        license_name="MIT",
        license_url=URL("https://example.com/license"),
        declared_inputs=(
            {"path": Path("code/run.R"), "purpose": "author-code", "required": True},
        ),
        expected_outputs=(
            Tier2ExpectedOutput(
                path="output/results.csv",
                comparator="csv_numeric",
                expected_path="expected/results.csv",
            ),
        ),
        runtime=ContainerRuntimeProfile(
            profile_id="r-did-v1",
            image_digest=f"example/r@sha256:{SHA256}",
            nonroot_uid_gid="1000:1000",
        ),
        budget=ReplicationBudget(
            max_download_bytes=1,
            max_storage_bytes=100,
            max_memory_bytes=100,
            inactivity_seconds=10,
        ),
        self_contained=True,
    )
    proposal_ref = _write(tmp_path, "tier2-intake-proposal", proposal)
    approved = ApprovedTier2Intake(
        proposal_ref=proposal_ref,
        approval=ExternalAdmission(
            approver_id="reviewer",
            rationale="approved",
            approved_locator=URL("https://example.com/package.tar.gz"),
        ),
        approved_at=datetime.now(UTC),
    )
    return _write(tmp_path, "approved-tier2-intake", approved, inputs=(proposal_ref,))


def acquired_ref(tmp_path: Path, approved: ArtifactRef, digest: str) -> ArtifactRef:
    inventory = AcquiredPackageInventory(
        approved_intake_ref=approved,
        archive_sha256=digest,
        archive_bytes=1,
        files=(),
    )
    return _write_inventory(tmp_path, inventory)


def runtime_ref(
    tmp_path: Path, approved: ArtifactRef, acquired: ArtifactRef
) -> ArtifactRef:
    return _write(
        tmp_path,
        "tier2-runtime-observation",
        {"engine": "docker", "version": "test"},
        producer="tier2-container",
        inputs=(approved, acquired),
    )


def observation() -> ResourceObservation:
    return ResourceObservation(
        elapsed_seconds=1,
        storage_bytes=1,
        memory_bytes=1,
        heartbeat_at=datetime.now(UTC),
    )


def derived_ref() -> ArtifactRef:
    return ArtifactRef(artifact_id="derived", artifact_version=1, content_hash="1" * 64)


def completion_evidence(
    tmp_path: Path,
) -> tuple[tuple[OutputResult, ...], ArtifactRef, ArtifactRef]:
    artifact = store(tmp_path).read_structured(
        Path("artifacts/replication/replication-ledger.yaml"),
        TypeAdapter(ResearchArtifact[object]),
    )
    assert isinstance(artifact.payload, dict)
    inputs = tuple(
        ArtifactRef.model_validate(artifact.payload[field])
        for field in ("approved_intake_ref", "acquired_inventory_ref", "runtime_ref")
    )
    result_payload = {
        "path": "output/results.csv",
        "sha256": "c" * 64,
        "comparator": "csv_numeric",
        "comparison_passed": True,
    }
    log = _write_log(tmp_path, "author-reproduction", inputs)
    raw = _write(
        tmp_path,
        "tier2-raw-author-output",
        {
            "path": "output/results.csv",
            "sha256": "c" * 64,
            "bytes": 1,
            "blob": f"artifacts/replication/raw-outputs/blobs/{'c' * 64}.bin",
        },
        producer="tier2-replication",
        inputs=(*inputs, log),
    )
    result_payload["raw_ref"] = raw.model_dump(mode="json")
    output = _write(
        tmp_path,
        "tier2-author-output",
        result_payload,
        producer="tier2-replication",
        inputs=(*inputs, log, raw),
    )
    result = OutputResult(artifact_ref=output, log_ref=log, **result_payload)
    derived_log = _write_log(tmp_path, "derived-did-event-study", inputs)
    derived = _write(
        tmp_path,
        "tier2-derived-output",
        {"status": "passed"},
        producer="tier2-replication",
        inputs=(*inputs, output, derived_log),
    )
    return (result,), derived, derived_log


def run_inputs(
    tmp_path: Path,
) -> tuple[ArtifactRef, ArtifactRef, ArtifactRef, ArtifactRef, str]:
    artifact = store(tmp_path).read_structured(
        Path("artifacts/replication/replication-ledger.yaml"),
        TypeAdapter(ResearchArtifact[object]),
    )
    assert isinstance(artifact.payload, dict)
    return tuple(
        ArtifactRef.model_validate(artifact.payload[field])
        for field in (
            "approved_intake_ref",
            "acquired_inventory_ref",
            "runtime_ref",
            "attempt_ref",
        )
    ) + (str(artifact.payload["output_root"]),)  # type: ignore[return-value]


def _write_log(
    tmp_path: Path, stage: str, inputs: tuple[ArtifactRef, ...]
) -> ArtifactRef:
    return _write(
        tmp_path,
        "tier2-execution-log",
        {
            "stage": stage,
            "stdout_sha256": "1" * 64,
            "stderr_sha256": "2" * 64,
            "stdout_truncated": False,
            "stderr_truncated": False,
            "stdout": "[redacted]",
            "stderr": "[redacted]",
        },
        producer="tier2-replication",
        inputs=inputs,
    )


def _write(
    tmp_path: Path,
    artifact_id: str,
    payload: object,
    *,
    producer: str = "tier2-intake",
    inputs: tuple[ArtifactRef, ...] = (),
) -> ArtifactRef:
    from envresearch.models.artifact import (
        ArtifactEnvelope,
        ProducerIdentity,
        seal_artifact,
    )

    artifact = seal_artifact(
        ResearchArtifact(
            envelope=ArtifactEnvelope(
                artifact_id=artifact_id,
                artifact_version=1,
                run_id="test",
                created_at=datetime.now(UTC),
                producer=ProducerIdentity(component=producer, version="1"),
                input_artifacts=inputs,
            ),
            payload=payload,
        )
    )
    reference = ArtifactRef(
        artifact_id=artifact.envelope.artifact_id,
        artifact_version=artifact.envelope.artifact_version,
        content_hash=artifact.envelope.content_hash or "",
    )
    if artifact_id == "tier2-intake-proposal":
        relative = Path(
            f"artifacts/replication/proposals/{reference.content_hash}.json"
        )
    elif artifact_id == "tier2-runtime-observation":
        relative = Path(f"artifacts/replication/runtime/{reference.content_hash}.json")
    elif artifact_id == "tier2-author-output":
        relative = Path(f"artifacts/replication/outputs/{reference.content_hash}.json")
    elif artifact_id == "tier2-raw-author-output":
        relative = Path(
            f"artifacts/replication/raw-output-manifests/{reference.content_hash}.json"
        )
    elif artifact_id == "tier2-derived-output":
        relative = Path(f"artifacts/replication/derived/{reference.content_hash}.json")
    elif artifact_id == "tier2-execution-log":
        relative = Path(f"artifacts/replication/logs/{reference.content_hash}.json")
    else:
        relative = Path(f"artifacts/replication/approved/{reference.content_hash}.json")
    store(tmp_path).write_structured(relative, artifact)
    return reference


def _write_inventory(tmp_path: Path, payload: AcquiredPackageInventory) -> ArtifactRef:
    from envresearch.models.artifact import (
        ArtifactEnvelope,
        ProducerIdentity,
        seal_artifact,
    )

    artifact = seal_artifact(
        ResearchArtifact(
            envelope=ArtifactEnvelope(
                artifact_id="acquired-tier2-package-inventory",
                artifact_version=1,
                run_id="test",
                created_at=datetime.now(UTC),
                producer=ProducerIdentity(component="tier2-intake", version="1"),
                input_artifacts=(payload.approved_intake_ref,),
            ),
            payload=payload,
        )
    )
    reference = ArtifactRef(
        artifact_id="acquired-tier2-package-inventory",
        artifact_version=1,
        content_hash=artifact.envelope.content_hash or "",
    )
    store(tmp_path).write_structured(
        Path(f"artifacts/replication/inventories/{reference.content_hash}.json"),
        artifact,
    )
    return reference
