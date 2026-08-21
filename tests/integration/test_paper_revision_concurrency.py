"""Process contention and exact-boundary recovery for paper revisions."""

from __future__ import annotations

from multiprocessing import get_context
from pathlib import Path

import pytest
from paper_draft_integration_fixtures import DraftStack, build_stack
from paper_draft_process_fixtures import process_arguments
from paper_revision_process_fixtures import (
    CRASH_EXIT_CODES,
    CrashPoint,
    revision_worker,
)
from test_paper_revision import _blocked_predecessor, _candidate_with_title

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._audit_store import audit_id
from envresearch.paper._revision_draft import successor_draft
from envresearch.paper.draft_contracts import PaperDraftCandidate
from envresearch.paper.revision import RevisionService


def _expected_successor(
    service: RevisionService,
    predecessor_ref: ArtifactRef,
    candidate: PaperDraftCandidate,
) -> ArtifactRef:
    predecessor = service.drafts.load(predecessor_ref)
    return service.drafts.expected_ref(
        successor_draft(predecessor_ref, predecessor, candidate)
    )


def _process(
    stack: DraftStack,
    predecessor_ref: ArtifactRef,
    candidate: PaperDraftCandidate,
    crash_point: CrashPoint | None,
    start: object,
    results: object,
):  # type: ignore[no-untyped-def]
    context = get_context("spawn")
    return context.Process(
        target=revision_worker,
        args=(
            *process_arguments(stack),
            predecessor_ref.model_dump_json(),
            candidate.model_dump_json(),
            crash_point,
            start,
            results,
        ),
    )


def _join(process, expected: int) -> None:  # type: ignore[no-untyped-def]
    process.join(timeout=40)
    if process.is_alive():
        raise AssertionError("spawned revision process did not terminate")
    assert process.exitcode == expected


def _cleanup(processes, results) -> None:  # type: ignore[no-untyped-def]
    for process in processes:
        if process.is_alive():
            process.terminate()
    for process in processes:
        process.join(timeout=10)
    results.close()
    results.join_thread()


def _one_outcome(
    stack: DraftStack,
    predecessor_ref: ArtifactRef,
    candidate: PaperDraftCandidate,
) -> tuple[str, str]:
    context = get_context("spawn")
    start = context.Event()
    results = context.Queue()
    process = _process(stack, predecessor_ref, candidate, None, start, results)
    started = []
    try:
        process.start()
        started.append(process)
        start.set()
        outcome = results.get(timeout=40)
        _join(process, 0)
        return outcome  # type: ignore[no-any-return]
    finally:
        _cleanup(started, results)


def _competing_outcomes(
    stack: DraftStack,
    predecessor_ref: ArtifactRef,
    candidates: tuple[PaperDraftCandidate, PaperDraftCandidate],
) -> tuple[tuple[str, str], tuple[str, str]]:
    context = get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = tuple(
        _process(stack, predecessor_ref, candidate, None, start, results)
        for candidate in candidates
    )
    started = []
    try:
        for process in processes:
            process.start()
            started.append(process)
        start.set()
        outcomes = tuple(results.get(timeout=40) for _ in processes)
        for process in processes:
            _join(process, 0)
        return outcomes  # type: ignore[return-value]
    finally:
        _cleanup(started, results)


def _crash_once(
    stack: DraftStack,
    predecessor_ref: ArtifactRef,
    candidate: PaperDraftCandidate,
    crash_point: CrashPoint,
) -> None:
    context = get_context("spawn")
    start = context.Event()
    results = context.Queue()
    process = _process(stack, predecessor_ref, candidate, crash_point, start, results)
    started = []
    try:
        process.start()
        started.append(process)
        start.set()
        _join(process, CRASH_EXIT_CODES[crash_point])
    finally:
        _cleanup(started, results)


def _object_path(root: Path, reference: ArtifactRef) -> Path:
    return (
        root
        / "exit/objects"
        / reference.artifact_id
        / f"v{reference.artifact_version}-{reference.content_hash}.json"
    )


def test_two_processes_recover_one_identical_revision(tmp_path: Path) -> None:
    stack = build_stack(tmp_path)
    try:
        audits, predecessor, _ = _blocked_predecessor(stack)
        outcomes = _competing_outcomes(
            stack, predecessor, (stack.candidate, stack.candidate)
        )

        assert all(status == "ok" for status, _ in outcomes), outcomes
        references = tuple(
            ArtifactRef.model_validate_json(payload) for _, payload in outcomes
        )
        assert references[0] == references[1]
        service = RevisionService(audit_service=audits)
        revision = service.status(references[0], predecessor)
        assert service.store.current(predecessor) == references[0]
        assert service.drafts.current() == revision.successor_ref
    finally:
        stack.orchestrator.close()


def test_two_conflicting_processes_leave_one_exact_revision_winner(
    tmp_path: Path,
) -> None:
    stack = build_stack(tmp_path)
    try:
        audits, predecessor, _ = _blocked_predecessor(stack)
        alternative = _candidate_with_title(
            stack, "Registered contingent valuation evidence"
        )
        candidates = (stack.candidate, alternative)
        service = RevisionService(audit_service=audits)
        successor_refs = tuple(
            _expected_successor(service, predecessor, candidate)
            for candidate in candidates
        )

        outcomes = _competing_outcomes(stack, predecessor, candidates)

        successes = tuple(payload for status, payload in outcomes if status == "ok")
        failures = tuple(status for status, _ in outcomes if status != "ok")
        assert len(successes) == 1
        assert failures == ("PAPER_AUTHORITY_INVALID",)
        winner = ArtifactRef.model_validate_json(successes[0])
        revision = service.status(winner, predecessor)
        assert revision.successor_ref in successor_refs
        loser = next(ref for ref in successor_refs if ref != revision.successor_ref)
        assert not _object_path(service.registry.root, loser).exists()
        assert service.store.current(predecessor) == winner
        assert service.drafts.current() == revision.successor_ref
    finally:
        stack.orchestrator.close()


@pytest.mark.parametrize("crash_point", tuple(CRASH_EXIT_CODES))
def test_process_death_recovers_the_same_exact_revision(
    tmp_path: Path, crash_point: CrashPoint
) -> None:
    stack = build_stack(tmp_path)
    try:
        audits, predecessor, _ = _blocked_predecessor(stack)
        service = RevisionService(audit_service=audits)
        successor_ref = _expected_successor(service, predecessor, stack.candidate)

        _crash_once(stack, predecessor, stack.candidate, crash_point)

        assert service.drafts.load(successor_ref).predecessor_ref == predecessor
        assert _object_path(service.registry.root, successor_ref).is_file()
        orphaned_audit_objects = tuple(
            (service.registry.root / "exit/objects" / audit_id(successor_ref)).glob(
                "*.json"
            )
        )
        audit_pending = audits.store.pending(successor_ref)
        revision_pending = service.store.pending(predecessor)
        if crash_point == "successor-draft":
            assert audit_pending is None
            assert not orphaned_audit_objects
        elif crash_point == "successor-audit-object":
            assert audit_pending is None
            assert len(orphaned_audit_objects) == 1
        else:
            assert audit_pending is not None
        if crash_point == "successor-audit-pending":
            assert audits.store.current(successor_ref) is None
        if crash_point in {
            "successor-audit-commit",
            "revision-pending",
            "revision-commit",
            "final-draft-cas",
        }:
            assert audits.store.current(successor_ref) == audit_pending
        if crash_point in {
            "successor-draft",
            "successor-audit-object",
            "successor-audit-pending",
            "successor-audit-commit",
        }:
            assert revision_pending is None
        if crash_point == "revision-pending":
            assert revision_pending is not None
            assert service.store.current(predecessor) is None
        if crash_point in {"revision-commit", "final-draft-cas"}:
            assert service.store.current(predecessor) == revision_pending
        expected_current = (
            successor_ref if crash_point == "final-draft-cas" else predecessor
        )
        assert service.drafts.current() == expected_current

        outcome = _one_outcome(stack, predecessor, stack.candidate)
        assert outcome[0] == "ok", outcome
        recovered = ArtifactRef.model_validate_json(outcome[1])
        revision = service.status(recovered, predecessor)

        assert revision.successor_ref == successor_ref
        if orphaned_audit_objects:
            assert (
                _object_path(service.registry.root, revision.successor_audit_ref)
                == orphaned_audit_objects[0]
            )
        if audit_pending is not None:
            assert revision.successor_audit_ref == audit_pending
        if revision_pending is not None:
            assert recovered == revision_pending
        assert audits.store.current(successor_ref) == revision.successor_audit_ref
        assert service.store.current(predecessor) == recovered
        assert service.drafts.current() == successor_ref
    finally:
        stack.orchestrator.close()


def test_revision_pending_rejects_a_different_process_candidate(
    tmp_path: Path,
) -> None:
    stack = build_stack(tmp_path)
    try:
        audits, predecessor, _ = _blocked_predecessor(stack)
        service = RevisionService(audit_service=audits)
        prepared_candidate = stack.candidate
        conflicting_candidate = _candidate_with_title(
            stack, "Registered contingent valuation evidence"
        )
        prepared_successor = _expected_successor(
            service, predecessor, prepared_candidate
        )
        _crash_once(stack, predecessor, prepared_candidate, "revision-pending")
        prepared_revision = service.store.pending(predecessor)
        assert prepared_revision is not None
        assert service.store.current(predecessor) is None

        conflict = _one_outcome(stack, predecessor, conflicting_candidate)

        assert conflict[0] == "PAPER_AUTHORITY_INVALID", conflict
        assert service.store.pending(predecessor) == prepared_revision
        assert service.store.current(predecessor) is None
        assert service.drafts.current() == predecessor
        recovered = _one_outcome(stack, predecessor, prepared_candidate)
        assert recovered == ("ok", prepared_revision.model_dump_json())
        revision = service.status(prepared_revision, predecessor)
        assert revision.successor_ref == prepared_successor
    finally:
        stack.orchestrator.close()
