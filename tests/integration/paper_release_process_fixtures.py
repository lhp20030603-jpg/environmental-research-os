"""Spawn-safe workers for Paper Builder release publication tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from paper_claim_fixtures import ProcessResolver
from paper_draft_process_fixtures import ProcessCitationAuthority

from envresearch.benchmarks.claim_report import report_from_payload
from envresearch.econometrics.report import LocalAnalysisReference, LocalAnalysisReport
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_claims import CuratorSourceSheet
from envresearch.paper.argument_map import ArgumentMapService
from envresearch.paper.auditor import PaperAuditService
from envresearch.paper.citation_authority import (
    CitationAuthoritySnapshot,
    CitationGenerationToken,
)
from envresearch.paper.draft_builder import DraftService
from envresearch.paper.errors import PaperBuilderError
from envresearch.paper.ledger import ClaimLedgerService
from envresearch.paper.release import (
    PAPER_RELEASE_PENDING_SUBJECT,
    PAPER_RELEASE_SUBJECT,
    PaperReleaseService,
)

CrashPoint = Literal["release-object", "release-pending", "release-current"]
CRASH_EXIT_CODES: dict[CrashPoint, int] = {
    "release-object": 81,
    "release-pending": 82,
    "release-current": 83,
}


def release_worker(
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
    audit_json: str,
    draft_json: str,
    crash_point: CrashPoint | None,
    start: Any,
    results: Any,
) -> None:
    """Build one exact release, optionally dying after a durable boundary."""
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
    citation_report = report_from_payload(json.loads(citation_report_json))
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
            report=(citation_ref, citation_report),
            source_sheets=sources,
            token=token,
        )
    )
    drafts = DraftService(map_service=map_service, citation_authority=authority)
    service = PaperReleaseService(audit_service=PaperAuditService(draft_service=drafts))
    audit_ref = ArtifactRef.model_validate_json(audit_json)
    draft_ref = ArtifactRef.model_validate_json(draft_json)
    if crash_point == "release-object":
        original_publish = service.registry.publish

        def crash_after_object(artifact_id, payload, *, version=1):  # type: ignore[no-untyped-def]
            reference = original_publish(artifact_id, payload, version=version)
            if artifact_id.startswith("paper-release-"):
                os._exit(CRASH_EXIT_CODES["release-object"])
            return reference

        service.registry.publish = crash_after_object  # type: ignore[method-assign]
    elif crash_point in {"release-pending", "release-current"}:
        original_set = service.registry.set_current

        def crash_after_pointer(subject, reference):  # type: ignore[no-untyped-def]
            original_set(subject, reference)
            if (
                subject == PAPER_RELEASE_PENDING_SUBJECT
                and crash_point == "release-pending"
            ):
                os._exit(CRASH_EXIT_CODES["release-pending"])
            if subject == PAPER_RELEASE_SUBJECT and crash_point == "release-current":
                os._exit(CRASH_EXIT_CODES["release-current"])

        service.registry.set_current = crash_after_pointer  # type: ignore[method-assign]
    start.wait()
    try:
        reference = service.build(audit_ref, draft_ref)
        results.put(("ok", reference.model_dump_json()))
    except PaperBuilderError as error:
        results.put((error.code, str(error)))
    except Exception as error:  # noqa: BLE001 - child reports unexpected outcome
        results.put((type(error).__name__, str(error)))


__all__ = ["CRASH_EXIT_CODES", "CrashPoint", "release_worker"]
