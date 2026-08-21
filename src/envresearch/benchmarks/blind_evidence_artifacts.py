"""Durable lifecycle boundary for externally signed human evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from envresearch.benchmarks.blind_artifact_support import artifact_ref
from envresearch.benchmarks.blind_authority import (
    SignedHumanEvidence,
    verify_human_evidence,
)
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_claims import CuratorSourceSheet
from envresearch.models.principal import PrincipalAssignment, PrincipalKind

if TYPE_CHECKING:
    from envresearch.benchmarks.blind_artifacts import BlindArtifactLifecycle


def persist_signed_evidence(
    artifacts: BlindArtifactLifecycle,
    case_id: str,
    evidence: SignedHumanEvidence,
    assignment: PrincipalAssignment,
    inputs: tuple[ArtifactRef, ...],
) -> ArtifactRef:
    """Persist the exact signed package under its authenticated human producer."""
    with artifacts.principals.registry.control.transaction_lock(
        "blind-case", case_id
    ):
        _require_enrollment(artifacts, case_id)
        kind = evidence.role
        durable = artifacts.principals.require_assignment(
            case_id, assignment, kind, evidence.slot
        )
        _verify_package(artifacts, case_id, evidence, durable)
        path = evidence_path(artifacts, case_id, evidence)
        return artifact_ref(
            artifacts.lifecycle.persist_structured(
                path, evidence, durable.producer, inputs
            )
        )


def require_signed_evidence(
    artifacts: BlindArtifactLifecycle,
    case_id: str,
    assignment: PrincipalAssignment,
    kind: PrincipalKind,
    slot: int,
    candidate: BaseModel,
    candidate_schema: str,
) -> ArtifactRef:
    """Resolve one current package and bind it to the artifact candidate."""
    _require_enrollment(artifacts, case_id)
    durable = artifacts.principals.require_assignment(
        case_id, assignment, kind, slot
    )
    path = _path_for(artifacts, case_id, kind, slot, candidate_schema)
    try:
        current = artifacts.lifecycle.read_artifact(path)
        evidence = SignedHumanEvidence.model_validate_json(json.dumps(current.payload))
        verified = artifacts.lifecycle.require_validated(
            path,
            producer=durable.producer,
            inputs=current.envelope.input_artifacts,
        )
    except FileNotFoundError as error:
        raise ValueError("current signed human evidence is required") from error
    _verify_package(artifacts, case_id, evidence, durable)
    if evidence.candidate_schema != candidate_schema:
        raise ValueError("signed human evidence schema mismatch")
    if evidence.candidate != candidate.model_dump(mode="json"):
        raise ValueError("signed human evidence candidate mismatch")
    ref = artifact_ref(verified)
    if artifacts.lifecycle.validated_history_ref(path) != ref:
        raise ValueError("signed human evidence is not current in validated history")
    return ref


def evidence_path(
    artifacts: BlindArtifactLifecycle,
    case_id: str,
    evidence: SignedHumanEvidence,
) -> Path:
    return _path_for(
        artifacts, case_id, evidence.role, evidence.slot, evidence.candidate_schema
    )


def _path_for(
    artifacts: BlindArtifactLifecycle,
    case_id: str,
    kind: PrincipalKind,
    slot: int,
    schema: str,
) -> Path:
    paths = artifacts.paths(case_id)
    if kind is PrincipalKind.EXPERT and slot == 1:
        return paths.expert_one_evidence
    if kind is PrincipalKind.EXPERT and slot == 2:
        return paths.expert_two_evidence
    if kind is PrincipalKind.ADJUDICATOR and slot == 1:
        return (
            paths.adjudication_evidence
            if schema == "envresearch.AdjudicationVerdict"
            else paths.third_score_evidence
        )
    raise ValueError("signed human evidence role or slot is invalid")


def _verify_package(
    artifacts: BlindArtifactLifecycle,
    case_id: str,
    evidence: SignedHumanEvidence,
    assignment: PrincipalAssignment,
) -> None:
    source = artifacts.lifecycle.read_payload(
        artifacts.paths(case_id).source_sheet, CuratorSourceSheet
    )
    participant = artifacts.principals.registry.enrolled_human_key(
        case_id, evidence.role, evidence.slot
    )
    verify_human_evidence(
        evidence,
        participant,
        case_id=case_id,
        role=evidence.role,
        slot=evidence.slot,
        source_generation=cast(int, source.source_generation),
        assignment_id=assignment.assignment_id,
        order_hash=evidence.order_hash,
        candidate_schema=evidence.candidate_schema,
    )


def _require_enrollment(artifacts: BlindArtifactLifecycle, case_id: str) -> None:
    from envresearch.benchmarks.blind_enrollment_marker import (
        require_frozen_enrollment,
    )

    require_frozen_enrollment(artifacts.principals.registry, case_id)
