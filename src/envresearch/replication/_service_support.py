"""Private sealed-artifact and trusted-workspace mechanics for replication."""

from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import TypeAdapter

from envresearch.models.artifact import (
    ArtifactEnvelope,
    ArtifactRef,
    ProducerIdentity,
    ResearchArtifact,
    seal_artifact,
)
from envresearch.models.enums import ArtifactLifecycle
from envresearch.replication._attempt_support import AttemptCoordinator
from envresearch.replication._runtime_evidence import restore_runtime_observation
from envresearch.replication.container import ContainerPlan, ContainerResult
from envresearch.replication.contracts import (
    AcquiredPackageInventory,
    ApprovedTier2Intake,
    InventoryFile,
    ReplicationException,
    Tier2IntakeProposal,
)
from envresearch.replication.ledger import (
    OutputResult,
    ReplicationLedger,
    ReplicationRun,
)
from envresearch.storage.atomic import atomic_write_bytes
from envresearch.storage.hashing import sha256_file
from envresearch.storage.research_artifacts import ResearchArtifactStore

Payload = TypeVar("Payload")


def artifact_ref(artifact: ResearchArtifact[Payload]) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact.envelope.artifact_id,
        artifact_version=artifact.envelope.artifact_version,
        content_hash=artifact.envelope.content_hash or "",
    )


def artifact_path(directory: str, reference: ArtifactRef) -> Path:
    return Path(f"artifacts/replication/{directory}/{reference.content_hash}.json")


def require_safe_relative_paths(*paths: Path) -> None:
    if any(path.is_absolute() or ".." in path.parts for path in paths):
        raise ValueError("replay input paths must be safe and relative")


def read_exact(
    store: ResearchArtifactStore, directory: str, reference: ArtifactRef
) -> ResearchArtifact[object]:
    artifact = store.read_structured(
        artifact_path(directory, reference), TypeAdapter(ResearchArtifact[object])
    )
    if artifact_ref(artifact) != reference:
        raise ValueError("artifact reference mismatch")
    return artifact


def read_admission(
    store: ResearchArtifactStore, reference: ArtifactRef
) -> tuple[ResearchArtifact[ApprovedTier2Intake], Tier2IntakeProposal]:
    raw = read_exact(store, "approved", reference)
    _require_artifact(raw, "approved-tier2-intake", "tier2-intake")
    approved = ApprovedTier2Intake.model_validate_json(json.dumps(raw.payload))
    if raw.envelope.input_artifacts != (approved.proposal_ref,):
        raise ValueError("approved intake does not bind its proposal")
    typed = ResearchArtifact[ApprovedTier2Intake](
        envelope=raw.envelope, payload=approved
    )
    proposal_artifact = read_exact(store, "proposals", approved.proposal_ref)
    _require_artifact(proposal_artifact, "tier2-intake-proposal", "tier2-intake")
    if proposal_artifact.envelope.input_artifacts:
        raise ValueError("intake proposal must not claim upstream artifacts")
    proposal = restore_proposal(proposal_artifact.payload)
    return typed, proposal


def _require_artifact(
    artifact: ResearchArtifact[Payload], artifact_id: str, producer: str
) -> None:
    if artifact.envelope.artifact_id != artifact_id:
        raise ValueError("artifact ID mismatch")
    if artifact.envelope.producer.component != producer:
        raise ValueError("artifact producer mismatch")


def read_inventory(
    store: ResearchArtifactStore, reference: ArtifactRef
) -> AcquiredPackageInventory:
    artifact = read_exact(store, "inventories", reference)
    _require_artifact(artifact, "acquired-tier2-package-inventory", "tier2-intake")
    payload = artifact.payload
    return AcquiredPackageInventory.model_validate_json(json.dumps(payload))


def reopen_run_evidence(
    store: ResearchArtifactStore,
    run: ReplicationRun,
    expected_engine: str,
    expected_executable_sha256: str | None = None,
    expected_endpoint: str | None = None,
) -> tuple[
    ResearchArtifact[ApprovedTier2Intake],
    Tier2IntakeProposal,
    AcquiredPackageInventory,
    Path,
    str,
]:
    approved, proposal = read_admission(store, run.approved_intake_ref)
    inventory = read_inventory(store, run.acquired_inventory_ref)
    runtime = read_exact(store, "runtime", run.runtime_ref)
    _require_artifact(runtime, "tier2-runtime-observation", "tier2-container")
    if runtime.envelope.input_artifacts != (
        run.approved_intake_ref,
        run.acquired_inventory_ref,
    ):
        raise ValueError("runtime observation does not bind admitted run")
    observation = restore_runtime_observation(
        runtime.payload,
        expected_engine,
        expected_executable_sha256,
        expected_endpoint,
    )
    return (
        approved,
        proposal,
        inventory,
        materialize_inventory(store, inventory),
        observation.engine,
    )


def restore_proposal(payload: object) -> Tier2IntakeProposal:
    if not isinstance(payload, dict):
        raise TypeError("proposal payload must be an object")
    value = dict(payload)
    inputs = value.get("declared_inputs")
    outputs = value.get("expected_outputs")
    if not isinstance(inputs, list) or not isinstance(outputs, list):
        raise TypeError("proposal declarations must be arrays")
    restored: list[dict[str, object]] = []
    for item in inputs:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise TypeError("proposal input must contain a path")
        restored.append({**item, "path": Path(item["path"])})
    value["declared_inputs"] = tuple(restored)
    value["expected_outputs"] = tuple(outputs)
    return Tier2IntakeProposal.model_validate(value)


def seal_payload(
    artifact_id: str,
    payload: object,
    inputs: tuple[ArtifactRef, ...],
    producer: str,
    *,
    provenance: dict[str, object] | None = None,
) -> ResearchArtifact[object]:
    artifact = seal_artifact(
        ResearchArtifact(
            envelope=ArtifactEnvelope(
                artifact_id=artifact_id,
                artifact_version=1,
                run_id="tier2-replication",
                created_at=datetime.now(UTC),
                producer=ProducerIdentity(component=producer, version="0.3.0"),
                input_artifacts=inputs,
                provenance=provenance or {},
                validation_status=ArtifactLifecycle.VALIDATED,
            ),
            payload=payload,
        )
    )
    return artifact


def persist_payload(
    store: ResearchArtifactStore,
    artifact_id: str,
    directory: str,
    payload: object,
    inputs: tuple[ArtifactRef, ...],
    producer: str,
    *,
    provenance: dict[str, object] | None = None,
) -> ArtifactRef:
    artifact = seal_payload(
        artifact_id, payload, inputs, producer, provenance=provenance
    )
    reference = artifact_ref(artifact)
    store.write_structured(artifact_path(directory, reference), artifact)
    return reference


def persist_output_result(
    store: ResearchArtifactStore,
    path: str,
    digest: str,
    comparator: Literal["exact", "json_numeric", "csv_numeric"],
    inputs: tuple[ArtifactRef, ...],
    log_ref: ArtifactRef,
    raw_ref: ArtifactRef,
    provenance: dict[str, object],
) -> OutputResult:
    payload = {
        "path": path,
        "sha256": digest,
        "comparator": comparator,
        "comparison_passed": True,
        "raw_ref": raw_ref.model_dump(mode="json"),
    }
    reference = persist_payload(
        store,
        "tier2-author-output",
        "outputs",
        payload,
        (*inputs, log_ref, raw_ref),
        "tier2-replication",
        provenance=provenance,
    )
    return OutputResult(
        path=path,
        sha256=digest,
        comparator=comparator,
        comparison_passed=True,
        raw_ref=raw_ref,
        artifact_ref=reference,
        log_ref=log_ref,
    )


def execution_evidence(
    plan: ContainerPlan, result: ContainerResult
) -> dict[str, object]:
    values = asdict(result)
    values["command_sha256"] = hashlib.sha256(
        json.dumps(plan.argv, separators=(",", ":")).encode()
    ).hexdigest()
    return {"execution": values}


def materialize_inventory(
    store: ResearchArtifactStore, inventory: AcquiredPackageInventory
) -> Path:
    root = (
        store.root
        / "artifacts/replication/acquired"
        / inventory.archive_sha256
        / inventory.approved_intake_ref.content_hash
    )
    members = {item.path.as_posix(): item for item in inventory.files}
    if root.exists():
        _remove_generated(root)
        _verify_files(root, members)
        return root.resolve()
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(mode=0o700)
    archive_path = (
        store.root / f"artifacts/replication/raw/{inventory.archive_sha256}.tar.gz"
    )
    if sha256_file(archive_path) != inventory.archive_sha256:
        raise ValueError("raw archive hash differs from acquired inventory")
    with tarfile.open(archive_path, "r:gz") as archive:
        observed = {member.name: member for member in archive.getmembers()}
        if set(observed) != set(members):
            raise ValueError("archive members differ from acquired inventory")
        for name, expected in members.items():
            source = archive.extractfile(observed[name])
            if source is None or not observed[name].isreg():
                raise ValueError("inventory member is not a regular file")
            data = source.read(expected.bytes + 1)
            if len(data) != expected.bytes:
                raise ValueError("archive member size differs from inventory")
            target = root / expected.path
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(target, data)
    _verify_files(root, members)
    return root.resolve()


def persist_attempt(
    store: ResearchArtifactStore,
    subject: ArtifactRef,
    exception: ReplicationException,
    evidence: tuple[ArtifactRef, ...],
) -> ArtifactRef:
    coordinator = AttemptCoordinator(store, subject)
    with coordinator.locked():
        return coordinator.persist_failure(exception, evidence)[0]


def read_attempt(
    store: ResearchArtifactStore, subject: ArtifactRef
) -> tuple[ArtifactRef, ReplicationException] | None:
    coordinator = AttemptCoordinator(store, subject)
    with coordinator.locked():
        return coordinator.read_failure()


def read_ledger(
    store: ResearchArtifactStore, reference: ArtifactRef | None = None
) -> tuple[ArtifactRef, ReplicationRun]:
    return ReplicationLedger(store).read_current(reference)


def current_ledger_for(
    store: ResearchArtifactStore, approved: ArtifactRef
) -> tuple[ArtifactRef, ReplicationRun] | None:
    if not (store.root / "artifacts/replication/replication-ledger.yaml").exists():
        return None
    observed = read_ledger(store)
    return observed if observed[1].approved_intake_ref == approved else None


def _verify_files(root: Path, members: dict[str, InventoryFile]) -> None:
    files = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }
    if files != set(members):
        raise ValueError("materialized inputs differ from inventory")
    for name, expected in members.items():
        if sha256_file(root / name) != expected.sha256:
            raise ValueError("materialized input hash mismatch")


def _remove_generated(root: Path) -> None:
    generated = root / ".generated"
    if generated.is_symlink():
        generated.unlink()
    elif generated.exists():
        shutil.rmtree(generated)
