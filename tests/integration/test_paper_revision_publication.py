"""Stable exception recovery at every paper revision publication stage."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest
from paper_draft_integration_fixtures import build_stack
from test_paper_revision import _blocked_predecessor

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._audit_store import audit_id
from envresearch.paper._revision_draft import successor_draft
from envresearch.paper._revision_store import (
    revision_commit_subject,
    revision_subject,
)
from envresearch.paper.errors import PaperIntegrityInvalid
from envresearch.paper.revision import RevisionService

Stage = Literal[
    "draft-object",
    "audit-object",
    "revision-object",
    "revision-prepare-before",
    "revision-prepare-after",
    "revision-commit-before",
    "revision-commit-after",
]


def _inject_failure(
    service: RevisionService,
    predecessor: ArtifactRef,
    stage: Stage,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], None]:
    """Install one exact failure and return a restoration callback."""
    if stage == "draft-object":
        original = service.drafts.publish_exact

        def fail_draft(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise OSError("injected successor draft publication failure")

        monkeypatch.setattr(service.drafts, "publish_exact", fail_draft)
        return lambda: monkeypatch.setattr(service.drafts, "publish_exact", original)
    if stage == "audit-object":
        candidate = service.drafts.load(predecessor)
        successor = successor_draft(predecessor, candidate, _clean_candidate(service))
        successor_audit_id = audit_id(service.drafts.expected_ref(successor))
        original_publish = service.registry.publish

        def fail_audit(artifact_id, payload, *, version=1):  # type: ignore[no-untyped-def]
            if artifact_id == successor_audit_id:
                raise OSError("injected successor audit publication failure")
            return original_publish(artifact_id, payload, version=version)

        monkeypatch.setattr(service.registry, "publish", fail_audit)
        return lambda: monkeypatch.setattr(
            service.registry, "publish", original_publish
        )
    if stage == "revision-object":
        original = service.store.publish

        def fail_revision(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise OSError("injected revision object publication failure")

        monkeypatch.setattr(service.store, "publish", fail_revision)
        return lambda: monkeypatch.setattr(service.store, "publish", original)
    method_name = "prepare" if "prepare" in stage else "commit"
    original_pointer = getattr(service.store, method_name)
    write_first = stage.endswith("after")

    def fail_pointer(*args, **kwargs):  # type: ignore[no-untyped-def]
        if write_first:
            original_pointer(*args, **kwargs)
        raise OSError(f"injected revision {method_name} failure")

    monkeypatch.setattr(service.store, method_name, fail_pointer)
    return lambda: monkeypatch.setattr(service.store, method_name, original_pointer)


def _clean_candidate(service: RevisionService):  # type: ignore[no-untyped-def]
    candidate = getattr(service, "_test_candidate", None)
    assert candidate is not None
    return candidate


@pytest.mark.parametrize(
    ("stage", "expected_kind"),
    (
        ("draft-object", "revision-authority-invalid"),
        ("audit-object", "audit-publication-failed"),
        ("revision-object", "revision-authority-invalid"),
        ("revision-prepare-before", "revision-authority-invalid"),
        ("revision-prepare-after", "revision-authority-invalid"),
        ("revision-commit-before", "revision-authority-invalid"),
        ("revision-commit-after", "revision-authority-invalid"),
    ),
)
def test_revision_publication_failure_preserves_predecessor_and_exact_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: Stage,
    expected_kind: str,
) -> None:
    stack = build_stack(tmp_path)
    try:
        audits, predecessor, _ = _blocked_predecessor(stack)
        service = RevisionService(audit_service=audits)
        service._test_candidate = stack.candidate  # type: ignore[attr-defined]
        restore = _inject_failure(service, predecessor, stage, monkeypatch)

        with pytest.raises(PaperIntegrityInvalid) as caught:
            service.revise(predecessor, stack.candidate)
        assert caught.value.finding_kind == expected_kind
        assert service.drafts.current() == predecessor
        pending_before_retry = service.store.pending(predecessor)

        restore()
        revision_ref = service.revise(predecessor, stack.candidate)
        revision = service.status(revision_ref, predecessor)

        if pending_before_retry is not None:
            assert revision_ref == pending_before_retry
        assert service.store.current(predecessor) == revision_ref
        assert service.drafts.current() == revision.successor_ref
    finally:
        stack.orchestrator.close()


@pytest.mark.parametrize(
    ("stage", "target", "raises", "expected_kind"),
    (
        ("draft-object", "publish", True, "draft-publication-failed"),
        ("audit-object", "publish", True, "audit-publication-failed"),
        ("revision-object", "publish", True, "revision-publication-failed"),
        ("revision-prepare-before", "current", True, "revision-publication-failed"),
        ("revision-prepare-after", "current", False, None),
        ("revision-commit-before", "current", True, "revision-publication-failed"),
        ("revision-commit-after", "current", False, None),
    ),
)
def test_registry_write_boundary_recovers_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: Stage,
    target: str,
    raises: bool,
    expected_kind: str | None,
) -> None:
    stack = build_stack(tmp_path)
    try:
        audits, predecessor, _ = _blocked_predecessor(stack)
        service = RevisionService(audit_service=audits)
        successor = successor_draft(
            predecessor,
            service.drafts.load(predecessor),
            stack.candidate,
        )
        successor_ref = service.drafts.expected_ref(successor)
        original_publish = service.registry.publish
        original_current = service.registry.set_current
        write_before_raise = stage.endswith("after") or target == "publish"

        def publish_then_raise(  # type: ignore[no-untyped-def]
            artifact_id, payload, *, version=1
        ):
            is_target = (
                (
                    stage == "draft-object"
                    and artifact_id == successor_ref.artifact_id
                    and version == successor_ref.artifact_version
                )
                or (stage == "audit-object" and artifact_id == audit_id(successor_ref))
                or (
                    stage == "revision-object"
                    and artifact_id == revision_subject(predecessor)
                )
            )
            if is_target and not write_before_raise:
                raise OSError(f"injected before {stage}")
            reference = original_publish(artifact_id, payload, version=version)
            if is_target:
                raise OSError(f"injected after {stage}")
            return reference

        pointer_target = (
            revision_subject(predecessor)
            if "prepare" in stage
            else revision_commit_subject(predecessor)
        )

        def current_then_raise(subject, reference):  # type: ignore[no-untyped-def]
            is_target = target == "current" and subject == pointer_target
            if is_target and not write_before_raise:
                raise OSError(f"injected before {stage}")
            original_current(subject, reference)
            if is_target:
                raise OSError(f"injected after {stage}")

        monkeypatch.setattr(service.registry, "publish", publish_then_raise)
        monkeypatch.setattr(service.registry, "set_current", current_then_raise)

        if raises:
            with pytest.raises(PaperIntegrityInvalid) as caught:
                service.revise(predecessor, stack.candidate)
            assert caught.value.finding_kind == expected_kind
            assert service.drafts.current() == predecessor
            monkeypatch.setattr(service.registry, "publish", original_publish)
            monkeypatch.setattr(service.registry, "set_current", original_current)
            revision_ref = service.revise(predecessor, stack.candidate)
        else:
            revision_ref = service.revise(predecessor, stack.candidate)

        revision = service.status(revision_ref, predecessor)
        assert service.store.current(predecessor) == revision_ref
        assert service.drafts.current() == revision.successor_ref
    finally:
        stack.orchestrator.close()
