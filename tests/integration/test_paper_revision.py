"""Generation-aware immutable draft storage before revision orchestration."""

from __future__ import annotations

from pathlib import Path

import pytest
from paper_draft_integration_fixtures import DraftStack, build_stack

from envresearch.paper.auditor import PaperAuditService
from envresearch.paper.errors import (
    PaperAuthorityInvalid,
    PaperIntegrityInvalid,
    PaperSupportInvalid,
)
from envresearch.paper.revision import RevisionService


def _candidate_with_title(stack: DraftStack, text: str):  # type: ignore[no-untyped-def]
    title = stack.candidate.paragraphs[0].model_copy(update={"text": text})
    return type(stack.candidate).model_validate(
        {
            **stack.candidate.model_dump(mode="python"),
            "paragraphs": (title, *stack.candidate.paragraphs[1:]),
        }
    )


def _candidate_with_question(
    stack: DraftStack,
    candidate,
    text: str,  # type: ignore[no-untyped-def]
):
    question = candidate.paragraphs[1].model_copy(update={"text": text})
    return type(candidate).model_validate(
        {
            **candidate.model_dump(mode="python"),
            "paragraphs": (
                candidate.paragraphs[0],
                question,
                *candidate.paragraphs[2:],
            ),
        }
    )


def _publish(stack: DraftStack, candidate):  # type: ignore[no-untyped-def]
    return stack.draft_service.publish(
        candidate,
        map_ref=stack.map_ref,
        ledger_ref=stack.ledger_ref,
        citation_report_ref=stack.report_ref,
    )


def _blocked_predecessor(
    stack: DraftStack,
) -> tuple[PaperAuditService, object, object]:
    predecessor = _publish(
        stack,
        _candidate_with_title(
            stack, "Regulators ought to implement this program nationwide."
        ),
    )
    audits = PaperAuditService(draft_service=stack.draft_service)
    audit_ref = audits.audit(predecessor)
    report = audits.status(audit_ref, predecessor)
    assert report.verdict == "blocked" and report.findings
    return audits, predecessor, report


def test_revision_closes_all_findings_and_promotes_only_after_clean_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = build_stack(tmp_path)
    try:
        audits, predecessor, blocked = _blocked_predecessor(stack)
        service = RevisionService(audit_service=audits)
        original = service.drafts.promote_if_current
        observed: dict[str, object] = {}

        def observe_promotion(previous, installed):  # type: ignore[no-untyped-def]
            assert previous == predecessor
            assert service.drafts.current() == predecessor
            predecessor_audit = audits.store.current(predecessor)
            assert predecessor_audit is not None
            assert audits.store.load(predecessor_audit).verdict == "blocked"
            successor_audit = audits.store.current(installed)
            assert successor_audit is not None
            assert audits.store.load(successor_audit).verdict == "clean"
            revision_ref = service.store.current(predecessor)
            assert revision_ref is not None
            prepared = service.store.load(revision_ref)
            assert prepared.successor_ref == installed
            assert prepared.successor_audit_ref == successor_audit
            observed.update(successor=installed, audit=successor_audit)
            original(previous=previous, installed=installed)

        monkeypatch.setattr(service.drafts, "promote_if_current", observe_promotion)
        revision_ref = service.revise(predecessor, stack.candidate)
        revision = service.status(revision_ref, predecessor)

        assert revision.successor_ref == observed["successor"]
        assert revision.successor_audit_ref == observed["audit"]
        assert revision.closed_finding_ids == tuple(
            item.finding_id for item in blocked.findings
        )
        assert tuple(
            item.predecessor_target for item in revision.closure_witnesses
        ) == tuple(item.target for item in blocked.findings)
        successor = service.drafts.load(revision.successor_ref)
        assert successor.generation == 2
        assert successor.predecessor_ref == predecessor
        assert service.drafts.current() == revision.successor_ref
        assert (
            audits.status(revision.successor_audit_ref, revision.successor_ref).verdict
            == "clean"
        )
    finally:
        stack.orchestrator.close()


def test_staged_successor_is_not_publicly_auditable_before_final_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = build_stack(tmp_path)
    try:
        audits, predecessor, _ = _blocked_predecessor(stack)
        service = RevisionService(audit_service=audits)
        staged: list[object] = []

        def stop_before_cas(*, previous, installed):  # type: ignore[no-untyped-def]
            assert previous == predecessor
            assert service.drafts.current() == predecessor
            assert audits.store.current(installed) is not None
            staged.append(installed)
            raise OSError("injected before final draft CAS")

        monkeypatch.setattr(service.drafts, "promote_if_current", stop_before_cas)
        with pytest.raises(PaperIntegrityInvalid, match="promotion|revision"):
            service.revise(predecessor, stack.candidate)

        assert service.drafts.current() == predecessor
        assert len(staged) == 1
        with pytest.raises(PaperAuthorityInvalid, match="current"):
            audits.audit(staged[0])  # type: ignore[arg-type]
    finally:
        stack.orchestrator.close()


def test_revision_rejects_unchanged_or_clean_predecessor(tmp_path: Path) -> None:
    blocked_stack = build_stack(tmp_path / "blocked")
    try:
        audits, predecessor, _ = _blocked_predecessor(blocked_stack)
        service = RevisionService(audit_service=audits)
        blocked_candidate = _candidate_with_title(
            blocked_stack, "Regulators ought to implement this program nationwide."
        )
        with pytest.raises(
            PaperSupportInvalid, match="material manuscript change"
        ) as caught:
            service.revise(predecessor, blocked_candidate)
        assert caught.value.finding_kind == "revision-unchanged"
        assert service.drafts.current() == predecessor
        assert service.store.current(predecessor) is None
        reordered = type(blocked_candidate).model_validate(
            {
                **blocked_candidate.model_dump(mode="python"),
                "claim_bindings": tuple(reversed(blocked_candidate.claim_bindings)),
            }
        )
        with pytest.raises(PaperSupportInvalid) as reordered_error:
            service.revise(predecessor, reordered)
        assert reordered_error.value.finding_kind == "revision-unchanged"
        assert service.store.current(predecessor) is None
    finally:
        blocked_stack.orchestrator.close()

    clean_stack = build_stack(tmp_path / "clean")
    try:
        predecessor = _publish(clean_stack, clean_stack.candidate)
        audits = PaperAuditService(draft_service=clean_stack.draft_service)
        assert audits.status(audits.audit(predecessor), predecessor).verdict == "clean"
        service = RevisionService(audit_service=audits)
        with pytest.raises(PaperSupportInvalid, match="blocked|finding"):
            service.revise(
                predecessor,
                _candidate_with_title(
                    clean_stack, "Registered contingent valuation evidence"
                ),
            )
        assert service.drafts.current() == predecessor
    finally:
        clean_stack.orchestrator.close()


def test_revision_rejects_partial_closure_without_promoting_or_committing(
    tmp_path: Path,
) -> None:
    stack = build_stack(tmp_path)
    try:
        audits, predecessor, _ = _blocked_predecessor(stack)
        service = RevisionService(audit_service=audits)
        still_dirty = _candidate_with_question(
            stack,
            stack.candidate,
            "Should this policy apply to every household?",
        )

        with pytest.raises(PaperSupportInvalid, match="clean|closure|finding"):
            service.revise(predecessor, still_dirty)

        assert service.drafts.current() == predecessor
        assert service.store.current(predecessor) is None
    finally:
        stack.orchestrator.close()
