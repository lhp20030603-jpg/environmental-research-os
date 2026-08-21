"""Real process contention and crash recovery for paper-draft publication."""

from __future__ import annotations

from multiprocessing import get_context
from pathlib import Path

import pytest
from paper_draft_integration_fixtures import DraftStack, build_stack
from paper_draft_process_fixtures import (
    authority_mutation_worker,
    citation_writer_boundary_worker,
    draft_worker,
    process_arguments,
)

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._draft_store import draft_id
from envresearch.paper.argument_map import ARGUMENT_MAP_SUBJECT
from envresearch.paper.draft_builder import PAPER_DRAFT_SUBJECT
from envresearch.paper.draft_contracts import PaperDraftCandidate
from envresearch.paper.errors import PaperAuthorityInvalid
from envresearch.paper.ledger import CLAIM_LEDGER_SUBJECT


def _candidate_with_title(
    candidate: PaperDraftCandidate, suffix: str
) -> PaperDraftCandidate:
    title = candidate.paragraphs[0]
    return candidate.model_copy(
        update={
            "paragraphs": (
                title.model_copy(update={"text": f"{title.text} {suffix}"}),
                *candidate.paragraphs[1:],
            )
        }
    )


def _run_processes(
    stack: DraftStack,
    candidates: tuple[PaperDraftCandidate, PaperDraftCandidate],
) -> tuple[tuple[str, str], tuple[str, str]]:
    context = get_context("spawn")
    start = context.Event()
    results = context.Queue()
    common = process_arguments(stack)
    processes = tuple(
        context.Process(
            target=draft_worker,
            args=(
                *common,
                candidate.model_dump_json(),
                False,
                start,
                results,
            ),
        )
        for candidate in candidates
    )
    for process in processes:
        process.start()
    start.set()
    received = tuple(results.get(timeout=40) for _ in processes)
    for process in processes:
        process.join(timeout=40)
        assert process.exitcode == 0
    return received[0], received[1]


def test_two_processes_publish_one_identical_current_draft(tmp_path: Path) -> None:
    stack = build_stack(tmp_path)
    try:
        outcomes = _run_processes(stack, (stack.candidate, stack.candidate))

        assert all(status == "ok" for status, _ in outcomes), outcomes
        references = tuple(
            ArtifactRef.model_validate_json(payload) for _, payload in outcomes
        )
        assert references[0] == references[1]
        assert (
            stack.draft_service.registry.current(PAPER_DRAFT_SUBJECT) == references[0]
        )
        assert stack.draft_service.status(
            references[0], map_ref=stack.map_ref, ledger_ref=stack.ledger_ref
        ).paragraphs
    finally:
        stack.orchestrator.close()


def test_two_conflicting_processes_leave_one_current_winner(tmp_path: Path) -> None:
    stack = build_stack(tmp_path)
    try:
        outcomes = _run_processes(
            stack,
            (
                _candidate_with_title(stack.candidate, "Alpha"),
                _candidate_with_title(stack.candidate, "Beta"),
            ),
        )

        successes = tuple(payload for status, payload in outcomes if status == "ok")
        failures = tuple(status for status, _ in outcomes if status != "ok")
        assert len(successes) == 1
        assert failures == ("PAPER_AUTHORITY_INVALID",)
        winner = ArtifactRef.model_validate_json(successes[0])
        assert stack.draft_service.registry.current(PAPER_DRAFT_SUBJECT) == winner
        assert stack.draft_service.status(
            winner, map_ref=stack.map_ref, ledger_ref=stack.ledger_ref
        ).paragraphs
    finally:
        stack.orchestrator.close()


def test_process_death_after_object_before_current_is_retryable(tmp_path: Path) -> None:
    stack = build_stack(tmp_path)
    try:
        context = get_context("spawn")
        start = context.Event()
        results = context.Queue()
        process = context.Process(
            target=draft_worker,
            args=(
                *process_arguments(stack),
                stack.candidate.model_dump_json(),
                True,
                start,
                results,
            ),
        )
        process.start()
        start.set()
        process.join(timeout=40)
        assert process.exitcode == 73
        assert stack.draft_service.registry.current(PAPER_DRAFT_SUBJECT) is None

        object_dir = (
            stack.draft_service.registry.root
            / "exit/objects"
            / draft_id(stack.map_ref, stack.ledger_ref, stack.report_ref)
        )
        published = tuple(object_dir.glob("*.json"))
        assert len(published) == 1

        recovered = stack.draft_service.publish(
            stack.candidate,
            map_ref=stack.map_ref,
            ledger_ref=stack.ledger_ref,
            citation_report_ref=stack.report_ref,
        )
        assert tuple(object_dir.glob("*.json")) == published
        assert stack.draft_service.status(
            recovered, map_ref=stack.map_ref, ledger_ref=stack.ledger_ref
        ).paragraphs
    finally:
        stack.orchestrator.close()


def _run_at_final_authority_check(stack: DraftStack, operation: str) -> ArtifactRef:
    if operation == "publish":
        return stack.draft_service.publish(
            stack.candidate,
            map_ref=stack.map_ref,
            ledger_ref=stack.ledger_ref,
            citation_report_ref=stack.report_ref,
        )
    current = stack.draft_service.registry.current(PAPER_DRAFT_SUBJECT)
    assert current is not None
    if operation == "status":
        stack.draft_service.status(
            current, map_ref=stack.map_ref, ledger_ref=stack.ledger_ref
        )
        return current
    return stack.draft_service.publish(
        stack.candidate,
        map_ref=stack.map_ref,
        ledger_ref=stack.ledger_ref,
        citation_report_ref=stack.report_ref,
    )


@pytest.mark.parametrize("subject", (CLAIM_LEDGER_SUBJECT, ARGUMENT_MAP_SUBJECT))
@pytest.mark.parametrize("operation", ("publish", "status", "recovery"))
def test_final_window_writer_waits_for_composite_paper_authority_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    subject: str,
    operation: str,
) -> None:
    stack = build_stack(tmp_path)
    context = get_context("spawn")
    start = context.Event()
    attempting = context.Event()
    acquired = context.Event()
    mutate = context.Event()
    done = context.Event()
    process = context.Process(
        target=authority_mutation_worker,
        args=(
            str(stack.draft_service.registry.root),
            subject,
            start,
            attempting,
            acquired,
            mutate,
            done,
        ),
    )
    acquired_during_operation = False
    original = stack.citation_authority.require_current

    def gate_final_citation(token):  # type: ignore[no-untyped-def]
        nonlocal acquired_during_operation
        start.set()
        assert attempting.wait(timeout=5)
        acquired_during_operation = acquired.wait(timeout=0.5)
        if acquired_during_operation:
            mutate.set()
            assert done.wait(timeout=5)
        original(token)

    try:
        if operation != "publish":
            _run_at_final_authority_check(stack, "publish")
        monkeypatch.setattr(
            stack.citation_authority, "require_current", gate_final_citation
        )
        process.start()

        reference = _run_at_final_authority_check(stack, operation)

        assert not acquired_during_operation
        assert acquired.wait(timeout=5)
        mutate.set()
        assert done.wait(timeout=5)
        process.join(timeout=10)
        assert process.exitcode == 0
        with pytest.raises(PaperAuthorityInvalid):
            stack.draft_service.status(
                reference, map_ref=stack.map_ref, ledger_ref=stack.ledger_ref
            )
    finally:
        mutate.set()
        process.join(timeout=10)
        stack.orchestrator.close()


@pytest.mark.parametrize("operation", ("publish", "status", "recovery"))
def test_citation_writer_waits_for_whole_public_draft_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Prove the production citation writer cannot enter a Draft success path."""
    stack = build_stack(tmp_path)
    if operation != "publish":
        _run_at_final_authority_check(stack, "publish")
    context = get_context("spawn")
    start = context.Event()
    attempting = context.Event()
    acquired = context.Event()
    release = context.Event()
    done = context.Event()
    process = context.Process(
        target=citation_writer_boundary_worker,
        args=(
            str(stack.orchestrator.queue.root),
            str(stack.orchestrator.queue.control_root),
            start,
            attempting,
            acquired,
            release,
            done,
        ),
    )
    entered_during_operation = False
    original = stack.citation_authority.require_current

    def gate_writer(token):  # type: ignore[no-untyped-def]
        nonlocal entered_during_operation
        start.set()
        assert attempting.wait(timeout=5)
        entered_during_operation = acquired.wait(timeout=0.5)
        original(token)

    try:
        monkeypatch.setattr(stack.citation_authority, "require_current", gate_writer)
        process.start()
        reference = _run_at_final_authority_check(stack, operation)

        assert reference
        assert not entered_during_operation
        assert acquired.wait(timeout=5)
        release.set()
        assert done.wait(timeout=5)
        process.join(timeout=10)
        assert process.exitcode == 0
    finally:
        release.set()
        process.join(timeout=10)
        stack.orchestrator.close()
