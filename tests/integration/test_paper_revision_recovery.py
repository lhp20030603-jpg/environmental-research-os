"""Idempotent recovery boundaries for paper revision promotion."""

from __future__ import annotations

from pathlib import Path

import pytest
from paper_draft_integration_fixtures import build_stack
from test_paper_revision import _blocked_predecessor, _candidate_with_title

from envresearch.paper._draft_store import PAPER_DRAFT_SUBJECT
from envresearch.paper.errors import PaperAuthorityInvalid, PaperIntegrityInvalid
from envresearch.paper.revision import RevisionService


def test_successful_revision_retry_returns_same_exact_revision(tmp_path: Path) -> None:
    stack = build_stack(tmp_path)
    try:
        audits, predecessor, _ = _blocked_predecessor(stack)
        service = RevisionService(audit_service=audits)

        first = service.revise(predecessor, stack.candidate)
        second = service.revise(predecessor, stack.candidate)

        assert second == first
        assert service.store.current(predecessor) == first
        assert (
            service.status(first, predecessor).successor_ref == service.drafts.current()
        )
    finally:
        stack.orchestrator.close()


def test_prepared_revision_rejects_a_different_successor_before_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = build_stack(tmp_path)
    try:
        audits, predecessor, _ = _blocked_predecessor(stack)
        service = RevisionService(audit_service=audits)
        original = service.drafts.promote_if_current

        def stop_before_cas(*, previous, installed):  # type: ignore[no-untyped-def]
            raise OSError(f"injected before {previous} -> {installed}")

        monkeypatch.setattr(service.drafts, "promote_if_current", stop_before_cas)
        with pytest.raises(PaperIntegrityInvalid, match="promotion"):
            service.revise(predecessor, stack.candidate)
        prepared_ref = service.store.current(predecessor)
        assert prepared_ref is not None
        prepared = service.store.load(prepared_ref)
        prepared_audit = audits.store.current(prepared.successor_ref)
        assert prepared_audit == prepared.successor_audit_ref
        assert service.drafts.current() == predecessor

        monkeypatch.setattr(service.drafts, "promote_if_current", original)
        different = _candidate_with_title(
            stack, "Registered contingent valuation evidence"
        )
        with pytest.raises(PaperAuthorityInvalid, match="conflict|different"):
            service.revise(predecessor, different)

        assert service.drafts.current() == predecessor
        assert service.store.current(predecessor) == prepared_ref
        assert service.store.load(prepared_ref) == prepared
        assert audits.store.current(prepared.successor_ref) == prepared_audit
    finally:
        stack.orchestrator.close()


def test_prepared_exact_revision_retry_reuses_staged_objects_and_promotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = build_stack(tmp_path)
    try:
        audits, predecessor, _ = _blocked_predecessor(stack)
        service = RevisionService(audit_service=audits)
        original = service.drafts.promote_if_current

        def stop_before_cas(*, previous, installed):  # type: ignore[no-untyped-def]
            raise OSError(f"injected before {previous} -> {installed}")

        monkeypatch.setattr(service.drafts, "promote_if_current", stop_before_cas)
        with pytest.raises(PaperIntegrityInvalid, match="promotion"):
            service.revise(predecessor, stack.candidate)
        prepared_ref = service.store.current(predecessor)
        assert prepared_ref is not None
        prepared = service.store.load(prepared_ref)

        monkeypatch.setattr(service.drafts, "promote_if_current", original)
        retried = service.revise(predecessor, stack.candidate)

        assert retried == prepared_ref
        assert service.store.load(retried) == prepared
        assert service.drafts.current() == prepared.successor_ref
        assert (
            audits.store.current(prepared.successor_ref) == prepared.successor_audit_ref
        )
    finally:
        stack.orchestrator.close()


def test_successful_draft_cas_is_the_last_fallible_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = build_stack(tmp_path)
    try:
        audits, predecessor, _ = _blocked_predecessor(stack)
        service = RevisionService(audit_service=audits)
        original_set = service.registry.set_current
        original_current = service.drafts.current
        promoted = False

        def observe_set(subject, reference):  # type: ignore[no-untyped-def]
            nonlocal promoted
            original_set(subject, reference)
            if subject == PAPER_DRAFT_SUBJECT:
                promoted = True

        def forbid_post_cas_read():
            if promoted:
                raise AssertionError("draft current was read after successful CAS")
            return original_current()

        monkeypatch.setattr(service.registry, "set_current", observe_set)
        monkeypatch.setattr(service.drafts, "current", forbid_post_cas_read)

        revision_ref = service.revise(predecessor, stack.candidate)

        revision = service.store.load(revision_ref)
        assert promoted
        assert original_current() == revision.successor_ref
    finally:
        stack.orchestrator.close()


def test_draft_cas_write_then_raise_converges_to_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = build_stack(tmp_path)
    try:
        audits, predecessor, _ = _blocked_predecessor(stack)
        service = RevisionService(audit_service=audits)
        original_set = service.registry.set_current

        def write_then_raise(subject, reference):  # type: ignore[no-untyped-def]
            original_set(subject, reference)
            if subject == PAPER_DRAFT_SUBJECT:
                raise OSError("injected after final draft CAS")

        monkeypatch.setattr(service.registry, "set_current", write_then_raise)
        revision_ref = service.revise(predecessor, stack.candidate)
        revision = service.status(revision_ref, predecessor)

        assert service.drafts.current() == revision.successor_ref
    finally:
        stack.orchestrator.close()
