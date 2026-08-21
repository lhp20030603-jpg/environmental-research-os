"""Local-only adapter for public metadata in a Zotero JSON export."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from envresearch.connectors.contracts import LiteratureQuery
from envresearch.connectors.zotero_export_parsing import (
    _contains,
    _ExportItem,
    _normalized,
    _read_export_array,
    _year,
)
from envresearch.models.evidence import AttachmentMetadata, SourceRecord


@dataclass
class _SourceAccumulator:
    """One stable source group with persistent metadata seen sets for linear merging."""

    identity: str
    key: str
    title: str
    date: str | None
    doi: str | None
    url: str | None
    abstract: str | None
    connector_keys: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)
    attachments: list[AttachmentMetadata] = field(default_factory=list)
    _connector_key_seen: set[str] = field(default_factory=set)
    _author_seen: set[str] = field(default_factory=set)
    _tag_seen: set[str] = field(default_factory=set)
    _collection_seen: set[str] = field(default_factory=set)
    _attachment_seen: set[str] = field(default_factory=set)

    @classmethod
    def from_item(cls, identity: str, item: _ExportItem) -> _SourceAccumulator:
        """Seed an ordered merge group with its first declared Zotero item."""
        accumulator = cls(
            identity=identity,
            key=item.key,
            title=item.title,
            date=item.date,
            doi=item.doi,
            url=item.url,
            abstract=item.abstract,
        )
        accumulator.append(item)
        return accumulator

    def append(self, item: _ExportItem) -> None:
        """Conservatively merge one item without reconstructing prior seen sets."""
        if self.date is None:
            self.date = item.date
        if self.url is None:
            self.url = item.url
        if self.abstract is None:
            self.abstract = item.abstract
        self._append_text(self.connector_keys, self._connector_key_seen, (item.key,))
        self._append_text(self.authors, self._author_seen, item.authors)
        self._append_text(self.tags, self._tag_seen, item.tags)
        self._append_text(self.collections, self._collection_seen, item.collections)
        for attachment in item.attachments:
            attachment_identity = attachment.model_dump_json()
            if attachment_identity not in self._attachment_seen:
                self.attachments.append(attachment)
                self._attachment_seen.add(attachment_identity)

    @staticmethod
    def _append_text(
        destination: list[str], seen: set[str], values: tuple[str, ...]
    ) -> None:
        """Append normalized-distinct text while retaining first declaration spelling."""
        for value in values:
            normalized = _normalized(value)
            if normalized not in seen:
                destination.append(value)
                seen.add(normalized)

    def to_source_record(self) -> SourceRecord:
        """Finalize this ordered group as one immutable connector-neutral record."""
        return SourceRecord(
            source_id=self.identity,
            title=self.title,
            source=self.url or f"zotero://{self.key}",
            authors=tuple(self.authors),
            publication_year=_year(self.date),
            license=None,
            evidence_reason="Public metadata from a local Zotero JSON export.",
            doi=self.doi,
            connector_keys=tuple(self.connector_keys),
            publication_date=self.date,
            abstract=self.abstract,
            tags=tuple(self.tags),
            collections=tuple(self.collections),
            attachments=tuple(self.attachments),
        )


class ZoteroExportConnector:
    """Search a user-created Zotero JSON export without network or file traversal."""

    connector_id = "zotero-json-export"
    connector_version = "1.0.0"

    def __init__(self, export_path: Path) -> None:
        self.export_path = export_path

    def search(self, query: LiteratureQuery) -> tuple[SourceRecord, ...]:
        """Read and search declared public metadata from one local JSON export."""
        if not isinstance(query, LiteratureQuery):
            raise TypeError("query must be a LiteratureQuery")
        validated_query = LiteratureQuery.model_validate(query.model_dump())
        items = _read_export_array(self.export_path)
        return _deduplicate_sources(
            item for item in items if _matches(validated_query, item)
        )


def _matches(query: LiteratureQuery, item: _ExportItem) -> bool:
    """Apply every declared query field against normalized public metadata."""
    if query.text is not None:
        searchable = (
            item.title,
            *item.authors,
            item.date or "",
            item.doi or "",
            item.url or "",
            item.abstract or "",
            *item.tags,
            *item.collections,
            *(
                value
                for attachment in item.attachments
                for value in (
                    attachment.key,
                    attachment.title,
                    attachment.content_type,
                    attachment.link_mode,
                )
                if value is not None
            ),
        )
        if not any(_contains(value, query.text) for value in searchable):
            return False
    if query.title is not None and not _contains(item.title, query.title):
        return False
    if query.author is not None and not any(
        _contains(author, query.author) for author in item.authors
    ):
        return False
    if query.tag is not None and not any(
        _contains(tag, query.tag) for tag in item.tags
    ):
        return False
    if query.collection is not None and not any(
        _contains(collection, query.collection) for collection in item.collections
    ):
        return False
    if query.doi is not None and item.doi != query.doi:
        return False
    return query.year is None or _year(item.date) == query.year


def _deduplicate_sources(items: Iterable[_ExportItem]) -> tuple[SourceRecord, ...]:
    """Merge DOI-identical records with persistent seen sets in export order."""
    groups: dict[str, _SourceAccumulator] = {}
    for item in items:
        identity = f"doi:{item.doi}" if item.doi is not None else f"zotero:{item.key}"
        group = groups.get(identity)
        if group is None:
            groups[identity] = _SourceAccumulator.from_item(identity, item)
        else:
            group.append(item)
    return tuple(group.to_source_record() for group in groups.values())
