"""Frozen contracts for one evidence-bound V0.4 paper draft."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._audit_lineage import ExactArtifactRef
from envresearch.paper.contracts import (
    CANONICAL_ID,
    STRICT,
    AnalysisOutputRef,
    ClaimStrength,
)

DraftSection = Literal[
    "title",
    "research-question",
    "methods",
    "results",
    "limitations",
    "validation-scope",
]
SpanPurpose = Literal["finding", "limitation", "validation-scope"]


def _canonical_text(value: str, field: str) -> str:
    if (
        not value
        or value != value.strip()
        or any(character in value for character in "\r\n\t")
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{field} must be canonical plain text")
    return value


def _canonical_ids(value: tuple[str, ...], field: str) -> tuple[str, ...]:
    if (
        not value
        or len(value) != len(set(value))
        or any(not CANONICAL_ID.fullmatch(item) for item in value)
    ):
        raise ValueError(f"{field} must contain unique canonical claim ids")
    return value


class PaperParagraph(BaseModel):
    """One ordered plain-text paragraph with a typed manuscript role."""

    model_config = STRICT

    paragraph_id: str
    position: int = Field(ge=0)
    section: DraftSection
    text: str

    @field_validator("paragraph_id")
    @classmethod
    def require_paragraph_id(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("paragraph id must be canonical lowercase kebab-case")
        return value

    @field_validator("text")
    @classmethod
    def require_plain_text(cls, value: str) -> str:
        return _canonical_text(value, "paragraph text")


class ClaimSpanBinding(BaseModel):
    """One exact paragraph span bound to registered empirical claim rows."""

    model_config = STRICT

    paragraph_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    claim_ids: tuple[str, ...] = Field(min_length=1)
    purpose: SpanPurpose
    allowed_strength: ClaimStrength
    unit: str
    population_basis: str
    time_basis: str
    price_base: str

    @field_validator("paragraph_id")
    @classmethod
    def require_paragraph_id(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("paragraph id must be canonical")
        return value

    @field_validator("claim_ids")
    @classmethod
    def require_claim_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_ids(value, "claim binding")

    @field_validator("unit", "population_basis", "time_basis", "price_base")
    @classmethod
    def require_basis(cls, value: str) -> str:
        return _canonical_text(value, "claim basis")

    @model_validator(mode="after")
    def require_positive_span(self) -> ClaimSpanBinding:
        if self.end <= self.start:
            raise ValueError("claim span end must follow start")
        return self


class CitationBinding(BaseModel):
    """One exact prose span bound to a verified claim in an exact source sheet."""

    model_config = STRICT

    paragraph_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    source_sheet_ref: ArtifactRef
    claim_id: str

    @field_validator("paragraph_id", "claim_id")
    @classmethod
    def require_identifier(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("citation identifiers must be canonical")
        return value

    @model_validator(mode="after")
    def require_positive_span(self) -> CitationBinding:
        if self.end <= self.start:
            raise ValueError("citation span end must follow start")
        return self


class _OutputBinding(BaseModel):
    """Shared exact result-output binding for a rendered table or figure."""

    model_config = STRICT

    binding_id: str
    claim_ids: tuple[str, ...] = Field(min_length=1)
    artifact_path: str
    caption: str
    output: AnalysisOutputRef

    @field_validator("binding_id")
    @classmethod
    def require_binding_id(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("output binding id must be canonical")
        return value

    @field_validator("claim_ids")
    @classmethod
    def require_claim_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_ids(value, "output binding")

    @field_validator("artifact_path")
    @classmethod
    def require_output_path(cls, value: str) -> str:
        if (
            not value.startswith("outputs/")
            or value != value.strip()
            or ".." in value.split("/")
            or "\\" in value
            or value.count("/") != 1
        ):
            raise ValueError("output artifact path must be canonical and confined")
        return value

    @field_validator("caption")
    @classmethod
    def require_caption(cls, value: str) -> str:
        return _canonical_text(value, "output caption")


class TableBinding(_OutputBinding):
    """One exact tabular output reused by the draft."""

    kind: Literal["table"]


class FigureBinding(_OutputBinding):
    """One exact figure output reused by the draft."""

    kind: Literal["figure"]


class PaperDraftCandidate(BaseModel):
    """Caller-owned prose and bindings without artifact authority fields."""

    model_config = STRICT

    paragraphs: tuple[PaperParagraph, ...] = Field(min_length=5)
    claim_bindings: tuple[ClaimSpanBinding, ...] = Field(min_length=2)
    citation_bindings: tuple[CitationBinding, ...] = Field(min_length=1)
    tables: tuple[TableBinding, ...] = Field(min_length=1)
    figures: tuple[FigureBinding, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_complete_slice(self) -> PaperDraftCandidate:
        _require_paragraph_set(self.paragraphs)
        _require_unique_bindings(self)
        return self


class PaperDraft(BaseModel):
    """Canonical immutable paper slice bound to every exact upstream authority."""

    model_config = STRICT

    schema_version: Literal["paper.draft.v1"]
    draft_id: str
    producer: Literal["paper-builder-draft-v1"]
    generation: int = Field(default=1, ge=1)
    predecessor_ref: ExactArtifactRef | None = None
    map_ref: ArtifactRef
    ledger_ref: ArtifactRef
    citation_report_ref: ArtifactRef
    paragraphs: tuple[PaperParagraph, ...] = Field(min_length=5)
    claim_bindings: tuple[ClaimSpanBinding, ...] = Field(min_length=2)
    citation_bindings: tuple[CitationBinding, ...] = Field(min_length=1)
    tables: tuple[TableBinding, ...] = Field(min_length=1)
    figures: tuple[FigureBinding, ...] = Field(min_length=1)

    @field_validator("draft_id")
    @classmethod
    def require_draft_id(cls, value: str) -> str:
        if not CANONICAL_ID.fullmatch(value):
            raise ValueError("draft id must be canonical")
        return value

    @model_validator(mode="after")
    def require_canonical_payload(self) -> PaperDraft:
        if (self.generation == 1) != (self.predecessor_ref is None):
            raise ValueError("draft generation and predecessor disagree")
        if self.predecessor_ref is not None and (
            self.predecessor_ref.artifact_id != self.draft_id
            or self.predecessor_ref.artifact_version != self.generation - 1
        ):
            raise ValueError("draft predecessor is not the exact previous generation")
        _require_paragraph_set(self.paragraphs)
        _require_unique_bindings(self)
        if self.paragraphs != tuple(
            sorted(self.paragraphs, key=lambda item: item.position)
        ):
            raise ValueError("draft paragraphs must use canonical order")
        for bindings in (
            self.claim_bindings,
            self.citation_bindings,
            self.tables,
            self.figures,
        ):
            if bindings != tuple(sorted(bindings, key=_binding_key)):
                raise ValueError("draft bindings must use canonical order")
        return self


def _require_paragraph_set(paragraphs: tuple[PaperParagraph, ...]) -> None:
    ids = tuple(item.paragraph_id for item in paragraphs)
    positions = tuple(item.position for item in paragraphs)
    if len(ids) != len(set(ids)) or len(positions) != len(set(positions)):
        raise ValueError("draft paragraphs require unique ids and positions")
    if tuple(sorted(positions)) != tuple(range(len(paragraphs))):
        raise ValueError("draft paragraph positions must be consecutive")
    sections = tuple(item.section for item in paragraphs)
    required = {"title", "research-question", "methods", "results", "limitations"}
    if not required.issubset(sections) or any(
        sections.count(item) != 1 for item in required
    ):
        raise ValueError("draft must contain one paragraph for every required section")
    if sections.count("validation-scope") > 1:
        raise ValueError("draft may contain at most one validation-scope paragraph")


def _binding_key(binding: object) -> tuple[object, ...]:
    if isinstance(binding, (ClaimSpanBinding, CitationBinding)):
        return (binding.paragraph_id, binding.start, binding.end)
    assert isinstance(binding, _OutputBinding)
    return (binding.binding_id,)


def _require_unique_bindings(candidate: PaperDraftCandidate | PaperDraft) -> None:
    for bindings in (
        candidate.claim_bindings,
        candidate.citation_bindings,
        candidate.tables,
        candidate.figures,
    ):
        keys = tuple(_binding_key(item) for item in bindings)
        if len(keys) != len(set(keys)):
            raise ValueError("draft bindings must be unique")
    output_ids = tuple(
        item.binding_id for item in (*candidate.tables, *candidate.figures)
    )
    if len(output_ids) != len(set(output_ids)):
        raise ValueError("table and figure binding ids must be globally unique")


__all__ = [
    "CitationBinding",
    "ClaimSpanBinding",
    "DraftSection",
    "FigureBinding",
    "PaperDraft",
    "PaperDraftCandidate",
    "PaperParagraph",
    "SpanPurpose",
    "TableBinding",
]
