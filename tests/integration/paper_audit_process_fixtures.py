"""Spawn-safe audit workers over exact local paper authorities."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from paper_claim_fixtures import ProcessResolver
from paper_draft_process_fixtures import ProcessCitationAuthority

from envresearch.benchmarks.claim_report import report_from_payload
from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.econometrics.report import LocalAnalysisReference, LocalAnalysisReport
from envresearch.econometrics.valuation_authority import VALUATION_AUTHORITY_SUBJECT
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_claims import CuratorSourceSheet
from envresearch.paper._audit_store import audit_commit_subject
from envresearch.paper.argument_map import ArgumentMapService
from envresearch.paper.auditor import PaperAuditService, audit_subject
from envresearch.paper.citation_authority import (
    CitationAuthoritySnapshot,
    CitationGenerationToken,
)
from envresearch.paper.draft_builder import DraftService
from envresearch.paper.errors import PaperBuilderError
from envresearch.paper.ledger import ClaimLedgerService


def audit_worker(
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
    draft_json: str,
    crash_point: str | None,
    start: Any,
    results: Any,
) -> None:
    """Publish one independent audit and return its exact typed outcome."""
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
    draft_service = DraftService(map_service=map_service, citation_authority=authority)
    service = PaperAuditService(draft_service=draft_service)
    draft_ref = ArtifactRef.model_validate_json(draft_json)
    if crash_point:
        original = service.registry.set_current

        def die_at_current(subject: str, reference: ArtifactRef) -> None:
            if subject == audit_subject(draft_ref) and crash_point == "before-current":
                os._exit(74)
            original(subject, reference)
            if subject == audit_subject(draft_ref) and crash_point == "after-current":
                os._exit(75)
            if (
                subject == audit_commit_subject(draft_ref)
                and crash_point == "after-commit"
            ):
                os._exit(76)

        service.registry.set_current = die_at_current  # type: ignore[method-assign]
    start.wait()
    try:
        reference = service.audit(draft_ref)
        results.put(("ok", reference.model_dump_json()))
    except PaperBuilderError as error:
        results.put((error.code, str(error)))
    except Exception as error:  # noqa: BLE001 - child returns contention outcome
        results.put((type(error).__name__, str(error)))


def transition_writer_boundary_worker(
    run_root: str,
    start: Any,
    attempting: Any,
    acquired: Any,
    release: Any,
    done: Any,
) -> None:
    """Enter the exact complete-chain lock used by every V0.3.1 writer."""
    runner = ExitRegistry(Path(run_root) / "runner", create=False)
    start.wait()
    attempting.set()
    with runner.lock(VALUATION_AUTHORITY_SUBJECT):
        acquired.set()
        release.wait()
    done.set()


__all__ = [
    "audit_worker",
    "transition_writer_boundary_worker",
]
