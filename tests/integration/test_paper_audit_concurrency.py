"""Process contention, crash recovery, and audit final-window tests."""

from __future__ import annotations

from multiprocessing import get_context
from pathlib import Path

import pytest
from paper_audit_process_fixtures import (
    audit_worker,
    transition_writer_boundary_worker,
)
from paper_draft_integration_fixtures import DraftStack, build_stack
from paper_draft_process_fixtures import (
    authority_mutation_worker,
    citation_writer_boundary_worker,
    process_arguments,
)

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.models.artifact import ArtifactRef
from envresearch.paper._audit_store import audit_commit_subject, audit_id
from envresearch.paper.argument_map import ARGUMENT_MAP_SUBJECT
from envresearch.paper.auditor import PaperAuditService, audit_subject
from envresearch.paper.draft_builder import PAPER_DRAFT_SUBJECT
from envresearch.paper.errors import PaperAuthorityInvalid
from envresearch.paper.ledger import (
    CLAIM_LEDGER_SUBJECT,
    V031AcceptedEvidenceResolver,
)


def _draft(stack: DraftStack) -> ArtifactRef:
    return stack.draft_service.publish(
        stack.candidate,
        map_ref=stack.map_ref,
        ledger_ref=stack.ledger_ref,
        citation_report_ref=stack.report_ref,
    )


def test_two_processes_publish_one_identical_current_audit(tmp_path: Path) -> None:
    stack = build_stack(tmp_path)
    try:
        draft_ref = _draft(stack)
        context = get_context("spawn")
        start = context.Event()
        results = context.Queue()
        common = process_arguments(stack)
        processes = tuple(
            context.Process(
                target=audit_worker,
                args=(
                    *common,
                    draft_ref.model_dump_json(),
                    None,
                    start,
                    results,
                ),
            )
            for _ in range(2)
        )
        for process in processes:
            process.start()
        start.set()
        outcomes = tuple(results.get(timeout=40) for _ in processes)
        for process in processes:
            process.join(timeout=40)
            assert process.exitcode == 0

        assert all(status == "ok" for status, _ in outcomes), outcomes
        references = tuple(
            ArtifactRef.model_validate_json(payload) for _, payload in outcomes
        )
        assert references[0] == references[1]
        service = PaperAuditService(draft_service=stack.draft_service)
        assert service.status(references[0], draft_ref).verdict == "clean"
    finally:
        stack.orchestrator.close()


def test_process_death_after_audit_object_before_current_is_retryable(
    tmp_path: Path,
) -> None:
    stack = build_stack(tmp_path)
    try:
        draft_ref = _draft(stack)
        context = get_context("spawn")
        start = context.Event()
        results = context.Queue()
        process = context.Process(
            target=audit_worker,
            args=(
                *process_arguments(stack),
                draft_ref.model_dump_json(),
                "before-current",
                start,
                results,
            ),
        )
        process.start()
        start.set()
        process.join(timeout=40)
        assert process.exitcode == 74
        service = PaperAuditService(draft_service=stack.draft_service)
        assert service.registry.current(audit_subject(draft_ref)) is None

        object_dir = service.registry.root / "exit/objects" / audit_id(draft_ref)
        published = tuple(object_dir.glob("*.json"))
        assert len(published) == 1
        recovered = service.audit(draft_ref)
        assert tuple(object_dir.glob("*.json")) == published
        assert service.status(recovered, draft_ref).verdict == "clean"
    finally:
        stack.orchestrator.close()


@pytest.mark.parametrize("stale_after_crash", (False, True))
def test_process_death_after_audit_current_recovers_only_committed_authority(
    tmp_path: Path, stale_after_crash: bool
) -> None:
    """Catch a post-pointer crash exposing an unvalidated audit as current."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = _draft(stack)
        context = get_context("spawn")
        start = context.Event()
        results = context.Queue()
        process = context.Process(
            target=audit_worker,
            args=(
                *process_arguments(stack),
                draft_ref.model_dump_json(),
                "after-current",
                start,
                results,
            ),
        )
        process.start()
        start.set()
        process.join(timeout=40)
        assert process.exitcode == 75
        service = PaperAuditService(draft_service=stack.draft_service)
        assert service.store.current(draft_ref) is None

        if stale_after_crash:
            stack.ledger_service.resolver.current = False  # type: ignore[attr-defined]
            with pytest.raises(PaperAuthorityInvalid):
                service.audit(draft_ref)
            assert service.registry.current(audit_subject(draft_ref)) is None
            assert service.store.current(draft_ref) is None
        else:
            recovered = service.audit(draft_ref)
            assert service.registry.current(audit_subject(draft_ref)) == recovered
            assert service.status(recovered, draft_ref).verdict == "clean"
    finally:
        stack.orchestrator.close()


@pytest.mark.parametrize("stale_after_crash", (False, True))
def test_process_death_after_commit_preserves_linearized_audit(
    tmp_path: Path, stale_after_crash: bool
) -> None:
    """Prove commit-marker write is final even if the process dies immediately."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = _draft(stack)
        context = get_context("spawn")
        start = context.Event()
        results = context.Queue()
        process = context.Process(
            target=audit_worker,
            args=(
                *process_arguments(stack),
                draft_ref.model_dump_json(),
                "after-commit",
                start,
                results,
            ),
        )
        process.start()
        start.set()
        process.join(timeout=40)
        assert process.exitcode == 76
        service = PaperAuditService(draft_service=stack.draft_service)
        committed = service.store.current(draft_ref)
        assert committed is not None

        if stale_after_crash:
            stack.ledger_service.resolver.current = False  # type: ignore[attr-defined]
            with pytest.raises(PaperAuthorityInvalid):
                service.status(committed, draft_ref)
            assert service.store.current(draft_ref) == committed
        else:
            assert service.audit(draft_ref) == committed
            assert service.status(committed, draft_ref).verdict == "clean"
    finally:
        stack.orchestrator.close()


@pytest.mark.parametrize(
    "subject", (CLAIM_LEDGER_SUBJECT, ARGUMENT_MAP_SUBJECT, PAPER_DRAFT_SUBJECT)
)
@pytest.mark.parametrize("operation", ("audit", "status", "recovery"))
def test_final_window_writer_waits_for_composite_audit_authority_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    subject: str,
    operation: str,
) -> None:
    stack = build_stack(tmp_path)
    draft_ref = _draft(stack)
    service = PaperAuditService(draft_service=stack.draft_service)
    current = service.audit(draft_ref) if operation != "audit" else None
    if operation == "recovery":
        service.registry.files.unlink(
            Path("exit/current") / f"{audit_commit_subject(draft_ref)}.json"
        )
        assert service.store.pending(draft_ref) == current
        assert service.store.current(draft_ref) is None
    context = get_context("spawn")
    start = context.Event()
    attempting = context.Event()
    acquired = context.Event()
    mutate = context.Event()
    done = context.Event()
    process = context.Process(
        target=authority_mutation_worker,
        args=(
            str(service.registry.root),
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
        original(token)

    try:
        monkeypatch.setattr(
            stack.citation_authority, "require_current", gate_final_citation
        )
        process.start()
        reference = (
            service.status(current, draft_ref)  # type: ignore[arg-type]
            if operation == "status"
            else service.audit(draft_ref)
        )

        assert reference
        assert not acquired_during_operation
        assert acquired.wait(timeout=5)
        mutate.set()
        assert done.wait(timeout=5)
        process.join(timeout=10)
        assert process.exitcode == 0
        with pytest.raises(PaperAuthorityInvalid):
            service.status(
                current if operation == "status" else reference,  # type: ignore[arg-type]
                draft_ref,
            )
    finally:
        mutate.set()
        process.join(timeout=10)
        stack.orchestrator.close()


@pytest.mark.parametrize("authority", ("citation", "transition"))
@pytest.mark.parametrize("operation", ("audit", "status", "recovery"))
def test_production_external_writer_waits_for_whole_public_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authority: str,
    operation: str,
) -> None:
    """Prove the real external writer lock cannot enter an audit success path."""
    stack = build_stack(tmp_path)
    draft_ref = _draft(stack)
    service = PaperAuditService(draft_service=stack.draft_service)
    context = get_context("spawn")
    start = context.Event()
    attempting = context.Event()
    acquired = context.Event()
    release = context.Event()
    done = context.Event()
    if authority == "citation":
        target = citation_writer_boundary_worker
        args = (
            str(stack.orchestrator.queue.root),
            str(stack.orchestrator.queue.control_root),
        )
    else:
        run_root = (tmp_path / "v031-authority").resolve()
        runner_registry = ExitRegistry(run_root / "runner")
        evaluator_registry = ExitRegistry(run_root / "evaluator")
        with runner_registry.lock("valuation-v031-authority"):
            pass
        assert runner_registry.root.is_dir()
        assert evaluator_registry.root.is_dir()
        production = V031AcceptedEvidenceResolver(run_root)
        monkeypatch.setattr(
            stack.ledger_service.resolver,
            "authority_lease",
            production.authority_lease,
        )
        target = transition_writer_boundary_worker
        args = (str(run_root),)
    current = service.audit(draft_ref) if operation != "audit" else None
    if operation == "recovery":
        service.registry.files.unlink(
            Path("exit/current") / f"{audit_commit_subject(draft_ref)}.json"
        )
        assert service.store.pending(draft_ref) == current
        assert service.store.current(draft_ref) is None
    process = context.Process(
        target=target,
        args=(*args, start, attempting, acquired, release, done),
    )
    entered_during_operation = False
    gated = False
    original = service._final_authority

    def gate_external_writer(  # type: ignore[no-untyped-def]
        draft_ref, required_current, inputs
    ):
        nonlocal entered_during_operation, gated
        if not gated:
            gated = True
            start.set()
            assert attempting.wait(timeout=5)
            entered_during_operation = acquired.wait(timeout=0.5)
        original(draft_ref, required_current, inputs)

    try:
        monkeypatch.setattr(service, "_final_authority", gate_external_writer)
        process.start()
        reference = (
            service.status(current, draft_ref)  # type: ignore[arg-type]
            if operation == "status"
            else service.audit(draft_ref)
        )

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
