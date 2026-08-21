"""Typed exact-citation boundary for V0.4 draft construction."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Protocol

from envresearch.benchmarks.blind_registry import LoadedBlindCase
from envresearch.benchmarks.claim_report import (
    CitationIntegrityReport,
    report_from_payload,
    report_payload,
)
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_claims import CuratorSourceSheet
from envresearch.paper.errors import PaperAuthorityInvalid, PaperIntegrityInvalid
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.citation_attestations import (
    ProtectedCitationAttestations,
    SourceGenerationAnchor,
)
from envresearch.research.citation_gate import (
    REPORT_PATH,
    require_current_citation_report,
)
from envresearch.research.citation_sources import load_registry_cases


@dataclass(frozen=True, slots=True)
class CitationGenerationToken:
    """Authenticated citation generation identity for a final linearization check."""

    report_ref: ArtifactRef
    report_payload_sha256: str
    source_generation: int
    source_anchor_sha256: str


@dataclass(frozen=True, slots=True)
class CitationAuthoritySnapshot:
    """One current sealed report and its freshly authenticated source sheets."""

    report: tuple[ArtifactRef, CitationIntegrityReport]
    source_sheets: tuple[tuple[ArtifactRef, CuratorSourceSheet], ...]
    token: CitationGenerationToken


class CitationAuthority(Protocol):
    """Reopen one caller-supplied exact citation report authority."""

    def authority_lease(self) -> AbstractContextManager[None]: ...

    def reopen(self, report_ref: ArtifactRef) -> CitationAuthoritySnapshot: ...

    def require_current(self, token: CitationGenerationToken) -> None: ...


class LifecycleCitationAuthority:
    """Reopen lifecycle-sealed reports against fresh protected source catalogs."""

    def __init__(
        self,
        *,
        lifecycle: ResearchArtifactLifecycle,
        attestations: ProtectedCitationAttestations,
    ) -> None:
        self.lifecycle = lifecycle
        self.attestations = attestations

    @contextmanager
    def authority_lease(self) -> Iterator[None]:
        """Exclude every citation generation writer for one paper transaction."""
        with self.attestations.queue.control.transaction_lock("mutation"):
            yield

    def reopen(self, report_ref: ArtifactRef) -> CitationAuthoritySnapshot:
        """Require one exact current report and independently reload every source."""
        report = self._report_payload()
        latest = self._latest_sources()
        self._require_attested_report(report_ref, report)
        current_ref = self._current_report()
        if current_ref != report_ref:
            raise PaperAuthorityInvalid(
                "citation report reference is not current",
                finding_kind="citation-report-not-current",
            )
        cases, roots = self._fresh_cases()
        _require_fresh_source_generation(cases, roots, latest)
        source_sheets = tuple(
            sorted(
                ((case.source_ref, case.source_sheet) for case in cases),
                key=lambda item: str(item[0]),
            )
        )
        if tuple(ref for ref, _ in source_sheets) != report.source_sheet_refs:
            raise PaperAuthorityInvalid(
                "citation source sheets do not match the sealed current report",
                finding_kind="citation-source-not-current",
            )
        if self._current_report() != report_ref:
            raise PaperAuthorityInvalid(
                "citation report changed during reopening",
                finding_kind="citation-report-not-current",
            )
        if self._report_payload() != report:
            raise PaperIntegrityInvalid(
                "citation report changed during reopening",
                finding_kind="citation-report-reconstruction-mismatch",
            )
        final_cases, final_roots = self._fresh_cases()
        final_latest = self._latest_sources()
        _require_fresh_source_generation(final_cases, final_roots, final_latest)
        if (final_cases, final_roots, final_latest) != (cases, roots, latest):
            raise PaperAuthorityInvalid(
                "citation source changed during reopening",
                finding_kind="citation-source-not-current",
            )
        if self._current_report() != report_ref:
            raise PaperAuthorityInvalid(
                "citation report changed during reopening",
                finding_kind="citation-report-not-current",
            )
        source_sheets = tuple(
            sorted(
                ((case.source_ref, case.source_sheet) for case in final_cases),
                key=lambda item: str(item[0]),
            )
        )
        return CitationAuthoritySnapshot(
            report=(report_ref, report),
            source_sheets=source_sheets,
            token=_generation_token(report_ref, report, final_latest),
        )

    def require_current(self, token: CitationGenerationToken) -> None:
        """Reopen protected authority and require the exact captured generation."""
        if self.reopen(token.report_ref).token != token:
            raise PaperAuthorityInvalid(
                "citation source generation changed during draft validation",
                finding_kind="citation-source-not-current",
            )

    def _current_report(self) -> ArtifactRef:
        try:
            return require_current_citation_report(self.lifecycle, self.attestations)
        except (OSError, TypeError, ValueError) as exc:
            raise PaperAuthorityInvalid(
                "citation report authority is not current",
                finding_kind="citation-report-not-current",
            ) from exc

    def _report_payload(self) -> CitationIntegrityReport:
        try:
            artifact = self.lifecycle.read_artifact(REPORT_PATH)
            return report_from_payload(artifact.payload)
        except (OSError, TypeError, ValueError) as exc:
            raise PaperIntegrityInvalid(
                "citation report bytes or payload are invalid",
                finding_kind="citation-report-bytes-invalid",
            ) from exc

    def _fresh_cases(self) -> tuple[tuple[LoadedBlindCase, ...], tuple[str, ...]]:
        try:
            return load_registry_cases(self.attestations.authorized_catalog_roots)
        except (OSError, TypeError, ValueError) as exc:
            raise PaperAuthorityInvalid(
                "citation source catalog is not current",
                finding_kind="citation-source-not-current",
            ) from exc

    def _latest_sources(self) -> SourceGenerationAnchor:
        try:
            latest = self.attestations.latest_sources(required=True)
        except FileNotFoundError as exc:
            raise PaperAuthorityInvalid(
                "citation source attestation is not current",
                finding_kind="citation-source-not-current",
            ) from exc
        except (OSError, TypeError, ValueError) as exc:
            raise PaperIntegrityInvalid(
                "citation source attestation bytes are invalid",
                finding_kind="citation-attestation-invalid",
            ) from exc
        assert latest is not None
        return latest

    def _require_attested_report(
        self, report_ref: ArtifactRef, report: CitationIntegrityReport
    ) -> None:
        try:
            self.attestations.require_current_report(report_ref, report)
        except ValueError as exc:
            if str(exc) in {
                "citation report authentication is missing",
                "citation report authentication is not current",
            }:
                raise PaperAuthorityInvalid(
                    "citation report attestation is not current",
                    finding_kind="citation-report-not-current",
                ) from exc
            raise PaperIntegrityInvalid(
                "citation report attestation bytes are invalid",
                finding_kind="citation-attestation-invalid",
            ) from exc
        except (OSError, TypeError) as exc:
            raise PaperIntegrityInvalid(
                "citation report attestation bytes are invalid",
                finding_kind="citation-attestation-invalid",
            ) from exc


def _require_fresh_source_generation(
    cases: tuple[LoadedBlindCase, ...],
    roots: tuple[str, ...],
    latest: SourceGenerationAnchor,
) -> None:
    ordered = tuple(sorted(cases, key=lambda item: item.source_sheet.case_id))
    expected: dict[str, object] = {
        "catalog_roots": roots,
        "case_ids": tuple(item.source_sheet.case_id for item in ordered),
        "source_generations": tuple(
            item.source_sheet.source_generation for item in ordered
        ),
        "case_ref_sha256s": tuple(_case_ref_sha256(item) for item in ordered),
        "source_sheet_refs": tuple(
            sorted((item.source_ref for item in ordered), key=str)
        ),
        "claim_fact_map_refs": tuple(
            sorted((item.claim_fact_map_ref for item in ordered), key=str)
        ),
        "blinded_brief_refs": tuple(
            sorted((item.brief_ref for item in ordered), key=str)
        ),
    }
    if any(getattr(latest, field) != value for field, value in expected.items()):
        raise PaperAuthorityInvalid(
            "citation source generation is stale",
            finding_kind="citation-source-not-current",
        )


def _case_ref_sha256(case: LoadedBlindCase) -> str:
    payload = {
        "source_sheet_ref": case.source_ref.model_dump(mode="json"),
        "claim_fact_map_ref": case.claim_fact_map_ref.model_dump(mode="json"),
        "blinded_brief_ref": case.brief_ref.model_dump(mode="json"),
    }
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()


def _generation_token(
    report_ref: ArtifactRef,
    report: CitationIntegrityReport,
    source: SourceGenerationAnchor,
) -> CitationGenerationToken:
    return CitationGenerationToken(
        report_ref=report_ref,
        report_payload_sha256=_payload_sha256(report_payload(report)),
        source_generation=source.generation,
        source_anchor_sha256=_payload_sha256(source.model_dump(mode="json")),
    )


def _payload_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()


__all__ = [
    "CitationAuthority",
    "CitationAuthoritySnapshot",
    "CitationGenerationToken",
    "LifecycleCitationAuthority",
]
