"""Fail-closed status checks for exact revision closure envelopes."""

from __future__ import annotations

from pathlib import Path

import pytest
from paper_draft_integration_fixtures import build_stack
from test_paper_revision import _blocked_predecessor, _candidate_with_title

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._audit_store import audit_commit_subject, audit_subject
from envresearch.paper._draft_store import PAPER_DRAFT_SUBJECT
from envresearch.paper._revision_store import revision_commit_subject, revision_subject
from envresearch.paper.audit_contracts import TextSpan
from envresearch.paper.errors import PaperAuthorityInvalid, PaperIntegrityInvalid
from envresearch.paper.revision import RevisionService
from envresearch.paper.revision_contracts import DraftRevision


def _promoted(tmp_path: Path):  # type: ignore[no-untyped-def]
    stack = build_stack(tmp_path)
    audits, predecessor, _ = _blocked_predecessor(stack)
    service = RevisionService(audit_service=audits)
    revision_ref = service.revise(predecessor, stack.candidate)
    revision = service.status(revision_ref, predecessor)
    return stack, service, predecessor, revision_ref, revision


def _install_forged(
    service: RevisionService,
    predecessor: ArtifactRef,
    revision: DraftRevision,
) -> ArtifactRef:
    reference = service.store.publish(revision)
    service.registry.set_current(revision_subject(predecessor), reference)
    service.registry.set_current(revision_commit_subject(predecessor), reference)
    return reference


@pytest.mark.parametrize("field", ["predecessor_target", "claim_ids"])
def test_revision_status_rejects_semantically_forged_witness(
    tmp_path: Path, field: str
) -> None:
    stack, service, predecessor, _, revision = _promoted(tmp_path)
    try:
        first = revision.closure_witnesses[0]
        update = (
            {
                "predecessor_target": TextSpan(
                    target_type="text-span",
                    paragraph_id="research-question",
                    start=0,
                    end=6,
                    text_sha256="f" * 64,
                )
            }
            if field == "predecessor_target"
            else {"claim_ids": ("unrelated-registered-claim",)}
        )
        forged_witness = first.model_copy(update=update)
        forged = DraftRevision.model_validate(
            {
                **revision.model_dump(mode="python"),
                "closure_witnesses": (
                    forged_witness,
                    *revision.closure_witnesses[1:],
                ),
            }
        )
        forged_ref = _install_forged(service, predecessor, forged)

        with pytest.raises(PaperIntegrityInvalid) as caught:
            service.status(forged_ref, predecessor)
        assert caught.value.finding_kind == "revision-closure-mismatch"
    finally:
        stack.orchestrator.close()


@pytest.mark.parametrize("role", ["map_ref", "ledger_ref", "citation_report_ref"])
def test_revision_status_rejects_same_role_upstream_substitution(
    tmp_path: Path, role: str
) -> None:
    stack, service, predecessor, _, revision = _promoted(tmp_path)
    try:
        original = getattr(revision, role)
        substituted = original.model_copy(update={"content_hash": "e" * 64})
        forged = DraftRevision.model_validate(
            {**revision.model_dump(mode="python"), role: substituted}
        )
        forged_ref = _install_forged(service, predecessor, forged)

        with pytest.raises(PaperIntegrityInvalid) as caught:
            service.status(forged_ref, predecessor)
        assert caught.value.finding_kind == "revision-chain-invalid"
    finally:
        stack.orchestrator.close()


def test_revision_rejects_stale_predecessor_for_a_different_candidate(
    tmp_path: Path,
) -> None:
    stack, service, predecessor, revision_ref, revision = _promoted(tmp_path)
    try:
        different = _candidate_with_title(
            stack, "Registered contingent valuation evidence"
        )
        with pytest.raises(PaperAuthorityInvalid) as caught:
            service.revise(predecessor, different)
        assert caught.value.finding_kind == "revision-predecessor-not-current"

        assert service.store.current(predecessor) == revision_ref
        assert service.drafts.current() == revision.successor_ref
    finally:
        stack.orchestrator.close()


def test_revision_status_rejects_torn_pending_and_commit_marker(tmp_path: Path) -> None:
    stack, service, predecessor, revision_ref, revision = _promoted(tmp_path)
    try:
        service.registry.set_current(
            revision_commit_subject(predecessor), revision.successor_ref
        )

        with pytest.raises(PaperAuthorityInvalid) as caught:
            service.status(revision_ref, predecessor)
        assert caught.value.finding_kind == "revision-not-current"
    finally:
        stack.orchestrator.close()


@pytest.mark.parametrize("draft_role", ["predecessor", "successor"])
@pytest.mark.parametrize("pointer_mode", ["noncurrent", "substituted"])
def test_revision_status_rejects_noncurrent_or_substituted_audit(
    tmp_path: Path, draft_role: str, pointer_mode: str
) -> None:
    stack, service, predecessor, revision_ref, revision = _promoted(tmp_path)
    try:
        target = predecessor if draft_role == "predecessor" else revision.successor_ref
        other = (
            revision.successor_audit_ref
            if draft_role == "predecessor"
            else revision.predecessor_audit_ref
        )
        if pointer_mode == "noncurrent":
            service.registry.set_current(audit_commit_subject(target), other)
        else:
            service.registry.set_current(audit_subject(target), other)
            service.registry.set_current(audit_commit_subject(target), other)

        with pytest.raises(PaperAuthorityInvalid) as caught:
            service.status(revision_ref, predecessor)
        assert caught.value.finding_kind == "audit-not-current"
    finally:
        stack.orchestrator.close()


def test_revision_status_rejects_draft_current_mismatch(tmp_path: Path) -> None:
    stack, service, predecessor, revision_ref, _ = _promoted(tmp_path)
    try:
        service.registry.set_current(PAPER_DRAFT_SUBJECT, predecessor)

        with pytest.raises(PaperAuthorityInvalid) as caught:
            service.status(revision_ref, predecessor)
        assert caught.value.finding_kind == "revision-successor-not-current"
    finally:
        stack.orchestrator.close()


def test_revision_status_rejects_live_upstream_authority_change(tmp_path: Path) -> None:
    stack, service, predecessor, revision_ref, _ = _promoted(tmp_path)
    try:
        stack.ledger_service.resolver.current = False  # type: ignore[attr-defined]

        with pytest.raises(PaperAuthorityInvalid) as caught:
            service.status(revision_ref, predecessor)
        assert caught.value.finding_kind == "transition-not-current"
    finally:
        stack.orchestrator.close()
