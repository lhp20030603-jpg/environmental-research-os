"""Spawn-safe workers for paper-revision contention and crash tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from paper_claim_fixtures import ProcessResolver
from paper_draft_process_fixtures import ProcessCitationAuthority
from pydantic import BaseModel

from envresearch.benchmarks.claim_report import report_from_payload
from envresearch.econometrics.report import LocalAnalysisReference, LocalAnalysisReport
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_claims import CuratorSourceSheet
from envresearch.paper._audit_store import (
    audit_commit_subject,
    audit_id,
    audit_subject,
)
from envresearch.paper._draft_store import PAPER_DRAFT_SUBJECT
from envresearch.paper._revision_draft import successor_draft
from envresearch.paper._revision_store import (
    revision_commit_subject,
    revision_subject,
)
from envresearch.paper.argument_map import ARGUMENT_MAP_SUBJECT, ArgumentMapService
from envresearch.paper.auditor import PaperAuditService
from envresearch.paper.citation_authority import (
    CitationAuthoritySnapshot,
    CitationGenerationToken,
)
from envresearch.paper.draft_builder import DraftService
from envresearch.paper.draft_contracts import PaperDraft, PaperDraftCandidate
from envresearch.paper.errors import PaperBuilderError
from envresearch.paper.ledger import CLAIM_LEDGER_SUBJECT, ClaimLedgerService
from envresearch.paper.revision import RevisionService

CrashPoint = Literal[
    "successor-draft",
    "successor-audit-object",
    "successor-audit-pending",
    "successor-audit-commit",
    "revision-pending",
    "revision-commit",
    "final-draft-cas",
]

CRASH_EXIT_CODES: dict[CrashPoint, int] = {
    "successor-draft": 81,
    "successor-audit-object": 87,
    "successor-audit-pending": 82,
    "successor-audit-commit": 83,
    "revision-pending": 84,
    "revision-commit": 85,
    "final-draft-cas": 86,
}


def _revision_service(
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
) -> RevisionService:
    """Reconstruct the complete serialized authority stack in one child."""
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
    ledger_ref = ArtifactRef.model_validate_json(ledger_json)
    map_ref = ArtifactRef.model_validate_json(map_json)
    if ledger_service.registry.current(CLAIM_LEDGER_SUBJECT) != ledger_ref:
        raise ValueError("serialized claim-ledger authority is not current")
    if ledger_service.registry.current(ARGUMENT_MAP_SUBJECT) != map_ref:
        raise ValueError("serialized argument-map authority is not current")
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
    drafts = DraftService(map_service=map_service, citation_authority=authority)
    return RevisionService(audit_service=PaperAuditService(draft_service=drafts))


def _install_crash(
    service: RevisionService,
    predecessor_ref: ArtifactRef,
    candidate: PaperDraftCandidate,
    crash_point: CrashPoint,
) -> None:
    predecessor = service.drafts.load(predecessor_ref)
    staged = successor_draft(predecessor_ref, predecessor, candidate)
    successor_ref = service.drafts.expected_ref(staged)
    exit_code = CRASH_EXIT_CODES[crash_point]
    if crash_point == "successor-draft":
        original_publish = service.drafts.publish_exact

        def publish_then_die(draft: PaperDraft) -> ArtifactRef:
            reference = original_publish(draft)
            if reference == successor_ref:
                os._exit(exit_code)
            return reference

        service.drafts.publish_exact = publish_then_die  # type: ignore[method-assign]
        return
    if crash_point == "successor-audit-object":
        original_registry_publish = service.registry.publish

        def publish_audit_then_die(
            artifact_id: str, payload: BaseModel, *, version: int = 1
        ) -> ArtifactRef:
            reference = original_registry_publish(artifact_id, payload, version=version)
            if artifact_id == audit_id(successor_ref):
                os._exit(exit_code)
            return reference

        service.registry.publish = publish_audit_then_die  # type: ignore[method-assign]
        return
    target = {
        "successor-audit-pending": audit_subject(successor_ref),
        "successor-audit-commit": audit_commit_subject(successor_ref),
        "revision-pending": revision_subject(predecessor_ref),
        "revision-commit": revision_commit_subject(predecessor_ref),
        "final-draft-cas": PAPER_DRAFT_SUBJECT,
    }[crash_point]
    original_set = service.registry.set_current

    def set_then_die(subject: str, reference: ArtifactRef) -> None:
        original_set(subject, reference)
        if subject == target:
            os._exit(exit_code)

    service.registry.set_current = set_then_die  # type: ignore[method-assign]


def revision_worker(
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
    predecessor_json: str,
    candidate_json: str,
    crash_point: CrashPoint | None,
    start: Any,
    results: Any,
) -> None:
    """Revise in one spawned process and return only a typed public outcome."""
    service = _revision_service(
        paper_root,
        transition_json,
        analysis_json,
        analysis_report_json,
        ledger_json,
        map_json,
        citation_ref_json,
        citation_report_json,
        sources_json,
        token_json,
    )
    predecessor_ref = ArtifactRef.model_validate_json(predecessor_json)
    candidate = PaperDraftCandidate.model_validate_json(candidate_json)
    if crash_point is not None:
        _install_crash(service, predecessor_ref, candidate, crash_point)
    start.wait()
    try:
        reference = service.revise(predecessor_ref, candidate)
        results.put(("ok", reference.model_dump_json()))
    except PaperBuilderError as error:
        results.put((error.code, str(error)))
    except Exception as error:  # noqa: BLE001 - child reports unexpected failures
        results.put((type(error).__name__, str(error)))


__all__ = ["CRASH_EXIT_CODES", "CrashPoint", "revision_worker"]
