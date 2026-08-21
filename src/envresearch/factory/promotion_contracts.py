"""Strict contracts for independent promotion of one exact factory run."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any, Literal, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from envresearch.factory.contracts import ResearchFactoryRun
from envresearch.factory.errors import FactoryError
from envresearch.kernel.gates import GateDecision
from envresearch.models.artifact import ArtifactRef
from envresearch.models.principal import (
    PrincipalAssignment,
    PrincipalKind,
    PrincipalVerification,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTEXT_ID = re.compile(r"^factory-promotion-context-[0-9a-f]{64}$")
_PROMOTION_ID = re.compile(r"^factory-run-promotion-[0-9a-f]{64}$")
_STRICT = ConfigDict(
    extra="forbid", frozen=True, strict=True, revalidate_instances="always"
)

FACTORY_PROMOTION_CHECKLIST = (
    "exact-run-authority-reopened",
    "retrospective-coherence-reviewed",
    "open-limitations-reviewed",
    "individual-run-scope-only",
    "independent-human-decision-required",
    "hidden-evaluation-not-run",
    "scientific-release-pending",
)


def _strict_model(model: type[BaseModel]) -> Any:
    def revalidate(value: object) -> BaseModel:
        if isinstance(value, model):
            return model.model_validate_json(value.model_dump_json())
        return model.model_validate_json(
            json.dumps(value, default=str, separators=(",", ":"))
        )

    return BeforeValidator(revalidate)


StrictArtifactRef = Annotated[ArtifactRef, _strict_model(ArtifactRef)]
StrictRun = ResearchFactoryRun
StrictDecision = Annotated[GateDecision, _strict_model(GateDecision)]
StrictPrincipal = Annotated[PrincipalAssignment, _strict_model(PrincipalAssignment)]


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _ref_payload_matches(
    reference: ArtifactRef, artifact_id: str, payload: BaseModel
) -> bool:
    return (
        reference.artifact_id == artifact_id
        and reference.artifact_version == 1
        and reference.content_hash
        == hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    )


def promotion_context_id(run_ref: ArtifactRef, generation: int) -> str:
    """Derive one generation identity from the complete exact run reference."""
    reference = ArtifactRef.model_validate(run_ref.model_dump(mode="python"))
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 1
    ):
        raise ValueError("promotion context generation must be positive")
    digest = hashlib.sha256(
        _canonical(
            {
                "generation": generation,
                "run_ref": reference.model_dump(mode="json"),
            }
        )
    ).hexdigest()
    return f"factory-promotion-context-{digest}"


class FactoryPromotionContext(BaseModel):
    """Deterministic, timestamp-free human review context for one exact run."""

    model_config = _STRICT

    schema_version: Literal["factory.promotion-context.v1"]
    context_id: str
    producer: Literal["research-factory-promotion-context-v1"]
    generation: int = Field(ge=1)
    run_ref: StrictArtifactRef
    run: StrictRun
    decision_kind: Literal["individual-run-release"]
    requested_by: str
    limitations: tuple[str, ...] = Field(min_length=1)
    checklist: tuple[str, ...] = Field(min_length=1)
    hidden_evaluation_status: Literal["not-run"]
    product_release_status: Literal["scientific_release_pending"]

    @field_validator("context_id")
    @classmethod
    def require_context_id(cls, value: str) -> str:
        if not _CONTEXT_ID.fullmatch(value):
            raise ValueError("promotion context ID must contain one full SHA-256")
        return value

    @field_validator("requested_by")
    @classmethod
    def require_requester(cls, value: str) -> str:
        canonical = value.strip().casefold()
        if not canonical or canonical != value:
            raise ValueError("promotion requester must be one canonical principal")
        return canonical

    @field_validator("checklist")
    @classmethod
    def require_derived_checklist(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != FACTORY_PROMOTION_CHECKLIST:
            raise ValueError("promotion checklist must be service-derived")
        return value

    @model_validator(mode="after")
    def require_exact_run_context(self) -> FactoryPromotionContext:
        if not _ref_payload_matches(self.run_ref, self.run.factory_run_id, self.run):
            raise ValueError(
                "promotion run reference does not bind canonical run bytes"
            )
        if self.context_id != promotion_context_id(self.run_ref, self.generation):
            raise ValueError("promotion context ID does not bind run generation")
        if self.limitations != self.run.binding_report.limitations:
            raise ValueError("promotion limitations differ from the exact run")
        return self


def derive_promotion_context(
    run_ref: ArtifactRef,
    run: ResearchFactoryRun,
    requested_by: str,
    generation: int,
) -> FactoryPromotionContext:
    """Build the one canonical service-owned review context."""
    return FactoryPromotionContext(
        schema_version="factory.promotion-context.v1",
        context_id=promotion_context_id(run_ref, generation),
        producer="research-factory-promotion-context-v1",
        generation=generation,
        run_ref=run_ref,
        run=run,
        decision_kind="individual-run-release",
        requested_by=requested_by,
        limitations=run.binding_report.limitations,
        checklist=FACTORY_PROMOTION_CHECKLIST,
        hidden_evaluation_status="not-run",
        product_release_status="scientific_release_pending",
    )


def factory_promotion_id(
    context_ref: ArtifactRef,
    decision: GateDecision,
    capability_sha256: str,
    principal: PrincipalAssignment,
) -> str:
    """Bind promotion identity to context, decision, secret digest, and principal."""
    reference = ArtifactRef.model_validate(context_ref.model_dump(mode="python"))
    durable_decision = GateDecision.model_validate_json(decision.model_dump_json())
    durable_principal = PrincipalAssignment.model_validate_json(
        principal.model_dump_json()
    )
    if not _SHA256.fullmatch(capability_sha256):
        raise ValueError("principal capability digest must be a full SHA-256")
    digest = hashlib.sha256(
        _canonical(
            {
                "context_ref": reference.model_dump(mode="json"),
                "decision": durable_decision.model_dump(mode="json"),
                "principal_capability_sha256": capability_sha256,
                "authenticated_principal": durable_principal.model_dump(mode="json"),
            }
        )
    ).hexdigest()
    return f"factory-run-promotion-{digest}"


class FactoryRunPromotion(BaseModel):
    """One exact terminal human decision with authenticated principal evidence."""

    model_config = _STRICT

    schema_version: Literal["factory.run-promotion.v1"]
    promotion_id: str
    producer: Literal["research-factory-promotion-v1"]
    context_ref: StrictArtifactRef
    context: FactoryPromotionContext
    decision: StrictDecision
    principal_capability_sha256: str
    authenticated_principal: StrictPrincipal
    promotion_scope: Literal["individual-run-only"]
    hidden_evaluation_status: Literal["not-run"]
    product_release_status: Literal["scientific_release_pending"]

    @field_validator("promotion_id")
    @classmethod
    def require_promotion_id(cls, value: str) -> str:
        if not _PROMOTION_ID.fullmatch(value):
            raise ValueError("promotion ID must contain one full SHA-256")
        return value

    @field_validator("principal_capability_sha256")
    @classmethod
    def require_capability_digest(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("principal capability digest must be a full SHA-256")
        return value

    @model_validator(mode="after")
    def require_exact_independent_decision(self) -> FactoryRunPromotion:
        principal = self.authenticated_principal
        if not _ref_payload_matches(
            self.context_ref, self.context.context_id, self.context
        ):
            raise ValueError("promotion context reference is not exact")
        if self.decision.decided_by == self.context.requested_by:
            raise ValueError("promotion requires an independent decision principal")
        if (
            principal.kind is not PrincipalKind.GATE
            or principal.verification is not PrincipalVerification.OWNER_CONTROL
            or principal.principal_id != self.decision.decided_by
        ):
            raise ValueError("promotion decision lacks authenticated human evidence")
        _require_narrow_conditions(self.context, self.decision)
        expected = factory_promotion_id(
            self.context_ref,
            self.decision,
            self.principal_capability_sha256,
            principal,
        )
        if self.promotion_id != expected:
            raise ValueError("promotion ID does not bind exact decision authority")
        return self


def _require_narrow_conditions(
    context: FactoryPromotionContext, decision: GateDecision
) -> None:
    allowed = {"acknowledged_limitations", "additional_limitations", "use_restrictions"}
    if not set(decision.conditions).issubset(allowed):
        raise ValueError("promotion decision conditions broaden scientific scope")
    acknowledged = decision.conditions.get("acknowledged_limitations")
    if acknowledged is not None and (
        not isinstance(acknowledged, list) or tuple(acknowledged) != context.limitations
    ):
        raise ValueError("promotion decision conditions broaden scientific scope")
    for key in ("additional_limitations", "use_restrictions"):
        value = decision.conditions.get(key)
        invalid = not isinstance(value, list) or not value
        if value is not None and not invalid:
            entries = cast(list[str], value)
            invalid = any(
                not isinstance(item, str) or not item or item != item.strip()
                for item in entries
            ) or entries != sorted(set(entries))
        if value is not None and invalid:
            raise ValueError("promotion decision conditions must only narrow scope")


class FactoryPromotionRequired(FactoryError):
    """Raised when a valid exact run still needs a human decision."""

    code = "FACTORY_PROMOTION_REQUIRED"


class FactoryPromotionRejected(FactoryError):
    """Raised when a human has terminally rejected one exact context."""

    code = "FACTORY_PROMOTION_REJECTED"


__all__ = [
    "FACTORY_PROMOTION_CHECKLIST",
    "FactoryPromotionContext",
    "FactoryPromotionRejected",
    "FactoryPromotionRequired",
    "FactoryRunPromotion",
    "derive_promotion_context",
    "factory_promotion_id",
    "promotion_context_id",
]
