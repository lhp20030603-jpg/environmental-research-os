"""Test-only external Ed25519 signers for blind human evidence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel

from envresearch.benchmarks.blind_authority import (
    AuthorityTrustAnchor,
    BlindEnrollmentPayload,
    EnrolledBlindCase,
    HumanKeyEnrollment,
    SignedBlindEnrollment,
    SignedHumanEvidence,
    canonical_json,
    encode_binary,
    enrollment_signing_bytes,
    evidence_signing_bytes,
)
from envresearch.benchmarks.blind_enrollment_controller import (
    profile_registry_sha256,
)
from envresearch.benchmarks.blind_trust_store import pin_authority_anchor
from envresearch.models.principal import PrincipalKind

if TYPE_CHECKING:
    from envresearch.benchmarks.blind_workflow import BlindEvaluationController
    from envresearch.workers.contracts import WorkOrder

_SIGNERS: WeakKeyDictionary[
    BlindEvaluationController, dict[tuple[PrincipalKind, int], Ed25519PrivateKey]
] = WeakKeyDictionary()
_RUN_SIGNERS: dict[
    str, dict[tuple[PrincipalKind, int], Ed25519PrivateKey]
] = {}


@dataclass(frozen=True)
class PreparedEnrollment:
    signed: SignedBlindEnrollment
    signers: dict[tuple[PrincipalKind, int], Ed25519PrivateKey]


def prepare_enrollment(
    controller: BlindEvaluationController,
) -> PreparedEnrollment:
    authority = Ed25519PrivateKey.generate()
    signers = {
        (PrincipalKind.EXPERT, 1): Ed25519PrivateKey.generate(),
        (PrincipalKind.EXPERT, 2): Ed25519PrivateKey.generate(),
        (PrincipalKind.ADJUDICATOR, 1): Ed25519PrivateKey.generate(),
    }
    participants = tuple(
        HumanKeyEnrollment(
            case_id=controller.case_id,
            role=role,
            slot=slot,
            principal_id=f"external-{role.value}-{slot}",
            key_id=f"key-{role.value}-{slot}",
            public_key=_public(private),
        )
        for (role, slot), private in signers.items()
    )
    anchor = AuthorityTrustAnchor(
        key_id="authority-test", public_key=_public(authority)
    )
    pin_authority_anchor(controller.registry, anchor)
    payload = BlindEnrollmentPayload(
        evaluation_id=f"evaluation-{controller.case_id}",
        authority_key_id="authority-test",
        frozen_at=datetime(2026, 8, 10, tzinfo=UTC),
        cases=(
            EnrolledBlindCase(
                case_id=controller.case_id,
                method_family=controller.loaded.source_sheet.method_family,
                cohort="pilot",
                source_generation=controller._source_generation(),
                descriptor_sha256=controller._descriptor_sha256,
                source_ref=controller.loaded.source_ref,
                claim_fact_map_ref=controller.loaded.claim_fact_map_ref,
                blinded_brief_ref=controller.loaded.brief_ref,
            ),
        ),
        participants=participants,
        profile_registry_sha256=profile_registry_sha256(),
        rubric_sha256=controller._rubric_sha256(),
        policy_sha256=controller._policy_sha256(),
    )
    signed = SignedBlindEnrollment(
        payload=payload,
        signature=encode_binary(authority.sign(enrollment_signing_bytes(payload))),
    )
    return PreparedEnrollment(signed=signed, signers=signers)


def install_enrollment(
    controller: BlindEvaluationController, prepared: PreparedEnrollment
) -> None:
    controller.enroll_participants(prepared.signed)
    _SIGNERS[controller] = prepared.signers
    _RUN_SIGNERS[str(controller.run_root)] = prepared.signers


def enroll_controller(controller: BlindEvaluationController) -> None:
    install_enrollment(controller, prepare_enrollment(controller))


def signed_candidate(
    controller: BlindEvaluationController,
    order: WorkOrder,
    payload: BaseModel,
    role: PrincipalKind,
    slot: int,
) -> SignedHumanEvidence:
    candidate = payload.model_dump(mode="json")
    assignment = order.principal_assignment
    assert assignment is not None and order.order_hash is not None
    unsigned = {
        "case_id": controller.case_id,
        "role": role,
        "slot": slot,
        "source_generation": controller._source_generation(),
        "assignment_id": assignment.assignment_id,
        "order_hash": order.order_hash,
        "candidate_schema": order.expected_output_schema,
        "candidate_sha256": hashlib.sha256(canonical_json(candidate)).hexdigest(),
        "key_id": assignment.key_id,
        "candidate": candidate,
        "signature": encode_binary(b"\0" * 64),
    }
    placeholder = SignedHumanEvidence.model_validate(unsigned)
    private = _SIGNERS.get(controller, _RUN_SIGNERS[str(controller.run_root)])[
        (role, slot)
    ]
    return placeholder.model_copy(
        update={"signature": encode_binary(private.sign(evidence_signing_bytes(placeholder)))}
    )


def _public(private: Ed25519PrivateKey) -> str:
    return encode_binary(
        private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    )
