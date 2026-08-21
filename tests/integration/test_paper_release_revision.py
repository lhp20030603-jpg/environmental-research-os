"""Release acceptance requires service-authenticated revision closure."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from paper_draft_integration_fixtures import build_stack
from test_paper_revision import _blocked_predecessor

from envresearch.paper._revision_draft import successor_draft
from envresearch.paper.errors import PaperAuthorityInvalid, PaperIntegrityInvalid
from envresearch.paper.release import PaperReleaseService
from envresearch.paper.revision import RevisionService


def test_clean_generation_two_without_committed_revision_cannot_release(
    tmp_path: Path,
) -> None:
    """Catch direct draft promotion bypassing the immutable closure envelope."""
    stack = build_stack(tmp_path)
    try:
        audits, predecessor_ref, _ = _blocked_predecessor(stack)
        revisions = RevisionService(audit_service=audits)
        predecessor = revisions.drafts.load(predecessor_ref)
        successor = successor_draft(predecessor_ref, predecessor, stack.candidate)
        successor_ref = revisions.drafts.publish_exact(successor)
        revisions.drafts.promote_if_current(
            previous=predecessor_ref, installed=successor_ref
        )
        audit_ref = audits.audit(successor_ref)
        assert audits.status(audit_ref, successor_ref).verdict == "clean"

        with pytest.raises((PaperAuthorityInvalid, PaperIntegrityInvalid)):
            PaperReleaseService(audit_service=audits).build(audit_ref, successor_ref)
    finally:
        stack.orchestrator.close()


def test_clean_terminal_revision_cannot_hide_missing_revision_ancestry(
    tmp_path: Path,
) -> None:
    """Catch a valid latest revision concealing an unrevisioned earlier hop."""
    stack = build_stack(tmp_path)
    try:
        audits, generation_one_ref, _ = _blocked_predecessor(stack)
        revisions = RevisionService(audit_service=audits)
        generation_one = revisions.drafts.load(generation_one_ref)
        results = next(
            item for item in stack.candidate.paragraphs if item.section == "results"
        )
        numeric = next(
            token
            for token in results.text.replace(",", " ").split()
            if any(character.isdigit() for character in token)
        )
        dirty_candidate = stack.candidate.model_copy(
            update={
                "paragraphs": tuple(
                    paragraph.model_copy(
                        update={"text": paragraph.text.replace(numeric, "99.999", 1)}
                    )
                    if paragraph.section == "results"
                    else paragraph
                    for paragraph in stack.candidate.paragraphs
                )
            }
        )
        generation_two = successor_draft(
            generation_one_ref, generation_one, dirty_candidate
        )
        generation_two_ref = revisions.drafts.publish_exact(generation_two)
        revisions.drafts.promote_if_current(
            previous=generation_one_ref, installed=generation_two_ref
        )
        generation_two_audit_ref = audits.audit(generation_two_ref)
        assert (
            audits.status(generation_two_audit_ref, generation_two_ref).verdict
            == "blocked"
        )

        revision_ref = revisions.revise(generation_two_ref, stack.candidate)
        revision = revisions.status(revision_ref, generation_two_ref)

        with pytest.raises((PaperAuthorityInvalid, PaperIntegrityInvalid)):
            PaperReleaseService(audit_service=audits).build(
                revision.successor_audit_ref,
                revision.successor_ref,
                revision_ref=revision_ref,
            )
    finally:
        stack.orchestrator.close()


def test_legitimate_revision_release_binds_and_reopens_exact_closure(
    tmp_path: Path,
) -> None:
    """Catch a generation-two release that omits or trusts revision metadata."""
    stack = build_stack(tmp_path)
    try:
        audits, predecessor_ref, _ = _blocked_predecessor(stack)
        revisions = RevisionService(audit_service=audits)
        revision_ref = revisions.revise(predecessor_ref, stack.candidate)
        revision = revisions.status(revision_ref, predecessor_ref)
        service = PaperReleaseService(audit_service=audits)

        release_ref = service.build(
            revision.successor_audit_ref,
            revision.successor_ref,
            revision_ref=revision_ref,
        )
        release = service.status(release_ref)

        assert release.revision_ref == revision_ref
        assert release.revision == revision
        assert release.draft_ref == revision.successor_ref
        assert release.audit_ref == revision.successor_audit_ref
        assert service.handoff(release_ref) == (release_ref, release)
    finally:
        stack.orchestrator.close()


def test_revision_pointer_mismatch_invalidates_an_existing_release(
    tmp_path: Path,
) -> None:
    """Catch release status trusting embedded closure after current mismatch."""
    stack = build_stack(tmp_path)
    try:
        audits, predecessor_ref, _ = _blocked_predecessor(stack)
        revisions = RevisionService(audit_service=audits)
        revision_ref = revisions.revise(predecessor_ref, stack.candidate)
        revision = revisions.status(revision_ref, predecessor_ref)
        service = PaperReleaseService(audit_service=audits)
        release_ref = service.build(
            revision.successor_audit_ref,
            revision.successor_ref,
            revision_ref=revision_ref,
        )
        from envresearch.paper._revision_store import revision_commit_subject

        revisions.registry.files.unlink(
            Path("exit/current") / f"{revision_commit_subject(predecessor_ref)}.json"
        )

        with pytest.raises((PaperAuthorityInvalid, PaperIntegrityInvalid)):
            service.status(release_ref)
    finally:
        stack.orchestrator.close()


def test_mutated_revision_bytes_invalidate_release_as_integrity_failure(
    tmp_path: Path,
) -> None:
    """Catch embedded revision closure concealing changed immutable bytes."""
    stack = build_stack(tmp_path)
    try:
        audits, predecessor_ref, _ = _blocked_predecessor(stack)
        revisions = RevisionService(audit_service=audits)
        revision_ref = revisions.revise(predecessor_ref, stack.candidate)
        revision = revisions.status(revision_ref, predecessor_ref)
        service = PaperReleaseService(audit_service=audits)
        release_ref = service.build(
            revision.successor_audit_ref,
            revision.successor_ref,
            revision_ref=revision_ref,
        )
        path = (
            revisions.registry.root
            / "exit/objects"
            / revision_ref.artifact_id
            / f"v1-{revision_ref.content_hash}.json"
        )
        payload = json.loads(path.read_bytes())
        payload["closed_finding_ids"] = ["forged-finding"]
        path.chmod(0o600)
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(PaperIntegrityInvalid):
            service.status(release_ref)
    finally:
        stack.orchestrator.close()


def test_draft_change_between_preview_and_revision_lease_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch a lock set derived from an unverified draft preview."""
    stack = build_stack(tmp_path)
    try:
        audits, predecessor_ref, _ = _blocked_predecessor(stack)
        revisions = RevisionService(audit_service=audits)
        revision_ref = revisions.revise(predecessor_ref, stack.candidate)
        revision = revisions.status(revision_ref, predecessor_ref)
        import envresearch.paper.release as release_module

        original = release_module.chain_authority

        @contextmanager
        def mutate_before_lease(self, chain):  # type: ignore[no-untyped-def]
            successor = chain[-1][1].successor_ref
            path = (
                self.registry.root
                / "exit/objects"
                / successor.artifact_id
                / f"v{successor.artifact_version}-{successor.content_hash}.json"
            )
            path.chmod(0o600)
            path.write_bytes(path.read_bytes() + b"changed-before-lease")
            with original(self, chain):
                yield

        monkeypatch.setattr(release_module, "chain_authority", mutate_before_lease)

        with pytest.raises(PaperIntegrityInvalid):
            PaperReleaseService(audit_service=audits).build(
                revision.successor_audit_ref,
                revision.successor_ref,
                revision_ref=revision_ref,
            )
    finally:
        stack.orchestrator.close()
