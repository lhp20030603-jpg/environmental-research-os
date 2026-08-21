"""Strict contracts for independent promotion of one exact factory run."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from test_factory_run_contracts import _run

from envresearch.factory.promotion_contracts import (
    FACTORY_PROMOTION_CHECKLIST,
    FactoryPromotionContext,
    FactoryPromotionRejected,
    FactoryPromotionRequired,
    FactoryRunPromotion,
    factory_promotion_id,
    promotion_context_id,
)
from envresearch.kernel.gates import GateDecision
from envresearch.models.artifact import ArtifactRef
from envresearch.models.enums import GateStatus
from envresearch.models.principal import (
    PrincipalAssignment,
    PrincipalKind,
    PrincipalVerification,
)
from envresearch.research.artifact_lifecycle_support import producer_identity


def _run_ref():
    run = _run()
    return ArtifactRef(
        artifact_id=run.factory_run_id,
        artifact_version=1,
        content_hash=hashlib.sha256(run.model_dump_json().encode()).hexdigest(),
    )


def _context() -> FactoryPromotionContext:
    run = _run()
    run_ref = _run_ref()
    return FactoryPromotionContext(
        schema_version="factory.promotion-context.v1",
        context_id=promotion_context_id(run_ref, 1),
        producer="research-factory-promotion-context-v1",
        generation=1,
        run_ref=run_ref,
        run=run,
        decision_kind="individual-run-release",
        requested_by="factory-agent",
        limitations=run.binding_report.limitations,
        checklist=FACTORY_PROMOTION_CHECKLIST,
        hidden_evaluation_status="not-run",
        product_release_status="scientific_release_pending",
    )


def _principal() -> PrincipalAssignment:
    return PrincipalAssignment(
        assignment_id="assignment-human-gate",
        principal_id="human-reviewer",
        kind=PrincipalKind.GATE,
        producer=producer_identity("human-control").model_copy(
            update={"context_id": "context-human-gate"}
        ),
        verification=PrincipalVerification.OWNER_CONTROL,
    )


def _decision(
    *, decided_by: str = "human-reviewer", conditions: dict[str, object] | None = None
) -> GateDecision:
    return GateDecision(
        status=GateStatus.APPROVED,
        decided_by=decided_by,
        rationale="The exact run is approved for individual use.",
        conditions=conditions or {},
        decided_at=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
    )


def _promotion() -> FactoryRunPromotion:
    context = _context()
    context_ref = ArtifactRef(
        artifact_id=context.context_id,
        artifact_version=1,
        content_hash=hashlib.sha256(context.model_dump_json().encode()).hexdigest(),
    )
    decision = _decision()
    principal = _principal()
    capability_sha256 = "a" * 64
    return FactoryRunPromotion(
        schema_version="factory.run-promotion.v1",
        promotion_id=factory_promotion_id(
            context_ref, decision, capability_sha256, principal
        ),
        producer="research-factory-promotion-v1",
        context_ref=context_ref,
        context=context,
        decision=decision,
        principal_capability_sha256=capability_sha256,
        authenticated_principal=principal,
        promotion_scope="individual-run-only",
        hidden_evaluation_status="not-run",
        product_release_status="scientific_release_pending",
    )


def test_context_is_service_derived_exact_and_contains_no_request_time() -> None:
    """Catch caller-written checklist/scope or operational time entering the object."""
    context = _context()

    assert "requested_at" not in FactoryPromotionContext.model_fields
    assert "timestamp" not in context.model_dump_json()
    with pytest.raises(ValidationError, match="checklist"):
        FactoryPromotionContext.model_validate(
            context.model_dump() | {"checklist": context.checklist[:-1]}
        )
    with pytest.raises(ValidationError):
        FactoryPromotionContext.model_validate(
            context.model_dump() | {"hidden_evaluation_status": "passed"}
        )
    with pytest.raises(ValidationError):
        FactoryPromotionContext.model_validate(
            context.model_dump() | {"product_release_status": "released"}
        )


def test_context_binds_exact_run_generation_and_complete_limitations() -> None:
    """Catch a context detached from its run bytes or omitting an open limitation."""
    context = _context()

    with pytest.raises(ValidationError, match="context ID"):
        FactoryPromotionContext.model_validate(context.model_dump() | {"generation": 2})
    with pytest.raises(ValidationError, match="run reference"):
        FactoryPromotionContext.model_validate(
            context.model_dump()
            | {"run_ref": context.run_ref.model_copy(update={"content_hash": "0" * 64})}
        )
    with pytest.raises(ValidationError, match="limitations"):
        FactoryPromotionContext.model_validate(
            context.model_dump() | {"limitations": context.limitations[:-1]}
        )


def test_promotion_revalidates_decision_and_requires_independent_principal() -> None:
    """Catch forged model instances and request/decision principal collisions."""
    promotion = _promotion()
    forged = GateDecision.model_construct(
        status=GateStatus.PENDING,
        decided_by="human-reviewer",
        rationale="forged",
        conditions={},
        decided_at=promotion.decision.decided_at,
    )

    with pytest.raises(ValidationError):
        FactoryRunPromotion.model_validate(
            promotion.model_dump() | {"decision": forged}
        )
    with pytest.raises(ValidationError, match="independent"):
        FactoryRunPromotion.model_validate(
            promotion.model_dump() | {"decision": _decision(decided_by="factory-agent")}
        )


def test_promotion_binds_exact_context_capability_and_human_evidence() -> None:
    """Catch detached context bytes, truncated secrets, or product-level claims."""
    promotion = _promotion()

    with pytest.raises(ValidationError, match="context reference"):
        FactoryRunPromotion.model_validate(
            promotion.model_dump()
            | {
                "context_ref": promotion.context_ref.model_copy(
                    update={"content_hash": "0" * 64}
                )
            }
        )
    with pytest.raises(ValidationError, match="capability"):
        FactoryRunPromotion.model_validate(
            promotion.model_dump() | {"principal_capability_sha256": "a" * 32}
        )
    with pytest.raises(ValidationError):
        FactoryRunPromotion.model_validate(
            promotion.model_dump() | {"promotion_scope": "factory-product"}
        )


def test_promotion_errors_expose_stable_public_codes() -> None:
    """Catch CLI-facing promotion outcomes collapsing into generic errors."""
    required = FactoryPromotionRequired("decision missing", finding_kind="promotion")
    rejected = FactoryPromotionRejected("decision rejected", finding_kind="promotion")

    assert required.code == "FACTORY_PROMOTION_REQUIRED"
    assert rejected.code == "FACTORY_PROMOTION_REJECTED"


@pytest.mark.parametrize(
    "values",
    (
        [" padded"],
        ["duplicate", "duplicate"],
        ["z-restriction", "a-restriction"],
    ),
)
def test_restriction_lists_require_unique_canonical_order(values: list[str]) -> None:
    """Catch equivalent restriction bytes receiving distinct promotion identities."""
    promotion = _promotion()
    decision = _decision(conditions={"use_restrictions": values})
    candidate = promotion.model_dump() | {"decision": decision}
    candidate["promotion_id"] = factory_promotion_id(
        promotion.context_ref,
        decision,
        promotion.principal_capability_sha256,
        promotion.authenticated_principal,
    )

    with pytest.raises(ValidationError, match="narrow scope"):
        FactoryRunPromotion.model_validate(candidate)


def test_canonical_restrictions_have_stable_bytes_and_identity() -> None:
    """Prove one canonical list round-trips without normalization or ID drift."""
    promotion = _promotion()
    decision = _decision(
        conditions={"use_restrictions": ["analysis-only", "no-redistribution"]}
    )
    candidate = promotion.model_dump() | {"decision": decision}
    candidate["promotion_id"] = factory_promotion_id(
        promotion.context_ref,
        decision,
        promotion.principal_capability_sha256,
        promotion.authenticated_principal,
    )
    first = FactoryRunPromotion.model_validate(candidate)
    second = FactoryRunPromotion.model_validate_json(first.model_dump_json())

    assert second.model_dump_json() == first.model_dump_json()
    assert second.promotion_id == first.promotion_id
