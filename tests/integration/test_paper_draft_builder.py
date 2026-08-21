"""Production-authority publication tests for evidence-bound paper drafts."""

from __future__ import annotations

from pathlib import Path

import pytest
from paper_draft_integration_fixtures import build_stack

from envresearch.models.artifact import ArtifactRef
from envresearch.paper.draft_builder import PAPER_DRAFT_SUBJECT
from envresearch.paper.errors import PaperAuthorityInvalid, PaperIntegrityInvalid


def test_real_authority_builds_useful_current_draft(tmp_path: Path) -> None:
    stack = build_stack(tmp_path)
    try:
        reference = stack.draft_service.publish(
            stack.candidate,
            map_ref=stack.map_ref,
            ledger_ref=stack.ledger_ref,
            citation_report_ref=stack.report_ref,
        )
        draft = stack.draft_service.status(
            reference, map_ref=stack.map_ref, ledger_ref=stack.ledger_ref
        )
        repeated = stack.draft_service.publish(
            stack.candidate,
            map_ref=stack.map_ref,
            ledger_ref=stack.ledger_ref,
            citation_report_ref=stack.report_ref,
        )

        assert repeated == reference
        assert draft.map_ref == stack.map_ref
        assert draft.ledger_ref == stack.ledger_ref
        assert draft.citation_report_ref == stack.report_ref
        assert tuple(item.section for item in draft.paragraphs) == (
            "title",
            "research-question",
            "methods",
            "results",
            "limitations",
        )
        assert len(draft.tables) == len(draft.figures) == 1
        assert stack.draft_service.registry.current(PAPER_DRAFT_SUBJECT) == reference
    finally:
        stack.orchestrator.close()


def test_authority_rejects_noncurrent_exact_report(tmp_path: Path) -> None:
    stack = build_stack(tmp_path)
    stale = ArtifactRef(
        artifact_id="citation-integrity-report",
        artifact_version=1,
        content_hash="f" * 64,
    )
    try:
        with pytest.raises(PaperAuthorityInvalid, match="current"):
            stack.citation_authority.reopen(stale)
    finally:
        stack.orchestrator.close()


def test_current_publication_failure_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = build_stack(tmp_path)
    original = stack.draft_service.registry.set_current
    calls = 0

    def fail_once(subject: str, reference: ArtifactRef) -> None:
        nonlocal calls
        calls += 1
        if subject == PAPER_DRAFT_SUBJECT and calls == 1:
            raise OSError("injected current failure")
        original(subject, reference)

    try:
        monkeypatch.setattr(stack.draft_service.registry, "set_current", fail_once)
        with pytest.raises(PaperIntegrityInvalid, match="publication"):
            stack.draft_service.publish(
                stack.candidate,
                map_ref=stack.map_ref,
                ledger_ref=stack.ledger_ref,
                citation_report_ref=stack.report_ref,
            )
        assert stack.draft_service.registry.current(PAPER_DRAFT_SUBJECT) is None

        monkeypatch.setattr(stack.draft_service.registry, "set_current", original)
        reference = stack.draft_service.publish(
            stack.candidate,
            map_ref=stack.map_ref,
            ledger_ref=stack.ledger_ref,
            citation_report_ref=stack.report_ref,
        )
        assert (
            stack.draft_service.status(
                reference, map_ref=stack.map_ref, ledger_ref=stack.ledger_ref
            ).draft_id
            == reference.artifact_id
        )
    finally:
        stack.orchestrator.close()
