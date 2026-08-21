"""Behavioral tests for the local-only Zotero JSON export connector."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from envresearch.connectors import (
    ConnectorUnavailable,
    LiteratureQuery,
    literature_gateway,
)
from envresearch.connectors.zotero_export import ZoteroExportConnector


def zotero_item(key: str, **changes: object) -> dict[str, object]:
    """Return a complete supported bibliographic item for local export tests."""
    item: dict[str, object] = {
        "key": key,
        "itemType": "journalArticle",
        "title": "Carbon Pricing and Household Energy Use",
        "creators": [
            {"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}
        ],
        "date": "2024-05-01",
        "DOI": "https://doi.org/10.1000/EXAMPLE",
        "url": "https://example.invalid/paper",
        "abstractNote": "Carbon pricing changes household energy use.",
        "tags": [{"tag": "Climate Policy"}],
        "collections": ["Policy studies"],
        "attachments": [
            {
                "key": "ATTACH-1",
                "title": "Publisher PDF",
                "contentType": "application/pdf",
                "path": "/definitely/not/read/paper.pdf",
            }
        ],
    }
    item.update(changes)
    return item


def write_export(path: Path, items: list[dict[str, object]]) -> None:
    """Write a synthetically exported Zotero JSON metadata array."""
    path.write_text(json.dumps(items), encoding="utf-8")


def test_zotero_export_deduplicates_doi_and_preserves_item_keys(tmp_path: Path) -> None:
    """Equivalent DOI variants become one record in declaration order."""
    export = tmp_path / "zotero.json"
    write_export(
        export,
        [
            zotero_item("A1"),
            zotero_item(
                "A2",
                DOI="doi:10.1000/example",
                tags=[{"tag": "Energy"}],
                collections=["Replication"],
            ),
        ],
    )

    records = ZoteroExportConnector(export).search(LiteratureQuery(text="carbon pricing"))

    assert len(records) == 1
    assert records[0].doi == "10.1000/example"
    assert records[0].connector_keys == ("A1", "A2")
    assert records[0].tags == ("Climate Policy", "Energy")
    assert records[0].collections == ("Policy studies", "Replication")


@pytest.mark.parametrize(
    "invalid_doi",
    ["N/A", "unknown", "10.1000 /example", "https://doi.org/not-a-doi"],
)
def test_zotero_export_uses_item_keys_for_invalid_doi_placeholders(
    tmp_path: Path, invalid_doi: str
) -> None:
    """Invalid DOI-like metadata never collapses unrelated Zotero items."""
    export = tmp_path / "zotero.json"
    write_export(
        export,
        [
            zotero_item("A1", DOI=invalid_doi, title="First distinct paper"),
            zotero_item("A2", DOI=invalid_doi, title="Second distinct paper"),
        ],
    )

    records = ZoteroExportConnector(export).search(LiteratureQuery(text="paper"))

    assert [(record.source_id, record.doi) for record in records] == [
        ("zotero:A1", None),
        ("zotero:A2", None),
    ]


def test_zotero_export_matches_normalized_public_metadata_in_export_order(
    tmp_path: Path,
) -> None:
    """Whitespace and case variation do not change metadata matching or order."""
    export = tmp_path / "zotero.json"
    write_export(
        export,
        [
            zotero_item("B2", title="Unrelated study", tags=[{"tag": "Water Policy"}]),
            zotero_item("A1", title="Unrelated study", tags=[{"tag": " Carbon   Pricing "}]),
        ],
    )

    records = ZoteroExportConnector(export).search(LiteratureQuery(tag="carbon pricing"))

    assert [record.connector_keys for record in records] == [("A1",)]


@pytest.mark.parametrize(
    "doi_query",
    [
        "https://doi.org/10.1000/example",
        "doi:10.1000/example",
        "http://dx.doi.org/10.1000/example",
    ],
)
def test_zotero_export_normalizes_doi_query_prefixes(
    tmp_path: Path, doi_query: str
) -> None:
    """A DOI URL query identifies the same record as its canonical DOI form."""
    export = tmp_path / "zotero.json"
    write_export(export, [zotero_item("A1")])

    records = ZoteroExportConnector(export).search(LiteratureQuery(doi=doi_query))

    assert [record.connector_keys for record in records] == [("A1",)]


@pytest.mark.parametrize(
    "invalid_doi",
    ["N/A", "unknown", "10.1000 /example", "https://doi.org/not-a-doi"],
)
def test_zotero_export_rejects_invalid_doi_filters_even_when_query_is_forged(
    tmp_path: Path, invalid_doi: str
) -> None:
    """Invalid DOI filters cannot match records whose DOI metadata is absent or invalid."""
    export = tmp_path / "zotero.json"
    write_export(
        export,
        [
            zotero_item("A1", DOI=None),
            zotero_item("A2", DOI="unknown"),
            zotero_item("A3", DOI="10.1000/example"),
        ],
    )

    with pytest.raises(ValidationError, match="valid DOI"):
        LiteratureQuery(doi=invalid_doi)

    forged_query = LiteratureQuery(text="carbon").model_copy(
        update={"doi": invalid_doi}
    )
    with pytest.raises(ValidationError, match="valid DOI"):
        ZoteroExportConnector(export).search(forged_query)


def test_zotero_export_keeps_attachment_metadata_without_reading_attachment_path(
    tmp_path: Path,
) -> None:
    """A nonexistent attachment path is metadata only and never opened."""
    export = tmp_path / "zotero.json"
    write_export(export, [zotero_item("A1")])

    records = ZoteroExportConnector(export).search(LiteratureQuery(text="household"))

    attachment = records[0].attachments[0]
    assert attachment.key == "ATTACH-1"
    assert attachment.title == "Publisher PDF"
    assert attachment.content_type == "application/pdf"
    with pytest.raises(ValidationError, match="frozen"):
        attachment.title = "Changed"  # type: ignore[misc]


def test_attachment_path_values_never_change_parsing_or_returned_metadata(
    tmp_path: Path,
) -> None:
    """Arbitrary path values are ignored rather than parsed or exposed."""
    first_export = tmp_path / "first.json"
    second_export = tmp_path / "second.json"
    first_item = zotero_item("A1")
    second_item = zotero_item("A1")
    first_item["attachments"] = [
        {"key": "ATTACH-1", "title": "Publisher PDF", "path": "TOP-SECRET-PATH"}
    ]
    second_item["attachments"] = [
        {
            "key": "ATTACH-1",
            "title": "Publisher PDF",
            "path": {"nested": ["TOP-SECRET-PATH"]},
        }
    ]
    write_export(first_export, [first_item])
    write_export(second_export, [second_item])

    first = ZoteroExportConnector(first_export).search(LiteratureQuery(text="carbon"))
    second = ZoteroExportConnector(second_export).search(LiteratureQuery(text="carbon"))

    assert first == second
    assert "TOP-SECRET-PATH" not in first[0].model_dump_json()


@pytest.mark.parametrize(
    "payload",
    [
        {"not": "an array"},
        [zotero_item("A1", itemType="note")],
        [zotero_item("A1", creators=["Ada Lovelace"])],
        [zotero_item("A1"), zotero_item("A1", title="Different")],
    ],
)
def test_zotero_export_rejects_invalid_shapes_with_non_secret_diagnostic(
    tmp_path: Path, payload: object
) -> None:
    """Malformed metadata becomes an explicit connector failure, never partial output."""
    export = tmp_path / "zotero.json"
    export.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ConnectorUnavailable, match="invalid metadata"):
        ZoteroExportConnector(export).search(LiteratureQuery(text="carbon"))


@pytest.mark.parametrize(
    "export_text",
    [
        '[{"TOP-SECRET-FIELD":"first","TOP-SECRET-FIELD":"second"}]',
        json.dumps([zotero_item("TOP-SECRET-KEY", itemType="TOP-SECRET-TYPE")]),
        json.dumps(
            [
                zotero_item("TOP-SECRET-KEY"),
                zotero_item("TOP-SECRET-KEY", title="Conflicting title"),
            ]
        ),
    ],
)
def test_export_diagnostics_and_degraded_coverage_never_echo_export_values(
    tmp_path: Path, export_text: str
) -> None:
    """Public connector failures cannot disclose metadata controlled by an export."""
    export = tmp_path / "zotero.json"
    export.write_text(export_text, encoding="utf-8")
    connector = ZoteroExportConnector(export)

    with pytest.raises(ConnectorUnavailable) as caught:
        connector.search(LiteratureQuery(text="carbon"))
    coverage = literature_gateway().literature_search(
        connector, LiteratureQuery(text="carbon")
    )

    for serialized in (
        str(caught.value),
        json.dumps(caught.value.model_dump(), sort_keys=True),
        coverage.model_dump_json(),
    ):
        assert "TOP-SECRET" not in serialized
    assert coverage.status == "degraded"


def test_zotero_export_rejects_nonfinite_json_values(tmp_path: Path) -> None:
    """Non-standard JSON constants cannot enter the local metadata boundary."""
    export = tmp_path / "zotero.json"
    export.write_text('[{"key": NaN}]', encoding="utf-8")

    with pytest.raises(ConnectorUnavailable, match="invalid JSON"):
        ZoteroExportConnector(export).search(LiteratureQuery(text="carbon"))


def test_zotero_export_rejects_duplicate_json_object_fields(tmp_path: Path) -> None:
    """Ambiguous JSON object fields cannot silently overwrite public metadata."""
    export = tmp_path / "zotero.json"
    export.write_text(
        '[{"key":"A1","key":"A2","itemType":"journalArticle","title":"Carbon"}]',
        encoding="utf-8",
    )

    with pytest.raises(ConnectorUnavailable, match="invalid JSON"):
        ZoteroExportConnector(export).search(LiteratureQuery(text="carbon"))


def test_zotero_export_rejects_nonpositive_leading_year(tmp_path: Path) -> None:
    """A malformed Gregorian year remains a connector failure rather than a model leak."""
    export = tmp_path / "zotero.json"
    write_export(export, [zotero_item("A1", date="0000-01-01")])

    with pytest.raises(ConnectorUnavailable, match="invalid metadata"):
        ZoteroExportConnector(export).search(LiteratureQuery(text="carbon"))


def test_missing_export_returns_explicit_degraded_coverage(tmp_path: Path) -> None:
    """A missing local export becomes deterministic degraded planning coverage."""
    coverage = literature_gateway().literature_search(
        ZoteroExportConnector(tmp_path / "missing.json"), LiteratureQuery(text="water")
    )

    assert coverage.status == "degraded"
    assert coverage.records == ()
    assert coverage.reason_code == "CONNECTOR_UNAVAILABLE"
    assert coverage.connector_reason_code == "EXPORT_MISSING"
    assert "missing.json" not in (coverage.diagnostic or "")


def test_zotero_export_merges_many_doi_duplicates_in_declaration_order(
    tmp_path: Path,
) -> None:
    """A large duplicate group retains every key and unique tag in stable order."""
    export = tmp_path / "zotero.json"
    count = 200
    write_export(
        export,
        [
            zotero_item(
                f"K{index}",
                creators=[
                    {
                        "creatorType": "author",
                        "firstName": "Author",
                        "lastName": str(index),
                    }
                ],
                tags=[{"tag": f"Tag {index}"}],
            )
            for index in range(count)
        ],
    )

    records = ZoteroExportConnector(export).search(LiteratureQuery(text="carbon"))

    assert records[0].connector_keys == tuple(f"K{index}" for index in range(count))
    assert records[0].tags == tuple(f"Tag {index}" for index in range(count))


def test_literature_query_requires_one_nonblank_field() -> None:
    """An empty discovery request cannot silently mean match everything."""
    with pytest.raises(ValidationError, match="at least one"):
        LiteratureQuery()
    with pytest.raises(ValidationError, match="must not be blank"):
        LiteratureQuery(text=" ")
