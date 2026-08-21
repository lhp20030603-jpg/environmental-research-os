"""Production-backed external authority exclusion for paper revisions."""

from __future__ import annotations

from multiprocessing import get_context
from pathlib import Path

import pytest
from paper_audit_process_fixtures import transition_writer_boundary_worker
from paper_draft_integration_fixtures import build_stack
from paper_draft_process_fixtures import citation_writer_boundary_worker
from test_paper_revision import _blocked_predecessor

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.paper.ledger import V031AcceptedEvidenceResolver
from envresearch.paper.revision import RevisionService


@pytest.mark.parametrize("authority", ("citation", "valuation"))
@pytest.mark.parametrize("operation", ("revise", "status"))
def test_real_external_writer_waits_for_whole_revision_success_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authority: str,
    operation: str,
) -> None:
    """Prove real citation and valuation writers wait through final validation."""
    stack = build_stack(tmp_path)
    audits, predecessor, _ = _blocked_predecessor(stack)
    service = RevisionService(audit_service=audits)
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
    revision_ref = (
        service.revise(predecessor, stack.candidate) if operation == "status" else None
    )
    process = context.Process(
        target=target,
        args=(*args, start, attempting, acquired, release, done),
    )
    entered_during_operation = False
    gated = False
    original = audits._final_authority

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
        monkeypatch.setattr(audits, "_final_authority", gate_external_writer)
        process.start()
        result = (
            service.status(revision_ref, predecessor)  # type: ignore[arg-type]
            if operation == "status"
            else service.revise(predecessor, stack.candidate)
        )

        assert result
        assert gated
        assert not entered_during_operation
        assert acquired.wait(timeout=5)
        release.set()
        assert done.wait(timeout=5)
        process.join(timeout=10)
        assert process.exitcode == 0
    finally:
        release.set()
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=10)
        stack.orchestrator.close()
