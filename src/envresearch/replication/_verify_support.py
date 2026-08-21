"""Private exact-reference checks used by the read-only verifier."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import TypeAdapter

from envresearch.models.artifact import ArtifactRef, ResearchArtifact
from envresearch.models.enums import ArtifactLifecycle
from envresearch.replication._runtime_evidence import restore_runtime_observation
from envresearch.replication._service_support import artifact_ref
from envresearch.replication.contracts import AcquiredPackageInventory
from envresearch.replication.ledger import ReplicationRun
from envresearch.storage.research_artifacts import ResearchArtifactStore

Payload = TypeVar("Payload")


def required_refs(
    ledger: ResearchArtifact[ReplicationRun], proposal_ref: ArtifactRef
) -> tuple[ArtifactRef, ...]:
    payload = ledger.payload
    return (
        proposal_ref,
        payload.attempt_ref,
        payload.approved_intake_ref,
        payload.acquired_inventory_ref,
        payload.runtime_ref,
        *(
            reference
            for item in payload.author_outputs
            for reference in (item.log_ref, item.raw_ref, item.artifact_ref)
        ),
        *((payload.derived_ref,) if payload.derived_ref is not None else ()),
        *((payload.derived_log_ref,) if payload.derived_log_ref is not None else ()),
        artifact_ref(ledger),
    )


def read_ledger_reference(
    store: ResearchArtifactStore, path: Path, reference: ArtifactRef
) -> ResearchArtifact[ReplicationRun]:
    artifact = store.read_structured(
        path, TypeAdapter(ResearchArtifact[ReplicationRun])
    )
    require_identity(
        artifact,
        reference,
        artifact_id="replication-ledger",
        producer="replication-ledger",
    )
    if artifact.envelope.validation_status is not ArtifactLifecycle.VALIDATED:
        raise ValueError("ledger is not validated")
    return artifact


def ledger_history_path(reference: ArtifactRef) -> Path:
    return Path(
        "artifacts/replication/.versions/replication-ledger/"
        f"{reference.artifact_version:04d}.yaml"
    )


def read_artifact_reference(
    store: ResearchArtifactStore,
    path: Path,
    reference: ArtifactRef,
    *,
    artifact_id: str,
    producer: str,
) -> ResearchArtifact[object]:
    artifact = store.read_structured(path, TypeAdapter(ResearchArtifact[object]))
    require_identity(artifact, reference, artifact_id=artifact_id, producer=producer)
    return artifact


def require_identity(
    artifact: ResearchArtifact[Payload],
    reference: ArtifactRef,
    *,
    artifact_id: str,
    producer: str,
) -> None:
    if artifact_ref(artifact) != reference:
        raise ValueError("artifact reference mismatch")
    if artifact.envelope.artifact_id != artifact_id:
        raise ValueError("artifact ID mismatch")
    if artifact.envelope.producer.component != producer:
        raise ValueError("artifact producer mismatch")


def require_copy(
    store: ResearchArtifactStore,
    path: Path,
    expected: ResearchArtifact[ReplicationRun],
) -> None:
    observed = store.read_structured(
        path, TypeAdapter(ResearchArtifact[ReplicationRun])
    )
    if observed != expected:
        raise ValueError("ledger copy differs from current generation")


def require_inputs(
    artifact: ResearchArtifact[Payload], expected: tuple[ArtifactRef, ...]
) -> None:
    if artifact.envelope.input_artifacts != expected:
        raise ValueError("artifact input chain differs from current refs")


def require_payload(artifact: ResearchArtifact[object], expected: object) -> None:
    if artifact.payload != expected:
        raise ValueError("artifact payload differs from current evidence")


def admission_reference_findings(
    ledger: ResearchArtifact[ReplicationRun],
    approved: ResearchArtifact[object] | None,
    proposal: ResearchArtifact[object] | None,
    inventory: ResearchArtifact[object] | None,
    runtime: ResearchArtifact[object] | None,
    proposal_ref: ArtifactRef,
) -> tuple[tuple[str, str], ...]:
    run = ledger.payload
    expected = (
        (approved, (proposal_ref,), "APPROVAL_INPUT_CHAIN_INVALID"),
        (proposal, (), "PROPOSAL_INPUT_CHAIN_INVALID"),
        (inventory, (run.approved_intake_ref,), "INVENTORY_INPUT_CHAIN_INVALID"),
        (
            runtime,
            (run.approved_intake_ref, run.acquired_inventory_ref),
            "RUNTIME_INPUT_CHAIN_INVALID",
        ),
    )
    findings: list[tuple[str, str]] = []
    for artifact, inputs, code in expected:
        if artifact is None:
            continue
        try:
            require_inputs(artifact, inputs)
        except ValueError as error:
            findings.append((code, str(error)))
    if inventory is not None:
        try:
            value = AcquiredPackageInventory.model_validate_json(
                json.dumps(inventory.payload)
            )
            if value.approved_intake_ref != run.approved_intake_ref:
                raise ValueError("inventory payload does not bind approval")
        except (TypeError, ValueError) as error:
            findings.append(("INVENTORY_INPUT_CHAIN_INVALID", str(error)))
    if runtime is not None:
        try:
            restore_runtime_observation(runtime.payload)
        except (TypeError, ValueError) as error:
            findings.append(("RUNTIME_REFERENCE_INVALID", str(error)))
    return tuple(findings)


def require_output_evidence(
    ledger: ResearchArtifact[ReplicationRun],
    result: dict[str, object],
    artifact: ResearchArtifact[object],
) -> None:
    run = ledger.payload
    require_inputs(
        artifact,
        (
            run.approved_intake_ref,
            run.acquired_inventory_ref,
            run.runtime_ref,
            ArtifactRef.model_validate(result["log_ref"]),
            ArtifactRef.model_validate(result["raw_ref"]),
        ),
    )
    expected = dict(result)
    expected.pop("artifact_ref", None)
    expected.pop("log_ref", None)
    require_payload(artifact, expected)


def require_ledger_evidence(ledger: ResearchArtifact[ReplicationRun]) -> None:
    run = ledger.payload
    expected = (
        run.attempt_ref,
        run.approved_intake_ref,
        run.acquired_inventory_ref,
        run.runtime_ref,
        *(
            reference
            for item in run.author_outputs
            for reference in (item.log_ref, item.raw_ref, item.artifact_ref)
        ),
        *((run.derived_ref,) if run.derived_ref is not None else ()),
        *((run.derived_log_ref,) if run.derived_log_ref is not None else ()),
        *((run.verification_ref,) if run.verification_ref is not None else ()),
        *((run.exception.evidence_refs) if run.exception is not None else ()),
    )
    require_inputs(ledger, expected)


def require_log_evidence(
    artifact: ResearchArtifact[object], expected: tuple[ArtifactRef, ...]
) -> None:
    require_inputs(artifact, expected)
    if not isinstance(artifact.payload, dict):
        raise TypeError("execution log payload must be an object")
    if (
        artifact.payload.get("stdout") != "[redacted]"
        or artifact.payload.get("stderr") != "[redacted]"
    ):
        raise ValueError("execution log is not redacted")
    if len(json.dumps(artifact.payload).encode()) >= 2_000:
        raise ValueError("execution log exceeds bounded payload size")
