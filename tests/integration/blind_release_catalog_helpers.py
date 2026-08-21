"""Build one fully authenticated held-out catalog for release integration tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel
from test_blind_registry_security import write_case
from test_blind_workflow import _raw_score_for, valid_recommendation

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
from envresearch.benchmarks.blind_release import CANONICAL_METHOD_FAMILIES
from envresearch.benchmarks.blind_trust_store import pin_authority_anchor
from envresearch.benchmarks.blind_workflow import BlindEvaluationController
from envresearch.models.benchmark_evaluation import PosthocComparison
from envresearch.models.principal import PrincipalKind
from envresearch.workers.contracts import WorkOrder


def build_releasable_catalog(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Persist sixteen held-out cases sealed by one external authority."""
    catalog = tmp_path / "catalog"
    run = tmp_path / "run"
    controllers = tuple(
        BlindEvaluationController.from_case(
            write_case(
                catalog / case_id,
                case_id=case_id,
                method_family=family,
            ),
            run / case_id,
        )
        for family in sorted(CANONICAL_METHOD_FAMILIES)
        for case_id in (
            f"heldout-{family.replace('_', '-')}-1",
            f"heldout-{family.replace('_', '-')}-2",
        )
    )
    authority = Ed25519PrivateKey.generate()
    anchor = AuthorityTrustAnchor(
        key_id="release-authority", public_key=_public(authority)
    )
    signers = {
        (controller.case_id, role, slot): Ed25519PrivateKey.generate()
        for controller in controllers
        for role, slot in _HUMAN_SLOTS
    }
    participants = tuple(
        HumanKeyEnrollment(
            case_id=case_id,
            role=role,
            slot=slot,
            principal_id=f"external-{case_id}-{role.value}-{slot}",
            key_id=f"key-{case_id}-{role.value}-{slot}",
            public_key=_public(private),
        )
        for (case_id, role, slot), private in signers.items()
    )
    payload = BlindEnrollmentPayload(
        evaluation_id="held-out-release-evaluation",
        authority_key_id=anchor.key_id,
        frozen_at=datetime(2026, 8, 10, tzinfo=UTC),
        cases=tuple(
            EnrolledBlindCase(
                case_id=controller.case_id,
                method_family=controller.loaded.source_sheet.method_family,
                cohort="held_out",
                source_generation=controller._source_generation(),
                descriptor_sha256=controller._descriptor_sha256 or "",
                source_ref=controller.loaded.source_ref,
                claim_fact_map_ref=controller.loaded.claim_fact_map_ref,
                blinded_brief_ref=controller.loaded.brief_ref,
            )
            for controller in controllers
        ),
        participants=participants,
        profile_registry_sha256=profile_registry_sha256(),
        rubric_sha256=controllers[0]._rubric_sha256(),
        policy_sha256=controllers[0]._policy_sha256(),
    )
    signed = SignedBlindEnrollment(
        payload=payload,
        signature=encode_binary(authority.sign(enrollment_signing_bytes(payload))),
    )
    try:
        for controller in controllers:
            pin_authority_anchor(controller.registry, anchor)
            controller.enroll_participants(signed)
            _complete_case(controller, signers)
    finally:
        for controller in controllers:
            controller.queue.close()
            for queue in controller.expert_queues.values():
                queue.close()
    configured = tmp_path / "owner-config/release-authority.json"
    configured.parent.mkdir()
    configured.write_bytes(canonical_json(anchor.model_dump(mode="json")))
    configured.chmod(0o600)
    return catalog, run, configured


_HUMAN_SLOTS = (
    (PrincipalKind.EXPERT, 1),
    (PrincipalKind.EXPERT, 2),
    (PrincipalKind.ADJUDICATOR, 1),
)


def _complete_case(
    controller: BlindEvaluationController,
    signers: dict[tuple[str, PrincipalKind, int], Ed25519PrivateKey],
) -> None:
    controller.replay_calibration()
    controller.accept_recommendation(valid_recommendation(controller))
    controller.issue_expert_orders()
    for slot in (1, 2):
        order = controller.expert_queues[slot].read_order(f"expert-score-{slot}")
        candidate = _raw_score_for(controller, slot)
        controller.accept_expert_score(
            slot,
            _signed_candidate(
                controller,
                order,
                candidate,
                PrincipalKind.EXPERT,
                slot,
                signers[(controller.case_id, PrincipalKind.EXPERT, slot)],
            ),
        )
    adjudicator = controller._human(PrincipalKind.ADJUDICATOR, 1)
    controller.artifacts.publish_posthoc(
        controller.case_id,
        PosthocComparison(
            recommendation_ref=controller.artifacts.ref(
                controller.case_id, "recommendation"
            ),
            realized_method_profile_ref="canonical-profile-v1",
            comparison={"classification": "defensible-alternative"},
            analyst_principal=adjudicator.principal_id,
        ),
        adjudicator,
    )


def _signed_candidate(
    controller: BlindEvaluationController,
    order: WorkOrder,
    payload: BaseModel,
    role: PrincipalKind,
    slot: int,
    private: Ed25519PrivateKey,
) -> SignedHumanEvidence:
    candidate = payload.model_dump(mode="json")
    assignment = order.principal_assignment
    assert assignment is not None and order.order_hash is not None
    placeholder = SignedHumanEvidence(
        case_id=controller.case_id,
        role=role,
        slot=slot,
        source_generation=controller._source_generation(),
        assignment_id=assignment.assignment_id,
        order_hash=order.order_hash,
        candidate_schema=order.expected_output_schema,
        candidate_sha256=hashlib.sha256(canonical_json(candidate)).hexdigest(),
        key_id=assignment.key_id or "",
        candidate=candidate,
        signature=encode_binary(b"\0" * 64),
    )
    return placeholder.model_copy(
        update={
            "signature": encode_binary(
                private.sign(evidence_signing_bytes(placeholder))
            )
        }
    )


def _public(private: Ed25519PrivateKey) -> str:
    return encode_binary(
        private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )
