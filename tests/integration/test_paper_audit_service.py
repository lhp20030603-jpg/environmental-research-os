"""Audit publication boundary before revision-lineage implementation."""

from __future__ import annotations

from pathlib import Path

import pytest
from paper_audit_integration_support import (
    audit_service,
    expected_report_lineage,
    forge_numeric_draft,
    publish_draft,
)
from paper_draft_integration_fixtures import build_stack

from envresearch.models.artifact import ArtifactRef
from envresearch.paper.auditor import audit_subject
from envresearch.paper.errors import PaperAuthorityInvalid, PaperIntegrityInvalid


def test_audit_publishes_clean_exact_report_without_task3_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch self-audit through DraftService.status or a missing exact report."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        service = audit_service(stack)

        def forbidden(*args: object, **kwargs: object) -> object:
            del args, kwargs
            raise AssertionError("audit must not call DraftService.status")

        monkeypatch.setattr(stack.draft_service, "status", forbidden)
        audit_ref = service.audit(draft_ref)
        report = service.status(audit_ref, draft_ref)

        assert report.verdict == "clean"
        assert report.findings == ()
        assert report.draft_ref == draft_ref
        assert report.map_ref == stack.map_ref
        assert report.ledger_ref == stack.ledger_ref
        assert report.citation_report_ref == stack.report_ref
        expected_refs, expected_analyses, expected_outputs = expected_report_lineage(
            stack, draft_ref
        )
        assert report.transitive_refs == expected_refs
        assert report.analysis_refs == expected_analyses
        assert report.output_refs == expected_outputs
        assert service.registry.current(audit_subject(draft_ref)) == audit_ref
    finally:
        stack.orchestrator.close()


def test_audit_accumulates_blocking_finding_from_current_coherent_forgery(
    tmp_path: Path,
) -> None:
    """Catch an audit that trusts sealed draft summaries rather than reconstructing."""
    stack = build_stack(tmp_path)
    try:
        clean_ref = publish_draft(stack)
        forged_ref = forge_numeric_draft(stack, clean_ref)
        service = audit_service(stack)

        audit_ref = service.audit(forged_ref)
        report = service.status(audit_ref, forged_ref)

        assert report.verdict == "blocked"
        assert "numeric-contradiction" in {
            item.finding_kind for item in report.findings
        }
        assert all(item.code == "PAPER_SUPPORT_INVALID" for item in report.findings)
        expected_refs, expected_analyses, expected_outputs = expected_report_lineage(
            stack, forged_ref
        )
        assert report.transitive_refs == expected_refs
        ledger = stack.ledger_service.status(stack.ledger_ref, stack.transition_ref)
        rows = {item.claim_id: item for item in ledger.claims}
        for finding in report.findings:
            bound = tuple(rows[item] for item in finding.claim_ids if item in rows)
            assert finding.upstream_refs == expected_refs
            assert finding.analysis_refs == tuple(
                item
                for item in expected_analyses
                if any(row.analysis_ref == item for row in bound)
            )
            assert finding.output_refs == tuple(
                item
                for item in expected_outputs
                if any(item in row.output_evidence for row in bound)
            )
    finally:
        stack.orchestrator.close()


def test_stale_draft_is_authority_failure_and_publishes_no_audit(
    tmp_path: Path,
) -> None:
    """Catch stale exact draft refs being converted into ordinary findings."""
    stack = build_stack(tmp_path)
    try:
        current = publish_draft(stack)
        stale = ArtifactRef(
            artifact_id=current.artifact_id,
            artifact_version=current.artifact_version,
            content_hash="f" * 64,
        )
        service = audit_service(stack)

        with pytest.raises(PaperAuthorityInvalid, match="current"):
            service.audit(stale)

        assert service.registry.current(audit_subject(stale)) is None
    finally:
        stack.orchestrator.close()


def test_audit_is_idempotent_and_current_failure_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch duplicate audit generations or an unreadable current on pointer fault."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        service = audit_service(stack)
        original = service.registry.set_current
        calls = 0

        def fail_once(subject: str, reference: ArtifactRef) -> None:
            nonlocal calls
            calls += 1
            if subject == audit_subject(draft_ref) and calls == 1:
                raise OSError("injected audit current failure")
            original(subject, reference)

        monkeypatch.setattr(service.registry, "set_current", fail_once)
        with pytest.raises(PaperIntegrityInvalid, match="publication"):
            service.audit(draft_ref)
        assert service.registry.current(audit_subject(draft_ref)) is None

        monkeypatch.setattr(service.registry, "set_current", original)
        first = service.audit(draft_ref)
        second = service.audit(draft_ref)
        assert second == first
        assert service.status(first, draft_ref).verdict == "clean"
    finally:
        stack.orchestrator.close()


def test_audit_object_publication_failure_has_no_current_and_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch an object-write failure leaking a current or blocking exact retry."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        service = audit_service(stack)
        original = service.registry.publish
        failed = False

        def fail_audit_once(artifact_id: str, payload):  # type: ignore[no-untyped-def]
            nonlocal failed
            if artifact_id.startswith("paper-audit-") and not failed:
                failed = True
                raise OSError("injected exact audit object failure")
            return original(artifact_id, payload)

        monkeypatch.setattr(service.registry, "publish", fail_audit_once)
        with pytest.raises(PaperIntegrityInvalid, match="publication"):
            service.audit(draft_ref)
        assert service.registry.current(audit_subject(draft_ref)) is None

        monkeypatch.setattr(service.registry, "publish", original)
        recovered = service.audit(draft_ref)
        assert service.status(recovered, draft_ref).verdict == "clean"
    finally:
        stack.orchestrator.close()


@pytest.mark.parametrize(
    ("failure", "expected"),
    (
        (OSError("late citation I/O"), PaperIntegrityInvalid),
        (TypeError("late citation payload"), PaperIntegrityInvalid),
        (ValueError("late citation generation"), PaperAuthorityInvalid),
    ),
)
def test_raw_final_citation_failure_is_stable_and_rolls_back_pending_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected: type[Exception],
) -> None:
    """Catch raw final-authority errors escaping with a pending audit current."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        service = audit_service(stack)

        def fail_final(token: object) -> None:
            del token
            raise failure

        monkeypatch.setattr(service.citation_authority, "require_current", fail_final)
        with pytest.raises(expected):
            service.audit(draft_ref)

        assert service.registry.current(audit_subject(draft_ref)) is None
        assert service.store.current(draft_ref) is None
    finally:
        stack.orchestrator.close()


def test_citation_generation_cannot_advance_inside_audit_commit_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch a citation writer entering between final validation and return."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        service = audit_service(stack)
        original = service.store.commit
        require_current = service.citation_authority.require_current
        citation_current = True
        calls = 0

        def advance_then_commit(
            reference_draft: ArtifactRef, audit_ref: ArtifactRef
        ) -> None:
            nonlocal calls, citation_current
            calls += 1
            original(reference_draft, audit_ref)
            citation_current = False

        def require_current_generation(token: object) -> None:
            if not citation_current:
                raise PaperAuthorityInvalid(
                    "citation generation changed",
                    finding_kind="citation-source-not-current",
                )
            require_current(token)  # type: ignore[arg-type]

        monkeypatch.setattr(service.store, "commit", advance_then_commit)
        monkeypatch.setattr(
            service.citation_authority,
            "require_current",
            require_current_generation,
        )
        with pytest.raises(PaperAuthorityInvalid):
            service.audit(draft_ref)

        assert calls == 1
        assert service.store.current(draft_ref) is None
    finally:
        stack.orchestrator.close()


def test_accepted_transition_cannot_change_inside_audit_commit_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch an accepted-evidence writer entering before audit linearization."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        service = audit_service(stack)
        original = service.store.commit

        def stale_then_commit(
            reference_draft: ArtifactRef, audit_ref: ArtifactRef
        ) -> None:
            stack.ledger_service.resolver.current = False  # type: ignore[attr-defined]
            original(reference_draft, audit_ref)

        monkeypatch.setattr(service.store, "commit", stale_then_commit)
        with pytest.raises(PaperAuthorityInvalid):
            service.audit(draft_ref)

        assert service.store.current(draft_ref) is None
    finally:
        stack.orchestrator.close()


def test_idempotent_audit_recovery_never_returns_a_noncurrent_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch a pointer swap after recovery validation but before its return."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        service = audit_service(stack)
        service.audit(draft_ref)
        original = service.citation_authority.require_current

        def swap_after_final_check(token: object) -> None:
            original(token)  # type: ignore[arg-type]
            service.registry.set_current(audit_subject(draft_ref), draft_ref)

        monkeypatch.setattr(
            service.citation_authority, "require_current", swap_after_final_check
        )
        with pytest.raises(PaperAuthorityInvalid, match="not current"):
            service.audit(draft_ref)

        assert service.registry.current(audit_subject(draft_ref)) == draft_ref
    finally:
        stack.orchestrator.close()


def test_audit_rejects_late_typed_output_evidence_mutation_and_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch output-evidence authority changing after immutable audit publication."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        service = audit_service(stack)
        resolver = stack.ledger_service.resolver
        original = service.registry.set_current

        def mutate_then_install(subject: str, reference: ArtifactRef) -> None:
            original(subject, reference)
            if subject == audit_subject(draft_ref):
                resolver.current = False  # type: ignore[attr-defined]

        monkeypatch.setattr(service.registry, "set_current", mutate_then_install)
        with pytest.raises(PaperAuthorityInvalid):
            service.audit(draft_ref)

        assert service.registry.current(audit_subject(draft_ref)) is None
    finally:
        stack.orchestrator.close()


def test_status_single_read_loader_rejects_mutated_audit_bytes(tmp_path: Path) -> None:
    """Catch canonical audit bytes changing after their report ref was returned."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        service = audit_service(stack)
        audit_ref = service.audit(draft_ref)
        path = (
            service.registry.root
            / "exit/objects"
            / audit_ref.artifact_id
            / f"v1-{audit_ref.content_hash}.json"
        )
        path.chmod(0o600)
        path.write_bytes(path.read_bytes() + b" ")

        with pytest.raises(PaperIntegrityInvalid):
            service.status(audit_ref, draft_ref)
    finally:
        stack.orchestrator.close()


def test_audit_rollback_never_overwrites_a_newer_current_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch rollback that ignores lost compare-and-restore ownership."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        service = audit_service(stack)
        installed = service.audit(draft_ref)
        newer = installed.model_copy(update={"content_hash": "e" * 64})
        monkeypatch.setattr(
            service.registry,
            "restore_current_if_unchanged",
            lambda *args, **kwargs: False,
        )

        with pytest.raises(PaperIntegrityInvalid, match="rollback"):
            service.store.restore(draft_ref=draft_ref, previous=None, installed=newer)

        assert service.registry.current(audit_subject(draft_ref)) == installed
    finally:
        stack.orchestrator.close()
