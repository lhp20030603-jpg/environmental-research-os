"""Strict parsing and normalization for local Zotero JSON metadata exports."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from envresearch.connectors.contracts import ConnectorUnavailable
from envresearch.models.evidence import AttachmentMetadata

_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_CANONICAL_DOI = re.compile(r"^10\.\d{1,9}/[^\s\x00-\x1f\x7f]+$", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")
_SUPPORTED_ITEM_TYPES = frozenset(
    {
        "artwork",
        "audioRecording",
        "bill",
        "blogPost",
        "book",
        "bookSection",
        "case",
        "computerProgram",
        "conferencePaper",
        "dictionaryEntry",
        "document",
        "email",
        "encyclopediaArticle",
        "film",
        "forumPost",
        "hearing",
        "instantMessage",
        "interview",
        "journalArticle",
        "letter",
        "magazineArticle",
        "manuscript",
        "map",
        "newspaperArticle",
        "patent",
        "podcast",
        "presentation",
        "radioBroadcast",
        "report",
        "statute",
        "thesis",
        "tvBroadcast",
        "videoRecording",
        "webpage",
    }
)


@dataclass(frozen=True)
class _ExportItem:
    """Validated public metadata from one bibliographic Zotero export entry."""

    key: str
    title: str
    authors: tuple[str, ...]
    date: str | None
    doi: str | None
    url: str | None
    abstract: str | None
    tags: tuple[str, ...]
    collections: tuple[str, ...]
    attachments: tuple[AttachmentMetadata, ...]


def _read_export_array(path: Path) -> tuple[_ExportItem, ...]:
    """Load exactly one UTF-8 JSON array, translating expected boundary failures."""
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ConnectorUnavailable(
            connector_id="zotero-json-export",
            reason_code="EXPORT_MISSING",
            diagnostic="local Zotero export is unavailable",
        ) from error
    except (OSError, UnicodeError) as error:
        raise ConnectorUnavailable(
            connector_id="zotero-json-export",
            reason_code="EXPORT_UNREADABLE",
            diagnostic="local Zotero export cannot be read",
        ) from error
    try:
        payload = json.loads(
            content,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_object_fields,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise ConnectorUnavailable(
            connector_id="zotero-json-export",
            reason_code="EXPORT_MALFORMED",
            diagnostic="local Zotero export has invalid JSON",
        ) from error
    if not isinstance(payload, list):
        raise _invalid_export()

    parsed: list[_ExportItem] = []
    seen: dict[str, _ExportItem] = {}
    for index, raw_item in enumerate(payload):
        item = _parse_item(raw_item, index)
        previous = seen.get(item.key)
        if previous is not None:
            if previous != item:
                raise _invalid_export()
            continue
        seen[item.key] = item
        parsed.append(item)
    return tuple(parsed)


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid JSON constant")


def _reject_duplicate_object_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    object_value: dict[str, Any] = {}
    for key, value in pairs:
        if key in object_value:
            raise ValueError("duplicate JSON object field")
        object_value[key] = value
    return object_value


def _parse_item(raw_item: object, index: int) -> _ExportItem:
    if not isinstance(raw_item, dict):
        raise _invalid_export()
    key = _required_string(raw_item, "key", f"item {index}")
    item_type = _required_string(raw_item, "itemType", f"item {index}")
    if item_type not in _SUPPORTED_ITEM_TYPES:
        raise _invalid_export()
    title = _required_string(raw_item, "title", f"item {index}")
    date = _optional_string(raw_item, "date", index)
    if _year(date) == 0:
        raise _invalid_export()
    return _ExportItem(
        key=key,
        title=title,
        authors=_parse_creators(raw_item.get("creators", []), index),
        date=date,
        doi=_normalize_doi(_optional_string(raw_item, "DOI", index)),
        url=_optional_string(raw_item, "url", index),
        abstract=_optional_string(raw_item, "abstractNote", index),
        tags=_parse_tags(raw_item.get("tags", []), index),
        collections=_parse_string_list(
            raw_item.get("collections", []), "collections", index
        ),
        attachments=_parse_attachments(raw_item.get("attachments", []), index),
    )


def _required_string(item: dict[str, Any], field: str, context: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _invalid_export(f"{context}.{field} must be a nonblank string")
    return value.strip()


def _optional_string(item: dict[str, Any], field: str, index: int) -> str | None:
    if field not in item or item[field] is None:
        return None
    value = item[field]
    if not isinstance(value, str):
        raise _invalid_export(f"item {index}.{field} must be a string or null")
    return value.strip() or None


def _parse_creators(raw: object, index: int) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise _invalid_export(f"item {index}.creators must be an array")
    creators: list[str] = []
    for creator_index, creator in enumerate(raw):
        context = f"item {index}.creators[{creator_index}]"
        if not isinstance(creator, dict):
            raise _invalid_export(f"{context} must be an object")
        creator_type = creator.get("creatorType")
        if not isinstance(creator_type, str) or not creator_type.strip():
            raise _invalid_export(f"{context}.creatorType must be a nonblank string")
        name = creator.get("name")
        first_name = creator.get("firstName", "")
        last_name = creator.get("lastName", "")
        if name is not None:
            if not isinstance(name, str) or not name.strip():
                raise _invalid_export(f"{context}.name must be a nonblank string")
            creators.append(_normalize_space(name))
        else:
            if not isinstance(first_name, str) or not isinstance(last_name, str):
                raise _invalid_export(f"{context} name fields must be strings")
            full_name = _normalize_space(f"{first_name} {last_name}")
            if not full_name:
                raise _invalid_export(f"{context} requires a creator name")
            creators.append(full_name)
    return tuple(creators)


def _parse_tags(raw: object, index: int) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise _invalid_export(f"item {index}.tags must be an array")
    tags: list[str] = []
    for tag_index, tag in enumerate(raw):
        context = f"item {index}.tags[{tag_index}]"
        if not isinstance(tag, dict):
            raise _invalid_export(f"{context} must be an object")
        label = tag.get("tag")
        if not isinstance(label, str) or not label.strip():
            raise _invalid_export(f"{context}.tag must be a nonblank string")
        tags.append(_normalize_space(label))
    return tuple(tags)


def _parse_string_list(raw: object, field: str, index: int) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise _invalid_export(f"item {index}.{field} must be an array")
    values: list[str] = []
    for value_index, value in enumerate(raw):
        if not isinstance(value, str) or not value.strip():
            raise _invalid_export(
                f"item {index}.{field}[{value_index}] must be a nonblank string"
            )
        values.append(_normalize_space(value))
    return tuple(values)


def _parse_attachments(raw: object, index: int) -> tuple[AttachmentMetadata, ...]:
    if not isinstance(raw, list):
        raise _invalid_export(f"item {index}.attachments must be an array")
    attachments: list[AttachmentMetadata] = []
    for attachment_index, attachment in enumerate(raw):
        context = f"item {index}.attachments[{attachment_index}]"
        if not isinstance(attachment, dict):
            raise _invalid_export(f"{context} must be an object")
        if "content" in attachment or "data" in attachment:
            raise _invalid_export(f"{context} must not embed attachment contents")
        metadata: dict[str, str] = {}
        for source_name, target_name in (
            ("key", "key"),
            ("title", "title"),
            ("contentType", "content_type"),
            ("mimeType", "content_type"),
            ("linkMode", "link_mode"),
        ):
            if source_name not in attachment or attachment[source_name] is None:
                continue
            value = attachment[source_name]
            if not isinstance(value, str) or not value.strip():
                raise _invalid_export(
                    f"{context}.{source_name} must be a nonblank string"
                )
            metadata.setdefault(target_name, _normalize_space(value))
        if not metadata:
            raise _invalid_export(f"{context} must contain public metadata")
        attachments.append(AttachmentMetadata.model_validate(metadata))
    return tuple(attachments)


def _normalize_doi(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _DOI_PREFIX.sub("", value.strip()).strip().casefold()
    return normalized if _CANONICAL_DOI.fullmatch(normalized) else None


def _year(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.match(r"^(\d{4})(?:\D|$)", value)
    return int(match.group(1)) if match is not None else None


def _contains(value: str, query: str) -> bool:
    return _normalized(query) in _normalized(value)


def _normalize_space(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


def _normalized(value: str) -> str:
    return _normalize_space(value).casefold()


def _invalid_export(_detail: str = "") -> ConnectorUnavailable:
    return ConnectorUnavailable(
        connector_id="zotero-json-export",
        reason_code="EXPORT_MALFORMED",
        diagnostic="local Zotero export has invalid metadata",
    )
