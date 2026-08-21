"""Strict timestamp-free contracts for one governed research-factory run."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from envresearch.factory.design_contracts import ApprovedDesignHandoff
from envresearch.models.artifact import ArtifactRef
from envresearch.paper._audit_lineage import ExactAnalysisRef, ExactOutputRef
from envresearch.paper.release_contracts import PaperReleaseCandidate

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^factory-run-[0-9a-f]{64}$")
_STRICT = ConfigDict(
    extra="forbid", frozen=True, strict=True, revalidate_instances="always"
)
BindingDimension = Literal[
    "method",
    "estimand",
    "unit",
    "population",
    "time",
    "price",
    "strength",
    "limitation",
]
BindingRelation = Literal["exact", "narrower", "blocked"]
_DIMENSION_ORDER = {
    name: index
    for index, name in enumerate(
        (
            "method",
            "estimand",
            "unit",
            "population",
            "time",
            "price",
            "strength",
            "limitation",
        )
    )
}


def _strict_model(model: type[BaseModel]) -> Any:
    def revalidate(value: object) -> BaseModel:
        if isinstance(value, model):
            return model.model_validate_json(value.model_dump_json())
        return model.model_validate_json(
            json.dumps(value, default=str, separators=(",", ":"))
        )

    return BeforeValidator(revalidate)


StrictArtifactRef = Annotated[ArtifactRef, _strict_model(ArtifactRef)]
StrictDesign = Annotated[ApprovedDesignHandoff, _strict_model(ApprovedDesignHandoff)]
StrictRelease = Annotated[PaperReleaseCandidate, _strict_model(PaperReleaseCandidate)]


def _ref_key(reference: ArtifactRef) -> tuple[str, int, str]:
    return (
        reference.artifact_id,
        reference.artifact_version,
        reference.content_hash,
    )


def _limitation_values(value: str, candidates: tuple[str, ...]) -> tuple[str, ...]:
    matches: list[tuple[str, ...]] = []

    def visit(start: int, selected: tuple[str, ...]) -> None:
        encoded = " | ".join(selected)
        if encoded == value:
            matches.append(selected)
            return
        if len(matches) > 1 or (encoded and not value.startswith(f"{encoded} | ")):
            return
        for index in range(start, len(candidates)):
            visit(index + 1, (*selected, candidates[index]))

    visit(0, ())
    if len(matches) != 1:
        raise ValueError("limitation field encoding is missing or ambiguous")
    return matches[0]


def factory_run_id(design_ref: ArtifactRef, release_ref: ArtifactRef) -> str:
    """Derive identity from both complete exact handoff references."""
    left = ArtifactRef.model_validate(design_ref.model_dump(mode="python"))
    right = ArtifactRef.model_validate(release_ref.model_dump(mode="python"))
    encoded = json.dumps(
        {
            "design_ref": left.model_dump(mode="json"),
            "release_ref": right.model_dump(mode="json"),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"factory-run-{hashlib.sha256(encoded).hexdigest()}"


class BindingField(BaseModel):
    """One claim-level typed relationship reconstructed across stages."""

    model_config = _STRICT

    dimension: BindingDimension
    claim_id: str
    design_value: str
    release_value: str
    relation: BindingRelation

    @field_validator("claim_id", "design_value", "release_value")
    @classmethod
    def require_canonical_value(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("binding values must be canonical nonblank strings")
        return value


class CrossStageBindingReport(BaseModel):
    """Independent retrospective typed coherence, never forward provenance."""

    model_config = _STRICT

    schema_version: Literal["factory.cross-stage-binding.v1"]
    producer: Literal["research-factory-coherence-v1"]
    provenance_claim: Literal["retrospective-coherence"]
    design_id: str
    release_id: str
    fields: tuple[BindingField, ...] = Field(min_length=1)
    limitations: tuple[str, ...] = Field(min_length=1)
    verdict: Literal["coherent", "blocked"]

    @field_validator("limitations")
    @classmethod
    def require_canonical_limitations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if (
            value != tuple(sorted(value))
            or len(value) != len(set(value))
            or any(not item or item != item.strip() for item in value)
        ):
            raise ValueError("limitations must be unique, nonblank, and canonical")
        return value

    @model_validator(mode="after")
    def require_complete_canonical_fields(self) -> CrossStageBindingReport:
        expected = tuple(
            sorted(
                self.fields,
                key=lambda item: (item.claim_id, _DIMENSION_ORDER[item.dimension]),
            )
        )
        keys = tuple((item.claim_id, item.dimension) for item in self.fields)
        if self.fields != expected or len(keys) != len(set(keys)):
            raise ValueError("binding fields must use unique canonical field order")
        if self.verdict == "coherent" and any(
            item.relation == "blocked" for item in self.fields
        ):
            raise ValueError("coherent binding report cannot contain blocked fields")
        if self.verdict == "blocked" and not any(
            item.relation == "blocked" for item in self.fields
        ):
            raise ValueError("blocked binding report requires one blocked field")
        dimensions_by_claim: dict[str, set[str]] = {}
        for item in self.fields:
            dimensions_by_claim.setdefault(item.claim_id, set()).add(item.dimension)
        if any(
            set(_DIMENSION_ORDER) != found for found in dimensions_by_claim.values()
        ):
            raise ValueError("every paper claim must bind every canonical field")
        limitation_fields = tuple(
            item for item in self.fields if item.dimension == "limitation"
        )
        design_inputs = {
            _limitation_values(item.design_value, self.limitations)
            for item in limitation_fields
        }
        if len(design_inputs) != 1:
            raise ValueError("limitation fields disagree on approved design inputs")
        expected_limitations = tuple(
            sorted(
                {
                    *next(iter(design_inputs)),
                    *(
                        limitation
                        for item in limitation_fields
                        for limitation in _limitation_values(
                            item.release_value, self.limitations
                        )
                    ),
                }
            )
        )
        if self.limitations != expected_limitations:
            raise ValueError("limitations must equal the exact canonical input union")
        return self


class CapabilityProfileBinding(BaseModel):
    """Exact registry-bound V0.2 capability profile authority."""

    model_config = _STRICT

    profile_id: str
    registered_version: str
    sha256: str

    @field_validator("profile_id", "registered_version")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("capability identity must be canonical and nonblank")
        return value

    @field_validator("sha256")
    @classmethod
    def require_full_digest(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("capability profile digest must be a full SHA-256")
        return value


def _canonical_artifact_refs(
    design_ref: ArtifactRef,
    design: ApprovedDesignHandoff,
    release_ref: ArtifactRef,
    release: PaperReleaseCandidate,
) -> tuple[ArtifactRef, ...]:
    refs = {
        design_ref,
        release_ref,
        design.manifest.intake_artifact,
        design.plan_ref,
        design.final_context_ref,
        *design.final_context.artifact_refs,
        release.audit_ref,
        release.draft_ref,
        release.map_ref,
        release.ledger_ref,
        release.citation_report_ref,
        *release.revision_refs,
        *release.transitive_refs,
    }
    return tuple(sorted(refs, key=_ref_key))


class ResearchFactoryRun(BaseModel):
    """Complete immutable assembly verdict over exact retrospective handoffs."""

    model_config = _STRICT

    schema_version: Literal["factory.research-run.v1"]
    factory_run_id: str
    producer: Literal["research-factory-run-v1"]
    design_ref: StrictArtifactRef
    design: StrictDesign
    release_ref: StrictArtifactRef
    release: StrictRelease
    binding_report: Annotated[
        CrossStageBindingReport, _strict_model(CrossStageBindingReport)
    ]
    artifact_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    analysis_refs: tuple[ExactAnalysisRef, ...] = Field(min_length=1)
    output_refs: tuple[ExactOutputRef, ...] = Field(min_length=1)
    capability_profiles: tuple[
        Annotated[CapabilityProfileBinding, _strict_model(CapabilityProfileBinding)],
        ...,
    ] = Field(min_length=1)
    assembly_verdict: Literal["assembled"]

    @field_validator("factory_run_id")
    @classmethod
    def require_run_id(cls, value: str) -> str:
        if not _RUN_ID.fullmatch(value):
            raise ValueError("factory run ID must contain one full SHA-256")
        return value

    @model_validator(mode="after")
    def require_exact_complete_assembly(self) -> ResearchFactoryRun:
        if self.factory_run_id != factory_run_id(self.design_ref, self.release_ref):
            raise ValueError("factory run ID does not bind both exact handoffs")
        _require_payload_ref(
            self.design_ref, "approved-design-handoff", self.design.model_dump_json()
        )
        _require_payload_ref(
            self.release_ref, self.release.release_id, self.release.model_dump_json()
        )
        expected_refs = _canonical_artifact_refs(
            self.design_ref, self.design, self.release_ref, self.release
        )
        if self.artifact_refs != tuple(sorted(self.artifact_refs, key=_ref_key)):
            raise ValueError("artifact lineage must use canonical order")
        if self.artifact_refs != expected_refs:
            raise ValueError("artifact lineage is incomplete or inconsistent")
        if (
            self.analysis_refs != self.release.analysis_refs
            or self.output_refs != self.release.output_refs
        ):
            raise ValueError("analysis/output lineage differs from the exact release")
        expected_profiles = tuple(
            CapabilityProfileBinding(
                profile_id=profile_id,
                registered_version=self.design.manifest.method_profiles[profile_id],
                sha256=digest,
            )
            for profile_id, digest in self.design.method_profile_sha256.items()
        )
        if self.capability_profiles != expected_profiles:
            raise ValueError("capability profiles differ from the approved manifest")
        if (
            self.binding_report.design_id != self.design.design_id
            or self.binding_report.release_id != self.release.release_id
            or self.binding_report.verdict != "coherent"
            or any(
                _limitation_values(item.design_value, self.binding_report.limitations)
                != tuple(sorted(self.design.plan.fallback_rules))
                for item in self.binding_report.fields
                if item.dimension == "limitation"
            )
            or not set(self.design.plan.fallback_rules).issubset(
                self.binding_report.limitations
            )
        ):
            raise ValueError("binding report is not coherent with the exact handoffs")
        return self


def _require_payload_ref(
    reference: ArtifactRef, artifact_id: str, payload: str
) -> None:
    if (
        reference.artifact_id != artifact_id
        or reference.artifact_version != 1
        or reference.content_hash != hashlib.sha256(payload.encode()).hexdigest()
    ):
        raise ValueError("embedded handoff reference does not match its payload")


class FactoryRunStatus(BaseModel):
    """Derived read-only promotion state for an immutable assembled run."""

    model_config = _STRICT

    state: Literal["promotion-required", "promoted", "promotion-rejected"]
    run_ref: StrictArtifactRef
    run: Annotated[ResearchFactoryRun, _strict_model(ResearchFactoryRun)]

    @model_validator(mode="after")
    def require_exact_run_reference(self) -> FactoryRunStatus:
        if (
            self.run_ref.artifact_id != self.run.factory_run_id
            or self.run_ref.artifact_version != 1
            or self.run_ref.content_hash
            != hashlib.sha256(self.run.model_dump_json().encode()).hexdigest()
        ):
            raise ValueError("status reference does not bind exact canonical run bytes")
        return self


__all__ = [
    "BindingField",
    "CapabilityProfileBinding",
    "CrossStageBindingReport",
    "FactoryRunStatus",
    "ResearchFactoryRun",
    "factory_run_id",
]
