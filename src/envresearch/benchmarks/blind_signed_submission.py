"""Import externally signed human candidates into authenticated local queues."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

from pydantic import BaseModel

from envresearch.benchmarks.blind_authority import (
    SignedHumanEvidence,
    canonical_json,
    verify_human_evidence,
)
from envresearch.benchmarks.blind_evidence_artifacts import persist_signed_evidence
from envresearch.models.artifact import ArtifactRef
from envresearch.models.principal import PrincipalAssignment, PrincipalKind
from envresearch.workers.queue import FilesystemWorkerQueue

if TYPE_CHECKING:
    from envresearch.research.order_issuance import BlindControllerInfrastructure

ModelT = TypeVar("ModelT", bound=BaseModel)


def accept_expert_score(
    infrastructure: BlindControllerInfrastructure,
    slot: int,
    payload: SignedHumanEvidence | None,
) -> ArtifactRef:
    from envresearch.models.benchmark_evaluation import ExpertScoreSheet
    queue = infrastructure.expert_queues.get(slot)
    if queue is None:
        raise ValueError("expert order has not been issued")
    order_id = f"expert-score-{slot}"
    if payload is not None:
        if not isinstance(payload, SignedHumanEvidence):
            raise ValueError("externally signed human evidence is required")
        import_signed_submission(
            infrastructure, queue, order_id, payload, ExpertScoreSheet,
            PrincipalKind.EXPERT, slot,
        )
    return infrastructure.accept_submission(order_id)  # type: ignore[attr-defined,no-any-return]


def import_signed_submission(
    infrastructure: BlindControllerInfrastructure,
    queue: FilesystemWorkerQueue,
    order_id: str,
    evidence: SignedHumanEvidence,
    model: type[ModelT],
    kind: PrincipalKind,
    slot: int,
) -> ModelT:
    """Verify the current external signer before creating a local queue receipt."""
    package = SignedHumanEvidence.model_validate(evidence.model_dump(mode="json"))
    order = queue.read_order(order_id)
    assignment = cast(PrincipalAssignment, order.principal_assignment)
    if assignment.kind is not kind or assignment.verification.value != "public_key_signature":
        raise ValueError("human order lacks an external public-key assignment")
    participant = infrastructure.registry.enrolled_human_key(
        infrastructure.case_id, kind, slot
    )
    if (
        assignment.principal_id != participant.principal_id
        or assignment.key_id != participant.key_id
        or assignment.public_key_sha256 != participant.public_key_sha256
        or order.order_hash is None
    ):
        raise ValueError("human assignment does not match participant enrollment")
    data = verify_human_evidence(
        package,
        participant,
        case_id=infrastructure.case_id,
        role=kind,
        slot=slot,
        source_generation=infrastructure._source_generation(),
        assignment_id=assignment.assignment_id,
        order_hash=order.order_hash,
        candidate_schema=order.expected_output_schema,
    )
    candidate = model.model_validate_json(data)
    if data != canonical_json(candidate.model_dump(mode="json")):
        raise ValueError("signed human candidate is not canonical")
    principal = getattr(candidate, "scorer_principal", None)
    if principal is None:
        principal = getattr(candidate, "adjudicator_principal", None)
    if principal != assignment.principal_id:
        raise ValueError("signed human candidate principal does not match assignment")
    persist_signed_evidence(
        infrastructure.artifacts,
        infrastructure.case_id,
        package,
        assignment,
        order.input_artifacts,
    )
    _persist_evidence(queue, order_id, package)
    infrastructure._submit_payload(queue, order_id, candidate)
    return candidate


def read_signed_evidence(
    queue: FilesystemWorkerQueue, order_id: str
) -> SignedHumanEvidence:
    path = _evidence_path(order_id)
    try:
        data = queue.control.storage.read_file(
            path, description="external signed human evidence", required_mode=0o600
        )
    except FileNotFoundError as error:
        raise ValueError("externally signed human evidence is required") from error
    package = SignedHumanEvidence.model_validate_json(data)
    if data != canonical_json(package.model_dump(mode="json")):
        raise ValueError("signed human evidence is not canonical")
    return package


def _persist_evidence(
    queue: FilesystemWorkerQueue, order_id: str, evidence: SignedHumanEvidence
) -> None:
    queue.control.storage.ensure_directory(Path("signed-evidence"))
    path = _evidence_path(order_id)
    data = canonical_json(evidence.model_dump(mode="json"))
    if not queue.control.storage.exists(path):
        queue.control.storage.write_file_noreplace(path, data, mode=0o600)
    if queue.control.storage.read_file(
        path, description="external signed human evidence", required_mode=0o600
    ) != data:
        raise ValueError("signed human evidence cannot be replaced")


def _evidence_path(order_id: str) -> Path:
    return Path("signed-evidence") / f"{order_id}.json"
