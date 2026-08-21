"""Strict, method-agnostic contracts for research-design artifacts."""

from __future__ import annotations

from enum import StrEnum
from importlib import import_module
from typing import TYPE_CHECKING, Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from envresearch.models.method_screening import MethodRejectionEvidence


class _StrictModel(BaseModel):
    """Base class for immutable persisted design artifacts."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _require_nonblank(value: str) -> str:
    """Reject identifiers and descriptions that cannot be reviewed."""
    if not value.strip():
        raise ValueError("must not be blank")
    return value


def _parse_serialized_tuple(value: object) -> object:
    """Accept JSON/YAML arrays while keeping model scalar validation strict."""
    if isinstance(value, list):
        return tuple(value)
    return value


def _parse_serialized_frozenset(value: object) -> object:
    """Accept JSON/YAML string arrays without losing duplicate acceptance IDs."""
    if not isinstance(value, list):
        return value
    if not all(isinstance(item, str) for item in value):
        return value
    if len(set(value)) != len(value):
        raise ValueError("accepted_major_ids must not contain duplicate values")
    return frozenset(value)


SerializedStringTuple = Annotated[
    tuple[str, ...], BeforeValidator(_parse_serialized_tuple)
]
SerializedStringFrozenSet = Annotated[
    frozenset[str], BeforeValidator(_parse_serialized_frozenset)
]


def _require_nonblank_unique(
    value: tuple[str, ...],
    *,
    field_name: str,
    require_items: bool = False,
) -> tuple[str, ...]:
    """Keep every reference list readable, nonempty where required, and unique."""
    if require_items and not value:
        raise ValueError(f"{field_name} must contain at least one item")
    if any(not item.strip() for item in value):
        raise ValueError(f"{field_name} must not contain blank values")
    if len(set(value)) != len(value):
        raise ValueError(f"{field_name} must not contain duplicate values")
    return value


class EstimandType(StrEnum):
    """The claim class supported by an estimand specification."""

    CAUSAL = "causal"
    DESCRIPTIVE = "descriptive"


class ClaimMode(StrEnum):
    """The language the terminal analysis plan is permitted to use."""

    CAUSAL = "causal"
    DESCRIPTIVE = "descriptive"


class ReviewSeverity(StrEnum):
    """The fixed review-severity vocabulary for design findings."""

    BLOCKING = "blocking"
    MAJOR = "major"
    MINOR = "minor"
    ADVISORY = "advisory"


class MethodCandidateRole(StrEnum):
    """A method's selected, alternative, or rejected position in a ranking."""

    PRIMARY = "primary"
    ALTERNATIVE = "alternative"
    REJECTED = "rejected"


class EstimandSpecPayload(_StrictModel):
    """An evidence-backed causal or descriptive target, independent of method."""

    estimand_id: str
    estimand_type: EstimandType
    population: str
    unit: str
    exposure_or_treatment: str
    outcome: str
    comparison_or_counterfactual: str | None = None
    time_horizon: str
    target_parameter: str
    evidence_refs: SerializedStringTuple = Field(min_length=1)
    assumption_refs: SerializedStringTuple = ()

    @field_validator("estimand_type", mode="before")
    @classmethod
    def parse_serialized_estimand_type(cls, value: object) -> object:
        """Accept only the exact persisted enum values for an estimand type."""
        if isinstance(value, str):
            return EstimandType(value)
        return value

    @field_validator(
        "estimand_id",
        "population",
        "unit",
        "exposure_or_treatment",
        "outcome",
        "time_horizon",
        "target_parameter",
    )
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        """Require each estimand component to remain interpretable in storage."""
        return _require_nonblank(value)

    @field_validator("comparison_or_counterfactual")
    @classmethod
    def require_nonblank_optional_text(cls, value: str | None) -> str | None:
        """Treat an empty comparison as absent rather than meaningful content."""
        if value is not None:
            return _require_nonblank(value)
        return value

    @field_validator("evidence_refs", "assumption_refs")
    @classmethod
    def require_valid_references(
        cls, value: tuple[str, ...], info: object
    ) -> tuple[str, ...]:
        """Preserve unique evidence and assumption links for claim-mode validation."""
        field_name = getattr(info, "field_name", "references")
        return _require_nonblank_unique(
            value,
            field_name=field_name,
            require_items=field_name == "evidence_refs",
        )

    @model_validator(mode="after")
    def require_type_specific_claim_support(self) -> EstimandSpecPayload:
        """Require counterfactual and assumptions before an estimand is causal."""
        if self.estimand_type is EstimandType.CAUSAL:
            if self.comparison_or_counterfactual is None:
                raise ValueError(
                    "causal estimand requires a comparison or counterfactual"
                )
            if not self.assumption_refs:
                raise ValueError("causal estimand requires assumption references")
        elif self.comparison_or_counterfactual is not None:
            raise ValueError("descriptive estimand must not declare a counterfactual")
        return self


class MethodCandidate(_StrictModel):
    """One ranked profile assessment without implementing an estimator."""

    method_profile_ref: str
    role: MethodCandidateRole
    rank: StrictInt = Field(ge=1)
    estimand_compatible: StrictBool
    required_assumption_refs: SerializedStringTuple = Field(min_length=1)
    required_data_structure: SerializedStringTuple = Field(min_length=1)
    diagnostics: SerializedStringTuple = Field(min_length=1)
    fallback_or_limitations: SerializedStringTuple = Field(min_length=1)
    rejection_evidence: MethodRejectionEvidence | None = None

    @field_validator("role", mode="before")
    @classmethod
    def parse_serialized_role(cls, value: object) -> object:
        """Accept only the exact persisted enum values for candidate role."""
        if isinstance(value, str):
            return MethodCandidateRole(value)
        return value

    @field_validator("method_profile_ref")
    @classmethod
    def require_nonblank_profile_ref(cls, value: str) -> str:
        """Ensure a selected method resolves to one versioned profile reference."""
        return _require_nonblank(value)

    @field_validator(
        "required_assumption_refs",
        "required_data_structure",
        "diagnostics",
        "fallback_or_limitations",
    )
    @classmethod
    def require_complete_unique_details(
        cls, value: tuple[str, ...], info: object
    ) -> tuple[str, ...]:
        """Require the data, diagnostics, and limitation record for every profile."""
        return _require_nonblank_unique(
            value,
            field_name=getattr(info, "field_name", "details"),
            require_items=True,
        )

    @model_validator(mode="after")
    def require_role_matches_compatibility(self) -> MethodCandidate:
        """Prevent a rejected method from being silently selected into a plan."""
        if self.estimand_compatible and self.role is MethodCandidateRole.REJECTED:
            raise ValueError("compatible method candidate must not be rejected")
        if (
            not self.estimand_compatible
            and self.role is not MethodCandidateRole.REJECTED
        ):
            raise ValueError("incompatible method candidate must be rejected")
        if self.role is MethodCandidateRole.REJECTED:
            if self.rejection_evidence is None:
                raise ValueError(
                    "rejected method requires structured rejection evidence"
                )
        elif self.rejection_evidence is not None:
            raise ValueError("retained method must not declare rejection evidence")
        return self


class MethodCandidatesPayload(_StrictModel):
    """One primary profile, explicit alternatives, and optional rejected profiles."""

    estimand_ref: str
    candidates: Annotated[
        tuple[MethodCandidate, ...], BeforeValidator(_parse_serialized_tuple)
    ] = Field(min_length=2)

    @field_validator("estimand_ref")
    @classmethod
    def require_nonblank_estimand_ref(cls, value: str) -> str:
        """Keep profile selection linked to exactly one estimand artifact."""
        return _require_nonblank(value)

    @model_validator(mode="after")
    def require_ranked_unique_selection(self) -> MethodCandidatesPayload:
        """Require a deterministic primary and at least one alternative profile."""
        profile_refs = tuple(item.method_profile_ref for item in self.candidates)
        if len(set(profile_refs)) != len(profile_refs):
            raise ValueError("candidates must not contain duplicate method_profile_ref")
        ranks = tuple(item.rank for item in self.candidates)
        if len(set(ranks)) != len(ranks):
            raise ValueError("candidates must not contain duplicate ranks")
        primary = tuple(
            item for item in self.candidates if item.role is MethodCandidateRole.PRIMARY
        )
        alternatives = tuple(
            item
            for item in self.candidates
            if item.role is MethodCandidateRole.ALTERNATIVE
        )
        if len(primary) != 1:
            raise ValueError("method candidates require exactly one primary")
        if primary[0].rank != 1:
            raise ValueError("primary method candidate must have rank 1")
        if not alternatives:
            raise ValueError("method candidates require at least one alternative")
        if any(item.rank <= primary[0].rank for item in alternatives):
            raise ValueError("alternative method candidates must rank after primary")
        return self

    @property
    def primary(self) -> MethodCandidate:
        """Return the validated, uniquely selected primary candidate."""
        return next(
            item for item in self.candidates if item.role is MethodCandidateRole.PRIMARY
        )

    @property
    def alternatives(self) -> tuple[MethodCandidate, ...]:
        """Return all validated alternatives in their declared ranking order."""
        return tuple(
            sorted(
                (
                    item
                    for item in self.candidates
                    if item.role is MethodCandidateRole.ALTERNATIVE
                ),
                key=lambda item: item.rank,
            )
        )


class IdentificationMemoMetadata(_StrictModel):
    """ID-based metadata tying the identification memo to design evidence."""

    estimand_ref: str
    primary_method_profile_ref: str
    alternative_method_profile_refs: SerializedStringTuple = Field(min_length=1)
    assumption_refs: SerializedStringTuple = Field(min_length=1)
    threat_refs: SerializedStringTuple = Field(min_length=1)
    diagnostic_refs: SerializedStringTuple = Field(min_length=1)
    evidence_refs: SerializedStringTuple = Field(min_length=1)
    residual_risks: SerializedStringTuple = ()

    @field_validator("estimand_ref", "primary_method_profile_ref")
    @classmethod
    def require_nonblank_identity_ref(cls, value: str) -> str:
        """Reject anonymous links between the memo and its upstream artifacts."""
        return _require_nonblank(value)

    @field_validator(
        "alternative_method_profile_refs",
        "assumption_refs",
        "threat_refs",
        "diagnostic_refs",
        "evidence_refs",
        "residual_risks",
    )
    @classmethod
    def require_unique_memo_details(
        cls, value: tuple[str, ...], info: object
    ) -> tuple[str, ...]:
        """Keep memo evidence and risks unambiguous when persisted as metadata."""
        field_name = getattr(info, "field_name", "details")
        return _require_nonblank_unique(
            value,
            field_name=field_name,
            require_items=field_name != "residual_risks",
        )

    @model_validator(mode="after")
    def require_distinct_method_references(self) -> IdentificationMemoMetadata:
        """Prevent the selected primary profile from being listed as an alternative."""
        if self.primary_method_profile_ref in self.alternative_method_profile_refs:
            raise ValueError("primary method profile must not duplicate alternatives")
        return self


if TYPE_CHECKING:
    from envresearch.models.design_plan import (
        AnalysisPlanPayload as AnalysisPlanPayload,  # noqa: PLC0414
    )
    from envresearch.models.design_plan import (
        ResearchQualityScores as ResearchQualityScores,  # noqa: PLC0414
    )
    from envresearch.models.design_review import (
        DesignFinding as DesignFinding,  # noqa: PLC0414
    )
    from envresearch.models.design_review import (
        DesignReviewPayload as DesignReviewPayload,  # noqa: PLC0414
    )

_COMPAT_EXPORTS = {
    "AnalysisPlanPayload": "envresearch.models.design_plan",
    "ResearchQualityScores": "envresearch.models.design_plan",
    "DesignFinding": "envresearch.models.design_review",
    "DesignReviewPayload": "envresearch.models.design_review",
}


def __getattr__(name: str) -> object:
    """Load split public contracts lazily without introducing import cycles."""
    module_name = _COMPAT_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
