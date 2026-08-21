"""Offline artifact graph builders for replication verifier tests."""

from __future__ import annotations

import hashlib
import io
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import HttpUrl, TypeAdapter

from envresearch.models.artifact import (
    ArtifactEnvelope,
    ArtifactRef,
    ProducerIdentity,
    ResearchArtifact,
    seal_artifact,
)
from envresearch.models.enums import ArtifactLifecycle
from envresearch.replication._attempt_support import AttemptCoordinator
from envresearch.replication._raw_evidence import persist_raw_output
from envresearch.replication._runtime_evidence import runtime_payload
from envresearch.replication.container import RuntimeObservation
from envresearch.replication.contracts import (
    AcquiredPackageInventory,
    ApprovedTier2Intake,
    ContainerRuntimeProfile,
    DeclaredInput,
    ExternalAdmission,
    InventoryFile,
    ReplicationBudget,
    Tier2ExpectedOutput,
    Tier2IntakeProposal,
)
from envresearch.replication.ledger import OutputResult, ReplicationLedger
from envresearch.storage.hashing import sha256_file
from envresearch.storage.research_artifacts import ResearchArtifactStore

URL = TypeAdapter(HttpUrl).validate_python
SHA256 = "a" * 64


def admitted_refs(tmp_path: Path) -> tuple[ArtifactRef, ArtifactRef, ArtifactRef]:
    proposal = Tier2IntakeProposal(
        schema_version="tier2-intake-v1",
        package_id="fixture",
        canonical_url=URL("https://example.com/package.tar.gz"),
        declared_version="1",
        doi=None,
        license_name="MIT",
        license_url=URL("https://example.com/license"),
        declared_inputs=(
            DeclaredInput(
                path=Path("code/run.R"), purpose="author-code", required=True
            ),
            DeclaredInput(
                path=Path("expected/results.csv"),
                purpose="author-output-target",
                required=True,
            ),
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
            max_download_bytes=10,
            max_storage_bytes=100,
            max_memory_bytes=100,
            inactivity_seconds=10,
        ),
        self_contained=True,
    )
    proposal_ref = write_artifact(tmp_path, "tier2-intake-proposal", proposal)
    approved = write_artifact(
        tmp_path,
        "approved-tier2-intake",
        ApprovedTier2Intake(
            proposal_ref=proposal_ref,
            approval=ExternalAdmission(
                approver_id="reviewer",
                rationale="approved",
                approved_locator=URL("https://example.com/package.tar.gz"),
            ),
            approved_at=datetime.now(UTC),
        ),
        inputs=(proposal_ref,),
    )
    acquired = write_inventory(tmp_path, approved)
    runtime = write_artifact(
        tmp_path,
        "tier2-runtime-observation",
        runtime_payload(
            RuntimeObservation(
                engine="docker",
                executable_sha256="e" * 64,
                endpoint="unix:///var/run/docker.sock",
                version="test",
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                stdout_sha256="1" * 64,
                stderr_sha256="2" * 64,
                stdout_truncated=False,
                stderr_truncated=False,
                peak_memory_bytes=0,
                storage_bytes=0,
            ),
            "docker",
        ),
        producer="tier2-container",
        inputs=(approved, acquired),
    )
    return approved, acquired, runtime


def attempt_args(tmp_path: Path, approved: ArtifactRef) -> tuple[ArtifactRef, str]:
    coordinator = AttemptCoordinator(store(tmp_path), approved)
    with coordinator.locked():
        reference, claim = coordinator.claim()
        coordinator.allocate_root(claim)
    return reference, claim.output_root


def write_log(
    tmp_path: Path, stage: str, inputs: tuple[ArtifactRef, ...]
) -> ArtifactRef:
    return write_artifact(
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


def write_inventory(tmp_path: Path, approved: ArtifactRef) -> ArtifactRef:
    contents = {
        "code/run.R": b"print('fixture')\n",
        "expected/results.csv": b"estimate\n0.1\n",
    }
    staged = tmp_path / "fixture.tar.gz"
    with tarfile.open(staged, "w:gz") as archive:
        for name, data in contents.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    digest = sha256_file(staged)
    raw = tmp_path / f"artifacts/replication/raw/{digest}.tar.gz"
    raw.parent.mkdir(parents=True, exist_ok=True)
    staged.replace(raw)
    payload = AcquiredPackageInventory(
        approved_intake_ref=approved,
        archive_sha256=digest,
        archive_bytes=raw.stat().st_size,
        files=tuple(
            InventoryFile(
                path=Path(name),
                bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
            for name, data in contents.items()
        ),
    )
    materialized = (
        tmp_path / "artifacts/replication/acquired" / digest / approved.content_hash
    )
    for name, data in contents.items():
        target = materialized / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return write_artifact(
        tmp_path,
        "acquired-tier2-package-inventory",
        payload,
        inputs=(approved,),
    )


def write_artifact(
    tmp_path: Path,
    artifact_id: str,
    payload: object,
    *,
    producer: str = "tier2-intake",
    inputs: tuple[ArtifactRef, ...] = (),
) -> ArtifactRef:
    artifact = seal_artifact(
        ResearchArtifact(
            envelope=ArtifactEnvelope(
                artifact_id=artifact_id,
                artifact_version=1,
                run_id="test",
                created_at=datetime.now(UTC),
                producer=ProducerIdentity(component=producer, version="1"),
                input_artifacts=inputs,
                validation_status=ArtifactLifecycle.VALIDATED,
            ),
            payload=payload,
        )
    )
    reference = ArtifactRef(
        artifact_id=artifact_id,
        artifact_version=1,
        content_hash=artifact.envelope.content_hash or "",
    )
    relative = artifact_path(artifact_id, reference)
    store(tmp_path).write_structured(relative, artifact)
    return reference


def artifact_path(artifact_id: str, reference: ArtifactRef) -> Path:
    directories = {
        "tier2-intake-proposal": "proposals",
        "approved-tier2-intake": "approved",
        "acquired-tier2-package-inventory": "inventories",
        "tier2-runtime-observation": "runtime",
        "tier2-author-output": "outputs",
        "tier2-derived-output": "derived",
        "tier2-execution-log": "logs",
    }
    return Path(
        f"artifacts/replication/{directories[artifact_id]}/{reference.content_hash}.json"
    )


def completed_ledger(tmp_path: Path) -> tuple[ArtifactRef, Path]:
    approved, acquired, runtime = admitted_refs(tmp_path)
    ledger = ReplicationLedger(store(tmp_path))
    started = ledger.start(
        approved, acquired, runtime, *attempt_args(tmp_path, approved)
    )
    inputs = (approved, acquired, runtime)
    author_log = write_log(tmp_path, "author-reproduction", inputs)
    _, run = ledger.read_current(started)
    raw_output = tmp_path / run.output_root / "author-reproduction/output/results.csv"
    raw_output.parent.mkdir(parents=True, exist_ok=True)
    raw_output.write_bytes(b"estimate\n0.1\n")
    raw = persist_raw_output(
        store(tmp_path),
        "output/results.csv",
        raw_output,
        inputs,
        author_log,
        max_bytes=100,
    )
    digest = sha256_file(raw_output)
    output = write_artifact(
        tmp_path,
        "tier2-author-output",
        {
            "path": "output/results.csv",
            "sha256": digest,
            "comparator": "csv_numeric",
            "comparison_passed": True,
            "raw_ref": raw.model_dump(mode="json"),
        },
        producer="tier2-replication",
        inputs=(*inputs, author_log, raw),
    )
    derived_log = write_log(tmp_path, "derived-did-event-study", inputs)
    derived = write_artifact(
        tmp_path,
        "tier2-derived-output",
        {
            "schema_version": "derived-did-event-study-v1",
            "treatment_timing": {},
            "support": {},
            "balance": {},
            "event_time": {},
            "twfe_event_study": {},
            "callaway_santanna": {
                "status": "unsupported",
                "reason": "fixture has no cohorts",
            },
            "configuration": {},
        },
        producer="tier2-replication",
        inputs=(*inputs, output, derived_log),
    )
    completed = ledger.complete(
        started,
        author_outputs=(
            OutputResult(
                path="output/results.csv",
                sha256=digest,
                comparator="csv_numeric",
                comparison_passed=True,
                raw_ref=raw,
                artifact_ref=output,
                log_ref=author_log,
            ),
        ),
        derived_ref=derived,
        derived_log_ref=derived_log,
    )
    return (
        completed,
        tmp_path / f"artifacts/replication/outputs/{output.content_hash}.json",
    )


def store(tmp_path: Path) -> ResearchArtifactStore:
    return ResearchArtifactStore(tmp_path)
