"""Fail-closed detection of source identity leakage in blinded briefs."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal, TypeAlias
from urllib.parse import unquote

from envresearch.benchmarks.blind_public_projection import (
    PUBLIC_BRIEF_EXCLUDED_FIELDS,
    public_brief_payload,
)
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_blinding import (
    BlindedBrief,
    LeakageCategory,
    LeakageFinding,
    LeakageReport,
    LeakageSeverity,
)
from envresearch.models.benchmark_claims import CuratorSourceSheet, RestrictedTerm

__all__ = ["LeakageScanner"]

JsonPointer: TypeAlias = tuple[str, ...]

_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE)
_DOI_PREFIX = re.compile(r"^(?:https?://)?(?:dx\.)?doi\.org/|^doi:\s*", re.IGNORECASE)
_NON_TOKEN = re.compile(r"[^\w]+", re.UNICODE)
_HIDDEN_METADATA_KEYS = frozenset(
    {
        "authors",
        "doi",
        "method_family",
        "source_content_hash",
        "source_generation",
        "title",
        "zotero_attachment_key",
        "zotero_item_key",
    }
)
_FIELD_CATEGORIES = {
    "policy_setting": LeakageCategory.IDENTITY,
    "population": LeakageCategory.IDENTITY,
    "unit": LeakageCategory.IDENTITY,
    "data_structures": LeakageCategory.DATASET,
    "available_variables": LeakageCategory.DATASET,
    "institutional_rules": LeakageCategory.METHOD,
    "constraints": LeakageCategory.RESULT,
    "candidate_outcomes": LeakageCategory.RESULT,
}
_CONFIGURATION = {
    "hidden_metadata_keys": sorted(_HIDDEN_METADATA_KEYS),
    "normalization": "NFKC-casefold-punctuation-whitespace-collapse",
    "phrase_ngram_tokens": 8,
    "public_brief_excluded_fields": sorted(PUBLIC_BRIEF_EXCLUDED_FIELDS),
    "scanner_version": "blind-leakage-v2",
}


class LeakageScanner:
    """Reject only configured source identifiers and phrase fingerprints."""

    scanner_version: Literal["blind-leakage-v2"] = "blind-leakage-v2"
    scanner_config_sha256 = hashlib.sha256(
        json.dumps(_CONFIGURATION, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()

    def scan(
        self,
        source: CuratorSourceSheet,
        brief: BlindedBrief,
        source_ref: ArtifactRef,
        brief_ref: ArtifactRef,
        validator_principal: str,
    ) -> LeakageReport:
        """Return an immutable, fail-closed verdict for one exact artifact pair."""
        serialized_brief = public_brief_payload(brief)
        fields = tuple(_walk_strings(serialized_brief))
        findings = self._hidden_metadata_findings(fields)
        findings.extend(self._doi_findings(source, fields))
        findings.extend(self._restricted_term_findings(source.restricted_terms, fields))
        findings.extend(self._distinctive_phrase_findings(source, fields))
        open_findings = _unique_findings(findings)
        return LeakageReport(
            source_sheet_ref=source_ref,
            blinded_brief_ref=brief_ref,
            findings=tuple(open_findings),
            verdict="pass" if not open_findings else "rejected",
            validator_principal=validator_principal,
            scanner_version=self.scanner_version,
            scanner_config_sha256=self.scanner_config_sha256,
            checked_at=datetime.now(UTC),
        )

    def _hidden_metadata_findings(
        self, fields: tuple[tuple[JsonPointer, str, bool], ...]
    ) -> list[LeakageFinding]:
        """Reject serialized field names reserved for curator-only provenance."""
        return [
            _finding(LeakageCategory.HIDDEN_METADATA, locator)
            for locator, text, is_key in fields
            if is_key and _normalize(text).replace(" ", "_") in _HIDDEN_METADATA_KEYS
        ]

    def _doi_findings(
        self,
        source: CuratorSourceSheet,
        fields: tuple[tuple[JsonPointer, str, bool], ...],
    ) -> list[LeakageFinding]:
        """Reject the source DOI regardless of presentation prefix or encoding."""
        source_doi = _canonical_doi(source.doi)
        return [
            _finding(LeakageCategory.CITATION, locator)
            for locator, text, _ in fields
            if source_doi in _canonical_dois(text)
        ]

    def _restricted_term_findings(
        self,
        restricted_terms: tuple[RestrictedTerm, ...],
        fields: tuple[tuple[JsonPointer, str, bool], ...],
    ) -> list[LeakageFinding]:
        """Apply curator-configured source terms without blocking unrelated facts."""
        normalized_terms = tuple(_normalize(item.term) for item in restricted_terms)
        findings: list[LeakageFinding] = []
        for locator, text, is_key in fields:
            if is_key or not any(term in _normalize(text) for term in normalized_terms):
                continue
            findings.append(_finding(_category_for(locator), locator))
        return findings

    def _distinctive_phrase_findings(
        self,
        source: CuratorSourceSheet,
        fields: tuple[tuple[JsonPointer, str, bool], ...],
    ) -> list[LeakageFinding]:
        """Compare every eight-token normalized brief n-gram by hash only."""
        source_hashes = frozenset(source.distinctive_phrase_hashes)
        findings: list[LeakageFinding] = []
        for locator, text, is_key in fields:
            if is_key:
                continue
            if source_hashes.intersection(_ngram_hashes(text)):
                findings.append(_finding(LeakageCategory.PHRASE, locator))
        return findings


def _walk_strings(
    value: object, pointer: JsonPointer = ()
) -> Iterator[tuple[JsonPointer, str, bool]]:
    """Yield all JSON keys and string values with RFC 6901-compatible locators."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            child = pointer + (key_text,)
            yield child, key_text, True
            yield from _walk_strings(item, child)
    elif isinstance(value, Sequence) and not isinstance(value, str):
        for index, item in enumerate(value):
            yield from _walk_strings(item, pointer + (str(index),))
    elif isinstance(value, str):
        yield pointer, value, False


def _normalize(value: str) -> str:
    """Canonicalize equivalent Unicode, case, punctuation, and whitespace."""
    decoded = _decode_url(value)
    normalized = unicodedata.normalize("NFKC", decoded).casefold()
    return " ".join(_NON_TOKEN.sub(" ", normalized).split())


def _decode_url(value: str) -> str:
    """Decode nested percent encoding before all identity comparisons."""
    decoded = value
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded


def _canonical_doi(value: str) -> str:
    """Reduce a DOI presentation to the canonical source comparison value."""
    decoded = _decode_url(value).strip()
    match = _DOI.search(_DOI_PREFIX.sub("", decoded))
    return match.group(0).casefold() if match else ""


def _canonical_dois(value: str) -> frozenset[str]:
    """Extract all DOI-shaped values after decoding prefixes and URLs."""
    decoded = _decode_url(value)
    candidates = (match.group(0).casefold() for match in _DOI.finditer(decoded))
    return frozenset(
        variant
        for candidate in candidates
        for variant in _doi_presentation_variants(candidate)
    )


def _doi_presentation_variants(candidate: str) -> tuple[str, ...]:
    """Retain every sentence-terminator suffix length for exact DOI comparison."""
    variants = [candidate]
    while variants[-1][-1] in ".,;:!?":
        variants.append(variants[-1][:-1])
    return tuple(variant for variant in variants if variant)


def _ngram_hashes(value: str) -> frozenset[str]:
    """Hash exactly eight normalized tokens, retaining no source quotation."""
    tokens = _normalize(value).split()
    return frozenset(
        hashlib.sha256(" ".join(tokens[index : index + 8]).encode()).hexdigest()
        for index in range(len(tokens) - 7)
    )


def _category_for(locator: JsonPointer) -> LeakageCategory:
    """Classify a configured match from its disclosed brief field only."""
    return _FIELD_CATEGORIES.get(locator[0], LeakageCategory.IDENTITY)


def _finding(category: LeakageCategory, locator: JsonPointer) -> LeakageFinding:
    """Create an unresolved finding without retaining leaked source text."""
    return LeakageFinding(
        category=category,
        severity=LeakageSeverity.HIGH,
        locator=_json_pointer(locator),
        disposition="Remove the source-identifying content.",
    )


def _json_pointer(parts: JsonPointer) -> str:
    """Serialize a JSON pointer while preserving escaped literal segments."""
    return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def _unique_findings(findings: list[LeakageFinding]) -> list[LeakageFinding]:
    """Keep the report deterministic when multiple checks find one field."""
    unique = {(item.category, item.locator): item for item in findings}
    return [unique[key] for key in sorted(unique, key=lambda item: (item[0], item[1]))]
