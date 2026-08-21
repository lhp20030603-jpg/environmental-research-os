"""Fail-closed binding checks for source-dependent benchmark statements."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping, Sequence
from typing import TypeAlias

from envresearch.benchmarks.blind_claim_semantics import (
    CurrentClaimBinding,
    validate_blind_recommendation,
)
from envresearch.benchmarks.blind_registry import LoadedBlindCase
from envresearch.benchmarks.claim_report import (
    CitationIntegrityFinding,
    CitationIntegrityReport,
    accepted_artifact_binding,
    binding_sha256,
    report_binding_is_valid,
)
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_claims import (
    ClaimFactMap,
    ClaimUsage,
    ClaimVerificationStatus,
    CuratorSourceSheet,
)
from envresearch.models.benchmark_evaluation import AcceptedArtifactClaims

__all__ = [
    "CitationIntegrityFinding",
    "CitationIntegrityReport",
    "CitationIntegrityValidator",
    "report_binding_is_valid",
]

_DOI = re.compile(r"(?i)(?:doi:\s*)?10\.\d{4,9}/[-._;()/:a-z0-9]+")
_AUTHOR_YEAR = re.compile(
    r"\b[A-Z][A-Za-z'’\-]+(?:\s+et al\.)?(?:\s*\(\d{4}[a-z]?\)|,\s*\d{4}[a-z]?)"
)
_QUANTITY = re.compile(r"(?<![A-Za-z-])\d[\d,]*(?:\.\d+)?%?")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ESTIMAND_ID = re.compile(r"^estimand-[a-z0-9]+(?:-[a-z0-9]+)*$")
_RECOMMENDER_ID = re.compile(r"^principal-[a-z0-9][a-z0-9._-]*-recommender$")
_RECOMMENDATION_STRUCTURE = {
    "/blinded_brief_ref/content_hash": _SHA256,
    "/leakage_report_ref/content_hash": _SHA256,
    "/method_profile_registry_sha256": _SHA256,
    "/method_candidates/estimand_ref": _ESTIMAND_ID,
    "/recommender_principal": _RECOMMENDER_ID,
}
_RESERVED_STRUCTURE_FIELDS = frozenset(
    {"content_hash", "method_profile_registry_sha256", "recommender_principal"}
)
JsonPointer: TypeAlias = tuple[str, ...]


class CitationIntegrityValidator:
    """Require every source-dependent leaf to bind one current reviewed claim."""

    validator_version = "claim-integrity-v1"
    blind_validator_version = "blind-claim-integrity-v2"

    def validate_loaded_cases(
        self,
        *,
        cases: tuple[LoadedBlindCase, ...],
        artifacts: tuple[AcceptedArtifactClaims, ...],
    ) -> CitationIntegrityReport:
        """Validate registry-loaded cases without detaching their exact refs."""
        return self.validate(
            source_sheets=tuple(item.source_sheet for item in cases),
            fact_maps=tuple(item.claim_fact_map for item in cases),
            artifacts=artifacts,
            source_sheet_refs=tuple(item.source_ref for item in cases),
            claim_fact_map_refs=tuple(item.claim_fact_map_ref for item in cases),
            blinded_brief_refs=tuple(item.brief_ref for item in cases),
        )

    def validate(
        self,
        *,
        source_sheets: tuple[CuratorSourceSheet, ...],
        fact_maps: tuple[ClaimFactMap, ...],
        artifacts: tuple[AcceptedArtifactClaims, ...],
        source_sheet_refs: tuple[ArtifactRef, ...] = (),
        claim_fact_map_refs: tuple[ArtifactRef, ...] = (),
        blinded_brief_refs: tuple[ArtifactRef, ...] = (),
    ) -> CitationIntegrityReport:
        """Validate exact claim usage bindings without network or data access."""
        _require_exact_coverage(
            source_sheets,
            fact_maps,
            artifacts,
            source_sheet_refs,
            claim_fact_map_refs,
            blinded_brief_refs,
        )
        claims = self._current_claims(
            source_sheets, fact_maps, source_sheet_refs, blinded_brief_refs
        )
        findings: list[CitationIntegrityFinding] = []
        for artifact in artifacts:
            findings.extend(self._validate_artifact(artifact, claims))
        artifact_refs = tuple(
            sorted((item.artifact_ref for item in artifacts), key=str)
        )
        artifact_bindings = tuple(
            sorted(
                (accepted_artifact_binding(item) for item in artifacts),
                key=lambda item: str(item.artifact_ref),
            )
        )
        validator_version = (
            self.blind_validator_version
            if any(
                item.artifact_ref.artifact_id == "method-recommendation"
                for item in artifacts
            )
            else self.validator_version
        )
        binding = binding_sha256(
            source_sheet_refs,
            claim_fact_map_refs,
            blinded_brief_refs,
            artifact_bindings,
            validator_version,
        )
        return CitationIntegrityReport(
            findings=tuple(findings),
            passed=not findings,
            validator_version=validator_version,
            source_sheet_refs=tuple(sorted(source_sheet_refs, key=str)),
            claim_fact_map_refs=tuple(sorted(claim_fact_map_refs, key=str)),
            blinded_brief_refs=tuple(sorted(blinded_brief_refs, key=str)),
            accepted_artifact_refs=artifact_refs,
            accepted_artifact_bindings=artifact_bindings,
            binding_sha256=binding,
        )

    @staticmethod
    def _current_claims(
        source_sheets: tuple[CuratorSourceSheet, ...],
        fact_maps: tuple[ClaimFactMap, ...],
        source_sheet_refs: tuple[ArtifactRef, ...],
        blinded_brief_refs: tuple[ArtifactRef, ...],
    ) -> dict[str, CurrentClaimBinding]:
        expected_refs = {
            sheet.case_id: source_sheet_refs[index]
            for index, sheet in enumerate(source_sheets)
            if index < len(source_sheet_refs)
        }
        mapped_facts_by_case: dict[str, dict[str, str]] = {}
        duplicate_cases: set[str] = set()
        for index, fact_map in enumerate(fact_maps):
            if fact_map.case_id in mapped_facts_by_case:
                duplicate_cases.add(fact_map.case_id)
                continue
            source_matches = fact_map.source_sheet_ref == expected_refs.get(
                fact_map.case_id
            )
            brief_matches = fact_map.blinded_brief_ref == blinded_brief_refs[index]
            if not source_matches or not brief_matches:
                mapped_facts_by_case[fact_map.case_id] = {}
                continue
            mapped_facts_by_case[fact_map.case_id] = {
                entry.claim_id: entry.fact_id for entry in fact_map.entries
            }
        for case_id in duplicate_cases:
            mapped_facts_by_case[case_id] = {}
        claims: dict[str, CurrentClaimBinding] = {}
        for sheet in sorted(source_sheets, key=lambda item: item.case_id):
            mapped_facts = mapped_facts_by_case.get(sheet.case_id, {})
            for claim in sorted(sheet.claims, key=lambda item: item.claim_id):
                prior = claims.get(claim.claim_id)
                current = CurrentClaimBinding(
                    claim=claim, fact_id=mapped_facts.get(claim.claim_id)
                )
                if prior is None:
                    claims[claim.claim_id] = current
                else:
                    claims[claim.claim_id] = CurrentClaimBinding(
                        claim=claim, fact_id=None
                    )
        return claims

    def _validate_artifact(
        self,
        artifact: AcceptedArtifactClaims,
        claims: Mapping[str, CurrentClaimBinding],
    ) -> tuple[CitationIntegrityFinding, ...]:
        if artifact.artifact_ref.artifact_id == "method-recommendation":
            return validate_blind_recommendation(
                artifact,
                claims,
                leaves=dict(_string_leaves(artifact.payload)),
                statement_sha256=_statement_sha256,
                is_source_dependent=self._is_source_dependent,
            )
        leaves = dict(_string_leaves(artifact.payload))
        usages_by_pointer: dict[str, list[ClaimUsage]] = {}
        for usage in artifact.usages:
            usages_by_pointer.setdefault(usage.json_pointer, []).append(usage)

        findings: list[CitationIntegrityFinding] = []
        bound_pointers: set[str] = set()
        for pointer in sorted(usages_by_pointer):
            pointer_usages = usages_by_pointer[pointer]
            statement = leaves.get(pointer)
            if statement is None or len(pointer_usages) != 1:
                findings.append(
                    self._finding(
                        "CLAIM_USAGE_UNBOUND",
                        artifact,
                        pointer,
                        None,
                        "claim usage must bind exactly one string leaf",
                    )
                )
                continue
            usage = pointer_usages[0]
            if not self._is_source_dependent(
                pointer, statement, artifact.artifact_ref.artifact_id
            ):
                findings.append(
                    self._finding(
                        "CLAIM_USAGE_UNBOUND",
                        artifact,
                        pointer,
                        usage.claim_id,
                        "claim usage points to a source-independent statement",
                    )
                )
                continue
            if usage.statement_sha256 != _statement_sha256(statement):
                findings.append(
                    self._finding(
                        "CLAIM_USAGE_UNBOUND",
                        artifact,
                        pointer,
                        usage.claim_id,
                        "statement_sha256 does not match the exact leaf value",
                    )
                )
                continue
            bound_pointers.add(pointer)
            current = claims.get(usage.claim_id)
            if (
                current is None
                or current.fact_id is None
                or (current.claim.status is not ClaimVerificationStatus.CLAIM_VERIFIED)
            ):
                findings.append(
                    self._finding(
                        "CLAIM_NOT_CURRENT_VERIFIED",
                        artifact,
                        pointer,
                        usage.claim_id,
                        "claim is missing, unmapped, stale, or not claim_verified",
                    )
                )

        for pointer, statement in sorted(leaves.items()):
            if (
                self._is_source_dependent(
                    pointer, statement, artifact.artifact_ref.artifact_id
                )
                and pointer not in bound_pointers
            ):
                findings.append(
                    self._finding(
                        "SOURCE_STATEMENT_UNBOUND",
                        artifact,
                        pointer,
                        None,
                        "source-dependent statement has no exact claim usage",
                    )
                )
        return tuple(findings)

    @staticmethod
    def _is_source_dependent(
        pointer: str, statement: str, artifact_id: str | None = None
    ) -> bool:
        """Recognize statements which need an independently reviewed source claim."""
        segments = _pointer_segments(pointer)
        grammar = _RECOMMENDATION_STRUCTURE.get(pointer)
        if artifact_id == "method-recommendation" and grammar is not None:
            return grammar.fullmatch(statement) is None
        if grammar is not None or set(segments) & _RESERVED_STRUCTURE_FIELDS:
            return True
        if "evidence_refs" in segments:
            return True
        normalized = _normalize_markers(statement)
        quantity = _QUANTITY.search(normalized)
        quantity_needs_claim = quantity and (
            artifact_id == "method-recommendation"
            or any(character.isalpha() for character in normalized)
        )
        return bool(
            _DOI.search(normalized) or _AUTHOR_YEAR.search(normalized) or quantity_needs_claim
        )

    @staticmethod
    def _finding(
        code: str,
        artifact: AcceptedArtifactClaims,
        pointer: str,
        claim_id: str | None,
        detail: str,
    ) -> CitationIntegrityFinding:
        return CitationIntegrityFinding(
            code=code,
            artifact_ref=artifact.artifact_ref,
            json_pointer=pointer,
            claim_id=claim_id,
            detail=detail,
        )


def _string_leaves(
    payload: object, pointer: JsonPointer = ()
) -> Iterator[tuple[str, str]]:
    """Yield string leaves in a canonical RFC 6901 pointer order."""
    if isinstance(payload, str):
        yield _pointer(pointer), payload
    elif isinstance(payload, Mapping):
        for key in sorted(payload, key=lambda item: str(item)):
            if not isinstance(key, str):
                raise TypeError(
                    "accepted artifact payload mappings must have string keys"
                )
            yield from _string_leaves(payload[key], (*pointer, key))
    elif isinstance(payload, Sequence) and not isinstance(payload, (bytes, bytearray)):
        for index, value in enumerate(payload):
            yield from _string_leaves(value, (*pointer, str(index)))


def _pointer(segments: JsonPointer) -> str:
    return (
        "" if not segments else "/" + "/".join(_escape(segment) for segment in segments)
    )


def _pointer_segments(pointer: str) -> tuple[str, ...]:
    if not pointer:
        return ()
    return tuple(
        segment.replace("~1", "/").replace("~0", "~")
        for segment in pointer[1:].split("/")
    )


def _escape(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def _normalize_markers(statement: str) -> str:
    """Canonicalize presentation-only DOI and author-year whitespace for detection."""
    return " ".join(statement.split())


def _statement_sha256(statement: str) -> str:
    return hashlib.sha256(statement.encode("utf-8")).hexdigest()


def _require_exact_coverage(
    source_sheets: tuple[CuratorSourceSheet, ...],
    fact_maps: tuple[ClaimFactMap, ...],
    artifacts: tuple[AcceptedArtifactClaims, ...],
    source_refs: tuple[ArtifactRef, ...],
    map_refs: tuple[ArtifactRef, ...],
    brief_refs: tuple[ArtifactRef, ...],
) -> None:
    """Reject incomplete, ambiguous, or replayed immutable generations."""
    if (
        not source_sheets
        or not fact_maps
        or not artifacts
        or len(source_sheets) != len(source_refs)
        or len(fact_maps) != len(map_refs)
        or len(fact_maps) != len(brief_refs)
    ):
        raise ValueError("citation validation requires exact generation coverage")
    for refs, label in (
        (source_refs, "source sheet refs"),
        (map_refs, "claim fact map refs"),
        (tuple(item.artifact_ref for item in artifacts), "accepted artifact refs"),
    ):
        if len(refs) != len(set(refs)):
            raise ValueError(f"{label} must be nonempty and unique")
    expected_sources = {
        sheet.case_id: source_refs[index] for index, sheet in enumerate(source_sheets)
    }
    for index, fact_map in enumerate(fact_maps):
        if fact_map.source_sheet_ref != expected_sources.get(fact_map.case_id):
            raise ValueError("claim fact map does not bind the current source sheet")
        if fact_map.blinded_brief_ref != brief_refs[index]:
            raise ValueError("claim fact map does not bind the current blinded brief")
