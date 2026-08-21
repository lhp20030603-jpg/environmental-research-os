"""Strict role projections for advisory Personal review bundles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, TypeAlias, cast

from pydantic import BaseModel, BeforeValidator, Field, ValidationError, model_validator

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation._strict import (
    STRICT,
    StrictArtifactRef,
    artifact_ref_key,
    canonical_json,
    materialize_id,
    require_materialized_id,
    require_nonblank,
    require_sorted_unique_refs,
    strict_model_input,
)
from envresearch.personal_validation.contracts import (
    CompletedFactoryRunTarget,
    PersonalCanonicalCase,
    PersonalValidationAttempt,
    PersonalValidationProtocol,
)
from envresearch.personal_validation.errors import PersonalValidationIntegrityInvalid
from envresearch.personal_validation.evaluation import (
    attempt_authority,
    attempt_session,
)
from envresearch.personal_validation.events import PersonalValidationEvent
from envresearch.personal_validation.private_store import PersonalValidationStore
from envresearch.personal_validation.report import (
    LifecycleService,
    authority_error,
    model_ref,
    publish_event_object,
)
from envresearch.personal_validation.review_closure import require_bundle_closure_safe
from envresearch.personal_validation.review_contracts import (
    AgentReviewResponse,
    ReviewAssignment,
    ReviewPublication,
)

Role = Literal["scientific", "evidence", "synthesis"]


@dataclass(frozen=True, slots=True)
class PreparedBundle:
    bundle_ref: ArtifactRef
    bundle: ReviewBundle
    completion_event_id: str


class ScientificProjection(BaseModel):
    model_config = STRICT
    projection_type: Literal["scientific"]
    estimand_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    identification_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    compatibility_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    assumption_threat_refs: tuple[StrictArtifactRef, ...]
    diagnostic_refs: tuple[StrictArtifactRef, ...]

    @model_validator(mode="after")
    def require_canonical_refs(self) -> ScientificProjection:
        _require_projection_ref_order(self)
        return self


class EvidenceProjection(BaseModel):
    model_config = STRICT
    projection_type: Literal["evidence"]
    lineage_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    numeric_output_refs: tuple[StrictArtifactRef, ...]
    citation_refs: tuple[StrictArtifactRef, ...]
    reproducibility_refs: tuple[StrictArtifactRef, ...]
    claim_strength_refs: tuple[StrictArtifactRef, ...]

    @model_validator(mode="after")
    def require_canonical_refs(self) -> EvidenceProjection:
        _require_projection_ref_order(self)
        return self


class SynthesisProjection(BaseModel):
    model_config = STRICT
    projection_type: Literal["synthesis"]
    scientific_publication_ref: StrictArtifactRef
    evidence_publication_ref: StrictArtifactRef

    @model_validator(mode="after")
    def require_distinct_publications(self) -> SynthesisProjection:
        if self.scientific_publication_ref == self.evidence_publication_ref:
            raise ValueError("synthesis projection requires distinct publications")
        return self


BundleProjection: TypeAlias = Annotated[
    ScientificProjection | EvidenceProjection | SynthesisProjection,
    BeforeValidator(strict_model_input),
    Field(discriminator="projection_type"),
]


class ReviewBundle(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.review-bundle.v1"]
    bundle_id: str
    attempt_ref: StrictArtifactRef
    role: Literal["scientific", "evidence", "synthesis"]
    behavioral_contract_ref: StrictArtifactRef
    target_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    evidence_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    projection: BundleProjection
    primary_publication_refs: tuple[StrictArtifactRef, ...] = ()
    projection_policy_sha256: str

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"bundle_id"})

    @model_validator(mode="after")
    def require_role_projection_and_identity(self) -> ReviewBundle:
        require_nonblank(self.projection_policy_sha256)
        require_sorted_unique_refs(self.target_refs, field="bundle target refs")
        require_sorted_unique_refs(self.evidence_refs, field="bundle evidence refs")
        require_sorted_unique_refs(
            self.primary_publication_refs, field="bundle primary publication refs"
        )
        if self.role != self.projection.projection_type:
            raise ValueError("bundle role differs from its projection")
        expected = 2 if self.role == "synthesis" else 0
        if len(self.primary_publication_refs) != expected:
            raise ValueError("bundle primary publications disagree with its role")
        if self.role == "synthesis":
            projection = self.projection
            if not isinstance(projection, SynthesisProjection):
                raise ValueError("synthesis bundle projection is invalid")
            if set(self.primary_publication_refs) != {
                projection.scientific_publication_ref,
                projection.evidence_publication_ref,
            } or any(
                not item.artifact_id.startswith("personal-review-publication-")
                for item in self.primary_publication_refs
            ):
                raise ValueError("synthesis bundle lacks canonical publications")
        require_materialized_id(
            self.bundle_id, "personal-review-bundle-", self.identity_payload()
        )
        return self


def materialize_bundle(
    *,
    attempt_ref: ArtifactRef,
    role: Literal["scientific", "evidence", "synthesis"],
    behavioral_contract_ref: ArtifactRef,
    target_refs: tuple[ArtifactRef, ...],
    evidence_refs: tuple[ArtifactRef, ...],
    projection: ScientificProjection | EvidenceProjection | SynthesisProjection,
    primary_publication_refs: tuple[ArtifactRef, ...],
    projection_policy_sha256: str,
) -> ReviewBundle:
    payload: dict[str, object] = {
        "schema_version": "personal.review-bundle.v1",
        "attempt_ref": attempt_ref,
        "role": role,
        "behavioral_contract_ref": behavioral_contract_ref,
        "target_refs": tuple(sorted(target_refs, key=artifact_ref_key)),
        "evidence_refs": tuple(sorted(evidence_refs, key=artifact_ref_key)),
        "projection": projection,
        "primary_publication_refs": tuple(
            sorted(primary_publication_refs, key=artifact_ref_key)
        ),
        "projection_policy_sha256": projection_policy_sha256,
    }
    payload["bundle_id"] = materialize_id("personal-review-bundle-", payload)
    return ReviewBundle.model_validate(payload)


def projection_refs(
    projection: ScientificProjection | EvidenceProjection | SynthesisProjection,
) -> tuple[ArtifactRef, ...]:
    found: set[ArtifactRef] = set()
    _collect_refs(projection, found)
    return tuple(sorted(found, key=artifact_ref_key))


def _collect_refs(value: object, found: set[ArtifactRef]) -> None:
    if isinstance(value, ArtifactRef):
        found.add(value)
    elif isinstance(value, BaseModel):
        for _name, child in value:
            _collect_refs(child, found)
    elif isinstance(value, dict):
        for child in value.values():
            _collect_refs(child, found)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _collect_refs(child, found)


def _require_projection_ref_order(model: BaseModel) -> None:
    for name, value in model:
        if name.endswith("_refs"):
            require_sorted_unique_refs(value, field=name.replace("_", " "))


class BundleLifecycleMixin:
    def prepare_bundle(
        self: LifecycleService,
        attempt_ref: ArtifactRef,
        *,
        role: Role,
        primary_publication_refs: tuple[ArtifactRef, ...] = (),
    ) -> PreparedBundle:
        session_id = attempt_session(self.store, attempt_ref)
        with self.store.session_lock(session_id):
            history, attempt, case, protocol = attempt_authority(
                self.store, session_id, attempt_ref
            )
            projection, targets, evidence = _projection(
                self.store, attempt, case, role, primary_publication_refs
            )
            bundle = materialize_bundle(
                attempt_ref=attempt_ref,
                role=role,
                behavioral_contract_ref=case.reviewer_contract_ref,
                target_refs=targets,
                evidence_refs=evidence,
                projection=projection,
                primary_publication_refs=primary_publication_refs,
                projection_policy_sha256=policy_for(protocol, role),
            )
            require_bundle_closure_safe(self.store, case, attempt, bundle)
            event_id = publish_event_object(
                self,
                history,
                session_id,
                "bundle-published",
                bundle.bundle_id,
                bundle,
                slot=lambda item: item.attempt_ref == attempt_ref and item.role == role,
                boundary="bundle",
            )
            return PreparedBundle(model_ref(bundle.bundle_id, bundle), bundle, event_id)


def policy_for(protocol: PersonalValidationProtocol, role: Role) -> str:
    return cast(str, getattr(protocol, f"{role}_policy_sha256"))


def reopen_bundle(
    store: PersonalValidationStore,
    events: tuple[PersonalValidationEvent, ...],
    attempt_ref: ArtifactRef,
    bundle_ref: ArtifactRef,
    role: Role,
) -> ReviewBundle:
    bundle = store.load(bundle_ref, ReviewBundle)
    matches = tuple(
        event
        for event in events
        if event.operation == "bundle-published" and event.object_ref == bundle_ref
    )
    if bundle.attempt_ref != attempt_ref or bundle.role != role or len(matches) != 1:
        raise authority_error("review bundle role or attempt authority is invalid")
    return bundle


def parse_response(raw: bytes) -> AgentReviewResponse:
    try:
        parsed = AgentReviewResponse.model_validate_json(raw, strict=False)
        response = AgentReviewResponse.model_validate(
            parsed.model_dump(mode="python", round_trip=True), strict=True
        )
        if raw != canonical_json(response.model_dump(mode="json")):
            raise ValueError("agent response bytes are noncanonical")
        return response
    except (TypeError, ValueError, ValidationError) as error:
        raise PersonalValidationIntegrityInvalid(
            "agent response bytes are noncanonical",
            finding_kind="review-response-noncanonical",
        ) from error


def _projection(
    store: PersonalValidationStore,
    attempt: PersonalValidationAttempt,
    case: PersonalCanonicalCase,
    role: Role,
    primary: tuple[ArtifactRef, ...],
) -> tuple[
    ScientificProjection | EvidenceProjection | SynthesisProjection,
    tuple[ArtifactRef, ...],
    tuple[ArtifactRef, ...],
]:
    if role == "synthesis":
        scientific_ref, evidence_ref = _primary_publications(store, primary, attempt)
        projection = SynthesisProjection(
            projection_type="synthesis",
            scientific_publication_ref=scientific_ref,
            evidence_publication_ref=evidence_ref,
        )
        refs = tuple(sorted(primary, key=artifact_ref_key))
        return projection, refs, refs
    target = (
        attempt.target.run_ref
        if isinstance(attempt.target, CompletedFactoryRunTarget)
        else attempt.target.inspection_ref
    )
    common = tuple(
        sorted(
            {
                attempt.system_snapshot_ref,
                attempt.attempt_inventory_ref,
                target,
            },
            key=artifact_ref_key,
        )
    )
    primary_projection: ScientificProjection | EvidenceProjection
    completed = isinstance(attempt.target, CompletedFactoryRunTarget)
    if role == "scientific":
        primary_projection = ScientificProjection(
            projection_type="scientific",
            estimand_refs=(target,),
            identification_refs=(target,),
            compatibility_refs=(target,)
            if completed
            else (attempt.attempt_inventory_ref,),
            assumption_threat_refs=(target,) if completed else (),
            diagnostic_refs=(target,) if completed else (attempt.system_snapshot_ref,),
        )
    else:
        primary_projection = EvidenceProjection(
            projection_type="evidence",
            lineage_refs=(target,) if completed else common,
            numeric_output_refs=(target,) if completed else (),
            citation_refs=(target,) if completed else (),
            reproducibility_refs=(target,)
            if completed
            else tuple(
                sorted(
                    {attempt.system_snapshot_ref, attempt.attempt_inventory_ref},
                    key=artifact_ref_key,
                )
            ),
            claim_strength_refs=(target,),
        )
    evidence_refs = tuple(
        sorted(
            {
                *common,
                *projection_refs(primary_projection),
                case.reviewer_contract_ref,
            },
            key=artifact_ref_key,
        )
    )
    return primary_projection, (target,), evidence_refs


def _primary_publications(
    store: PersonalValidationStore,
    refs: tuple[ArtifactRef, ...],
    attempt: PersonalValidationAttempt,
) -> tuple[ArtifactRef, ArtifactRef]:
    if len(refs) != 2 or len(set(refs)) != 2:
        raise authority_error("synthesis requires two primary publications")
    by_role: dict[str, ArtifactRef] = {}
    events = store.read_events()
    for reference in refs:
        publication = store.load(reference, ReviewPublication)
        assignment = store.load(publication.assignment_ref, ReviewAssignment)
        published = any(
            event.operation == "review-published" and event.object_ref == reference
            for event in events
        )
        if (
            assignment.attempt_ref != model_ref(attempt.attempt_id, attempt)
            or assignment.role not in {"scientific", "evidence"}
            or not published
        ):
            raise authority_error("synthesis primary publication is invalid")
        by_role[assignment.role] = reference
    if set(by_role) != {"scientific", "evidence"}:
        raise authority_error("synthesis primary roles are incomplete")
    return by_role["scientific"], by_role["evidence"]
