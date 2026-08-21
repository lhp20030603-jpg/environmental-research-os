"""Direct single-read, canonical-byte, and torn-pointer audit-store tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from paper_audit_integration_support import audit_service, publish_draft
from paper_draft_integration_fixtures import build_stack

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._audit_store import audit_commit_subject, audit_subject
from envresearch.paper.errors import PaperIntegrityInvalid


def _object_path(reference: ArtifactRef) -> Path:
    return (
        Path("exit/objects")
        / reference.artifact_id
        / f"v{reference.artifact_version}-{reference.content_hash}.json"
    )


def test_audit_store_load_reads_the_exact_object_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        service = audit_service(stack)
        audit_ref = service.audit(draft_ref)
        original = service.registry.files.read
        reads = 0

        def counted(relative: Path) -> bytes:
            nonlocal reads
            if relative == _object_path(audit_ref):
                reads += 1
                if reads > 1:
                    raise AssertionError("audit object was read more than once")
            return original(relative)

        monkeypatch.setattr(service.registry.files, "read", counted)
        assert service.store.load(audit_ref).draft_ref == draft_ref
        assert reads == 1
    finally:
        stack.orchestrator.close()


def test_audit_store_rejects_hash_matching_noncanonical_json(tmp_path: Path) -> None:
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        service = audit_service(stack)
        audit_ref = service.audit(draft_ref)
        canonical = service.registry.files.read(_object_path(audit_ref))
        noncanonical = json.dumps(json.loads(canonical), indent=2).encode()
        forged = ArtifactRef(
            artifact_id=audit_ref.artifact_id,
            artifact_version=1,
            content_hash=hashlib.sha256(noncanonical).hexdigest(),
        )
        service.registry.files.persist_exact(_object_path(forged), noncanonical)

        with pytest.raises(PaperIntegrityInvalid, match="canonical"):
            service.store.load(forged)
    finally:
        stack.orchestrator.close()


def test_commit_marker_without_pending_pointer_fails_closed(tmp_path: Path) -> None:
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        service = audit_service(stack)
        audit_ref = service.audit(draft_ref)
        service.registry.files.unlink(
            Path("exit/current") / f"{audit_subject(draft_ref)}.json"
        )

        with pytest.raises(PaperIntegrityInvalid, match="commit marker"):
            service.store.recover_uncommitted(draft_ref)

        assert service.registry.current(audit_commit_subject(draft_ref)) == audit_ref
        assert service.store.current(draft_ref) is None
    finally:
        stack.orchestrator.close()


def test_new_pending_over_old_commit_recovers_exact_old_pair(tmp_path: Path) -> None:
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        service = audit_service(stack)
        old = service.audit(draft_ref)
        report = service.store.load(old)
        pending = service.registry.publish("external-audit-pending", report)
        service.registry.set_current(audit_subject(draft_ref), pending)

        service.store.recover_uncommitted(draft_ref)

        assert service.registry.current(audit_subject(draft_ref)) == old
        assert service.registry.current(audit_commit_subject(draft_ref)) == old
        assert service.store.current(draft_ref) == old
    finally:
        stack.orchestrator.close()


def test_non_audit_commit_marker_is_not_copied_into_pending(tmp_path: Path) -> None:
    """Catch recovery that turns an authenticated draft ref into an audit current."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        service = audit_service(stack)
        pending = service.audit(draft_ref)
        service.registry.set_current(audit_commit_subject(draft_ref), draft_ref)

        with pytest.raises(PaperIntegrityInvalid):
            service.store.recover_uncommitted(draft_ref)

        assert service.registry.current(audit_subject(draft_ref)) == pending
        assert service.registry.current(audit_commit_subject(draft_ref)) == draft_ref
        assert service.store.current(draft_ref) is None
    finally:
        stack.orchestrator.close()
