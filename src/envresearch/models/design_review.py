"""Independent research-design finding and review contracts."""

from __future__ import annotations

from typing import Annotated

from pydantic import (
    BeforeValidator,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from envresearch.models.design import (
    ReviewSeverity,
    SerializedStringFrozenSet,
    SerializedStringTuple,
    _parse_serialized_tuple,
    _require_nonblank,
    _require_nonblank_unique,
    _StrictModel,
)


class DesignFinding(_StrictModel):
    """One independently reviewable design issue and its closure metadata."""

    finding_id: str
    severity: ReviewSeverity
    resolved: StrictBool
    finding: str
    evidence_refs: SerializedStringTuple = Field(min_length=1)
    remediation: str | None = None
    resolution: str | None = None
    residual_risk: str | None = None

    @field_validator("severity", mode="before")
    @classmethod
    def parse_serialized_severity(cls, value: object) -> object:
        if isinstance(value, str):
            return ReviewSeverity(value)
        return value

    @field_validator("finding_id", "finding")
    @classmethod
    def require_nonblank_finding_text(cls, value: str) -> str:
        return _require_nonblank(value)

    @field_validator("evidence_refs")
    @classmethod
    def require_nonblank_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_nonblank_unique(
            value, field_name="evidence_refs", require_items=True
        )

    @field_validator("remediation", "resolution", "residual_risk")
    @classmethod
    def require_nonblank_optional_metadata(cls, value: str | None) -> str | None:
        if value is not None:
            return _require_nonblank(value)
        return value

    @model_validator(mode="after")
    def require_state_specific_metadata(self) -> DesignFinding:
        if self.resolved and self.resolution is None:
            raise ValueError("resolved finding requires a resolution")
        if (
            not self.resolved
            and self.remediation is None
            and self.residual_risk is None
        ):
            raise ValueError("unresolved finding requires remediation or residual risk")
        return self


class DesignReviewPayload(_StrictModel):
    """The independent review output and explicit human major-risk acceptance."""

    review_id: str
    findings: Annotated[
        tuple[DesignFinding, ...], BeforeValidator(_parse_serialized_tuple)
    ]
    accepted_major_ids: SerializedStringFrozenSet = frozenset()

    @field_validator("review_id")
    @classmethod
    def require_nonblank_review_id(cls, value: str) -> str:
        return _require_nonblank(value)

    @field_validator("accepted_major_ids")
    @classmethod
    def require_nonblank_accepted_ids(cls, value: frozenset[str]) -> frozenset[str]:
        if any(not finding_id.strip() for finding_id in value):
            raise ValueError("accepted_major_ids must not contain blank values")
        return value

    @model_validator(mode="after")
    def require_consistent_acceptance_records(self) -> DesignReviewPayload:
        finding_ids = tuple(item.finding_id for item in self.findings)
        if len(set(finding_ids)) != len(finding_ids):
            raise ValueError("findings must not contain duplicate finding_id")
        findings_by_id = {item.finding_id: item for item in self.findings}
        unknown_ids = self.accepted_major_ids - findings_by_id.keys()
        if unknown_ids:
            raise ValueError("accepted_major_ids contain unknown finding IDs")
        for finding_id in self.accepted_major_ids:
            finding = findings_by_id[finding_id]
            if finding.resolved or finding.severity is not ReviewSeverity.MAJOR:
                raise ValueError(
                    "accepted_major_ids may contain only unresolved major IDs"
                )
            if finding.residual_risk is None:
                raise ValueError("accepted major finding requires residual risk")
        return self
