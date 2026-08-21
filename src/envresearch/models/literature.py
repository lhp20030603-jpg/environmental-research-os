"""Strict source and evidence-row contracts for literature maps."""

from __future__ import annotations

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)


class _StrictLiteratureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _require_nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


class AttachmentMetadata(_StrictLiteratureModel):
    """Inert, immutable metadata about a Zotero attachment, never its contents."""

    key: str | None = None
    title: str | None = None
    content_type: str | None = None
    link_mode: str | None = None

    @field_validator("key", "title", "content_type", "link_mode")
    @classmethod
    def require_nonblank_optional_text(cls, value: str | None) -> str | None:
        if value is not None:
            return _require_nonblank(value)
        return value

    @model_validator(mode="after")
    def require_some_metadata(self) -> AttachmentMetadata:
        if all(
            value is None
            for value in (self.key, self.title, self.content_type, self.link_mode)
        ):
            raise ValueError("attachment metadata requires at least one field")
        return self


class SourceRecord(_StrictLiteratureModel):
    """One identifiable source retained for a literature map."""

    source_id: str
    title: str
    source: str
    authors: tuple[str, ...] = ()
    publication_year: StrictInt | None = Field(default=None, ge=1)
    license: str | None = None
    evidence_reason: str
    doi: str | None = None
    connector_keys: tuple[str, ...] = ()
    publication_date: str | None = None
    abstract: str | None = None
    tags: tuple[str, ...] = ()
    collections: tuple[str, ...] = ()
    attachments: tuple[AttachmentMetadata, ...] = ()

    @field_validator("source_id", "title", "source", "evidence_reason")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        return _require_nonblank(value)

    @field_validator("authors")
    @classmethod
    def require_nonblank_authors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not author.strip() for author in value):
            raise ValueError("authors must not contain blank values")
        return value

    @field_validator("doi", "publication_date", "abstract")
    @classmethod
    def require_nonblank_optional_text(cls, value: str | None) -> str | None:
        if value is not None:
            return _require_nonblank(value)
        return value

    @field_validator("connector_keys", "tags", "collections")
    @classmethod
    def require_nonblank_metadata(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not entry.strip() for entry in value):
            raise ValueError("metadata entries must not be blank")
        return value


class EvidenceRow(_StrictLiteratureModel):
    """A reviewable statement linked to exactly one source record."""

    evidence_id: str
    source_id: str
    finding: str
    relevance: str
    evidence_reason: str

    @field_validator(
        "evidence_id", "source_id", "finding", "relevance", "evidence_reason"
    )
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        return _require_nonblank(value)


class LiteratureMapPayload(_StrictLiteratureModel):
    """Literature sources and rows supporting a proposed research design."""

    research_question: str
    sources: tuple[SourceRecord, ...]
    evidence_rows: tuple[EvidenceRow, ...]
    synthesis: str

    @field_validator("research_question", "synthesis")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        return _require_nonblank(value)
