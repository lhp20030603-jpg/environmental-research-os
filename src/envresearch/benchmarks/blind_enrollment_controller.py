"""Controller-side import of public authority enrollment state."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from envresearch.benchmarks.blind_authority import (
    SignedBlindEnrollment,
    VerifiedBlindEnrollment,
    verify_enrollment_signature,
)
from envresearch.benchmarks.blind_enrollment_marker import (
    enrollment_is_frozen,
    freeze_enrollment,
    require_frozen_enrollment,
)
from envresearch.benchmarks.blind_enrollment_prestate import (
    require_enrollment_prestate,
)
from envresearch.benchmarks.blind_enrollment_store import store_signed_enrollment
from envresearch.benchmarks.blind_trust_store import read_authority_anchor
from envresearch.research.order_policy import (
    blind_expert_rubric,
    blind_order_constraints,
    blind_profile_payload,
    canonical_blind_json,
)
from envresearch.workers.contracts import WorkerRole

if TYPE_CHECKING:
    from envresearch.research.order_issuance import BlindControllerInfrastructure

_METHOD_ROOT = Path(__file__).resolve().parents[3] / "packs/methods"


def enroll_participants(
    infrastructure: BlindControllerInfrastructure,
    signed: SignedBlindEnrollment,
) -> VerifiedBlindEnrollment:
    payload, digest = verify_enrollment_signature(
        signed, read_authority_anchor(infrastructure.registry)
    )
    verified = VerifiedBlindEnrollment(payload, digest)
    cases = tuple(
        item for item in verified.payload.cases
        if item.case_id == infrastructure.case_id
    )
    if len(cases) != 1:
        raise ValueError("controller case is missing from sealed enrollment")
    case = cases[0]
    if (
        case.source_generation != infrastructure._source_generation()
        or case.source_ref != infrastructure.loaded.source_ref
        or case.claim_fact_map_ref != infrastructure.loaded.claim_fact_map_ref
        or case.blinded_brief_ref != infrastructure.loaded.brief_ref
        or case.method_family != infrastructure.loaded.source_sheet.method_family
        or infrastructure._descriptor_sha256 is None
        or case.descriptor_sha256 != infrastructure._descriptor_sha256
    ):
        raise ValueError("participant enrollment does not match the frozen case")
    if (
        verified.payload.profile_registry_sha256
        != profile_registry_sha256()
        or verified.payload.rubric_sha256 != infrastructure._rubric_sha256()
        or verified.payload.policy_sha256 != infrastructure._policy_sha256()
    ):
        raise ValueError("participant enrollment policy digests do not match")
    control = infrastructure.registry.control
    with control.transaction_lock("blind-enrollment", infrastructure.case_id):
        if enrollment_is_frozen(infrastructure.registry, infrastructure.case_id):
            durable = require_frozen_enrollment(
                infrastructure.registry, infrastructure.case_id
            )
            if durable.signed_sha256 != verified.signed_sha256:
                raise ValueError("participant enrollment cannot be replaced")
            return durable
        base = Path("principals/benchmark") / infrastructure.case_id
        signed_exists = control.storage.exists(base / "signed-enrollment.json")
        human_exists = control.storage.exists(base / "human-keys.json")
        if human_exists and not signed_exists:
            raise ValueError("enrollment must be the first authenticated run transition")
        require_enrollment_prestate(infrastructure, allow_partial=signed_exists)
        store_signed_enrollment(
            infrastructure.registry, infrastructure.case_id, signed
        )
        infrastructure.registry.enroll_benchmark_humans(
            infrastructure.case_id, verified.payload.participants
        )
        freeze_enrollment(infrastructure.registry, infrastructure.case_id, verified)
        return require_frozen_enrollment(
            infrastructure.registry, infrastructure.case_id
        )


def profile_registry_sha256() -> str:
    return str(blind_profile_payload(_METHOD_ROOT)["registry_sha256"])


def rubric_sha256() -> str:
    return hashlib.sha256(canonical_blind_json(blind_expert_rubric())).hexdigest()


def policy_sha256() -> str:
    policy = {
        role.value: blind_order_constraints(role)
        for role in (
            WorkerRole.BENCHMARK_EXPERT,
            WorkerRole.BENCHMARK_ADJUDICATOR,
        )
    }
    return hashlib.sha256(canonical_blind_json(policy)).hexdigest()
