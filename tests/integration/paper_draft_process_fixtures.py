"""Spawn-safe exact authorities for paper-draft transaction tests."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_claim_fixtures import ProcessResolver, ResolverFixture

from envresearch.benchmarks.claim_report import report_from_payload, report_payload
from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.report import LocalAnalysisReference, LocalAnalysisReport
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_claims import CuratorSourceSheet
from envresearch.paper.argument_map import ArgumentMapService
from envresearch.paper.citation_authority import (
    CitationAuthoritySnapshot,
    CitationGenerationToken,
)
from envresearch.paper.draft_builder import PAPER_DRAFT_SUBJECT, DraftService
from envresearch.paper.draft_contracts import PaperDraftCandidate
from envresearch.paper.errors import PaperAuthorityInvalid, PaperBuilderError
from envresearch.paper.ledger import ClaimLedgerService
from envresearch.workers.queue import FilesystemWorkerQueue


@dataclass(frozen=True, slots=True)
class ProcessCitationAuthority:
    """Exact in-memory authority isolated to transaction contention tests."""

    snapshot: CitationAuthoritySnapshot

    def reopen(self, report_ref: ArtifactRef) -> CitationAuthoritySnapshot:
        if report_ref != self.snapshot.report[0]:
            raise PaperAuthorityInvalid(
                "citation report is not current",
                finding_kind="citation-report-not-current",
            )
        return self.snapshot

    def require_current(self, token: CitationGenerationToken) -> None:
        if token != self.snapshot.token:
            raise PaperAuthorityInvalid(
                "citation generation is not current",
                finding_kind="citation-source-not-current",
            )

    @contextmanager
    def authority_lease(self) -> Iterator[None]:
        yield


def process_arguments(stack: Any) -> tuple[str, ...]:
    """Serialize a real local stack into complete spawn-safe authority values."""
    resolver = stack.ledger_service.resolver
    assert isinstance(resolver, ResolverFixture)
    analysis_ref, analysis_report = resolver.reports[0]
    snapshot = stack.citation_authority.reopen(stack.report_ref)
    report_ref, report = snapshot.report
    sources = tuple(
        {
            "reference": reference.model_dump(mode="json"),
            "sheet": sheet.model_dump(mode="json"),
        }
        for reference, sheet in snapshot.source_sheets
    )
    token = {
        "report_ref": snapshot.token.report_ref.model_dump(mode="json"),
        "report_payload_sha256": snapshot.token.report_payload_sha256,
        "source_generation": snapshot.token.source_generation,
        "source_anchor_sha256": snapshot.token.source_anchor_sha256,
    }
    return (
        str(stack.draft_service.registry.root),
        resolver.transition_ref.model_dump_json(),
        analysis_ref.model_dump_json(),
        analysis_report.model_dump_json(),
        stack.ledger_ref.model_dump_json(),
        stack.map_ref.model_dump_json(),
        report_ref.model_dump_json(),
        json.dumps(report_payload(report), separators=(",", ":"), sort_keys=True),
        json.dumps(sources, separators=(",", ":"), sort_keys=True),
        json.dumps(token, separators=(",", ":"), sort_keys=True),
    )


def draft_worker(
    paper_root: str,
    transition_json: str,
    analysis_json: str,
    analysis_report_json: str,
    ledger_json: str,
    map_json: str,
    citation_ref_json: str,
    citation_report_json: str,
    sources_json: str,
    token_json: str,
    candidate_json: str,
    crash_before_current: bool,
    start: Any,
    results: Any,
) -> None:
    """Publish one draft in a spawned process and return its typed outcome."""
    transition_ref = ArtifactRef.model_validate_json(transition_json)
    resolver = ProcessResolver(
        transition_ref=transition_ref,
        analysis_ref=LocalAnalysisReference.model_validate_json(analysis_json),
        report=LocalAnalysisReport.model_validate_json(analysis_report_json),
    )
    ledger_service = ClaimLedgerService.for_resolver(
        paper_root=Path(paper_root), resolver=resolver
    )
    map_service = ArgumentMapService(ledger_service=ledger_service)
    citation_ref = ArtifactRef.model_validate_json(citation_ref_json)
    report = report_from_payload(json.loads(citation_report_json))
    sources = tuple(
        (
            ArtifactRef.model_validate(item["reference"]),
            CuratorSourceSheet.model_validate_json(
                json.dumps(item["sheet"], separators=(",", ":"), sort_keys=True)
            ),
        )
        for item in json.loads(sources_json)
    )
    token_payload = json.loads(token_json)
    token = CitationGenerationToken(
        report_ref=ArtifactRef.model_validate(token_payload["report_ref"]),
        report_payload_sha256=token_payload["report_payload_sha256"],
        source_generation=token_payload["source_generation"],
        source_anchor_sha256=token_payload["source_anchor_sha256"],
    )
    authority = ProcessCitationAuthority(
        CitationAuthoritySnapshot(
            report=(citation_ref, report), source_sheets=sources, token=token
        )
    )
    service = DraftService(map_service=map_service, citation_authority=authority)
    if crash_before_current:
        original = service.registry.set_current

        def die_before_current(subject: str, reference: ArtifactRef) -> None:
            if subject == PAPER_DRAFT_SUBJECT:
                os._exit(73)
            original(subject, reference)

        service.registry.set_current = die_before_current  # type: ignore[method-assign]
    candidate = PaperDraftCandidate.model_validate_json(candidate_json)
    ledger_ref = ArtifactRef.model_validate_json(ledger_json)
    map_ref = ArtifactRef.model_validate_json(map_json)
    start.wait()
    try:
        reference = service.publish(
            candidate,
            map_ref=map_ref,
            ledger_ref=ledger_ref,
            citation_report_ref=citation_ref,
        )
        results.put(("ok", reference.model_dump_json()))
    except PaperBuilderError as error:
        results.put((error.code, str(error)))
    except Exception as error:  # noqa: BLE001 - child reports unexpected contention
        results.put((type(error).__name__, str(error)))


def authority_mutation_worker(
    paper_root: str,
    subject: str,
    start: Any,
    attempting: Any,
    acquired: Any,
    mutate: Any,
    done: Any,
) -> None:
    """Follow one public subject lock before removing its current pointer."""
    registry = ExitRegistry(Path(paper_root))
    start.wait()
    attempting.set()
    with registry.lock(subject):
        acquired.set()
        mutate.wait()
        registry.files.unlink(Path("exit/current") / f"{subject}.json")
    done.set()


def citation_writer_boundary_worker(
    queue_root: str,
    control_root: str,
    start: Any,
    attempting: Any,
    acquired: Any,
    release: Any,
    done: Any,
) -> None:
    """Enter the exact mutation lock used by every citation-generation writer."""
    queue = FilesystemWorkerQueue(
        Path(queue_root), control_root=Path(control_root), require_producer_context=True
    )
    try:
        start.wait()
        attempting.set()
        with queue.control.transaction_lock("mutation"):
            acquired.set()
            release.wait()
        done.set()
    finally:
        queue.close()


__all__ = [
    "authority_mutation_worker",
    "citation_writer_boundary_worker",
    "draft_worker",
    "process_arguments",
]
