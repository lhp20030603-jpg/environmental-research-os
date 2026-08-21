"""V0.4 release boundary and exact-chain acceptance tests."""

from __future__ import annotations

from multiprocessing import get_context
from pathlib import Path

import pytest
from paper_audit_integration_support import (
    audit_service,
    install_reopening_resolver,
    publish_draft,
)
from paper_draft_integration_fixtures import advance_citation_generation, build_stack
from paper_draft_process_fixtures import process_arguments
from paper_release_integration_support import forge_draft
from paper_release_process_fixtures import (
    CRASH_EXIT_CODES,
    CrashPoint,
    release_worker,
)
from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.paper.errors import (
    PaperAuthorityInvalid,
    PaperIntegrityInvalid,
    PaperSupportInvalid,
)
from envresearch.paper.release import (
    PAPER_RELEASE_PENDING_SUBJECT,
    PAPER_RELEASE_SUBJECT,
    PaperReleaseCandidate,
    PaperReleaseService,
)


def test_release_contract_is_strict_frozen_and_requires_a_clean_exact_audit(
    tmp_path: Path,
) -> None:
    """Catch mutable, extensible, or blocked payloads crossing the V1 handoff."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        audits = audit_service(stack)
        audit_ref = audits.audit(draft_ref)
        service = PaperReleaseService(audit_service=audits)
        release_ref = service.build(audit_ref, draft_ref)
        release = service.status(release_ref)

        assert release.audit_report.verdict == "clean"
        assert release.audit_report.findings == ()
        with pytest.raises(ValidationError):
            PaperReleaseCandidate.model_validate(
                {**release.model_dump(mode="python"), "unexpected": True}
            )
        with pytest.raises(ValidationError):
            release.release_id = "changed"  # type: ignore[misc]
        with pytest.raises(ValidationError):
            PaperReleaseCandidate.model_validate(
                {
                    **release.model_dump(mode="python"),
                    "audit_report": release.audit_report.model_copy(
                        update={"verdict": "blocked"}
                    ),
                }
            )
    finally:
        stack.orchestrator.close()


def test_green_release_reopens_complete_exact_chain_and_returns_v1_handoff(
    tmp_path: Path,
) -> None:
    """Catch a release that drops lineage or returns a payload it did not reopen."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        audits = audit_service(stack)
        audit_ref = audits.audit(draft_ref)
        report = audits.status(audit_ref, draft_ref)
        service = PaperReleaseService(audit_service=audits)

        first = service.build(audit_ref, draft_ref)
        second = service.build(audit_ref, draft_ref)
        handoff_ref, handoff = service.handoff(first)

        assert second == first == handoff_ref
        assert handoff.audit_ref == audit_ref
        assert handoff.draft_ref == draft_ref
        assert handoff.map_ref == report.map_ref
        assert handoff.ledger_ref == report.ledger_ref
        assert handoff.citation_report_ref == report.citation_report_ref
        assert handoff.audit_report == report
        assert handoff.transitive_refs == report.transitive_refs
        assert handoff.analysis_refs == report.analysis_refs
        assert handoff.output_refs == report.output_refs
        assert service.registry.current(PAPER_RELEASE_SUBJECT) == first
    finally:
        stack.orchestrator.close()


def test_blocked_audit_is_an_audited_non_release_without_current_candidate(
    tmp_path: Path,
) -> None:
    """Catch a release rule that ignores independently accumulated findings."""
    from paper_audit_integration_support import forge_numeric_draft

    stack = build_stack(tmp_path)
    try:
        clean_ref = publish_draft(stack)
        blocked_ref = forge_numeric_draft(stack, clean_ref)
        audits = audit_service(stack)
        audit_ref = audits.audit(blocked_ref)
        assert audits.status(audit_ref, blocked_ref).verdict == "blocked"
        service = PaperReleaseService(audit_service=audits)

        with pytest.raises(PaperSupportInvalid) as caught:
            service.build(audit_ref, blocked_ref)

        assert caught.value.finding_kind == "audit-findings-open"
        assert service.registry.current(PAPER_RELEASE_SUBJECT) is None
    finally:
        stack.orchestrator.close()


def _release_process(
    stack,  # type: ignore[no-untyped-def]
    audit_ref: ArtifactRef,
    draft_ref: ArtifactRef,
    crash_point: CrashPoint | None,
    start: object,
    results: object,
):  # type: ignore[no-untyped-def]
    return get_context("spawn").Process(
        target=release_worker,
        args=(
            *process_arguments(stack),
            audit_ref.model_dump_json(),
            draft_ref.model_dump_json(),
            crash_point,
            start,
            results,
        ),
    )


def test_identical_process_builders_return_one_exact_release(tmp_path: Path) -> None:
    """Catch split-brain current candidates under independent processes."""
    stack = build_stack(tmp_path)
    context = get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = []
    try:
        draft_ref = publish_draft(stack)
        audits = audit_service(stack)
        audit_ref = audits.audit(draft_ref)
        processes = [
            _release_process(stack, audit_ref, draft_ref, None, start, results)
            for _ in range(2)
        ]
        for process in processes:
            process.start()
        start.set()
        outcomes = tuple(results.get(timeout=40) for _ in processes)
        for process in processes:
            process.join(timeout=40)
            assert process.exitcode == 0

        assert all(status == "ok" for status, _ in outcomes), outcomes
        refs = tuple(ArtifactRef.model_validate_json(item) for _, item in outcomes)
        assert refs[0] == refs[1]
        assert (
            PaperReleaseService(audit_service=audits).status(refs[0]).draft_ref
            == draft_ref
        )
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=10)
        results.close()
        results.join_thread()
        stack.orchestrator.close()


@pytest.mark.parametrize("crash_point", tuple(CRASH_EXIT_CODES))
def test_process_death_at_every_release_boundary_recovers_exact_generation(
    tmp_path: Path, crash_point: CrashPoint
) -> None:
    """Catch a torn release publication that cannot be restarted exactly."""
    stack = build_stack(tmp_path)
    context = get_context("spawn")
    start = context.Event()
    results = context.Queue()
    process = None
    try:
        draft_ref = publish_draft(stack)
        audits = audit_service(stack)
        audit_ref = audits.audit(draft_ref)
        service = PaperReleaseService(audit_service=audits)
        process = _release_process(
            stack, audit_ref, draft_ref, crash_point, start, results
        )
        process.start()
        start.set()
        process.join(timeout=40)
        assert process.exitcode == CRASH_EXIT_CODES[crash_point]

        if crash_point == "release-pending":
            with pytest.raises(PaperIntegrityInvalid):
                service.store.current()
            assert service.store.pending() is not None
            assert service.store.committed() is None
            before_retry = None
        else:
            before_retry = service.store.current()
        if crash_point == "release-current":
            assert before_retry is not None
            assert service.status(before_retry).draft_ref == draft_ref
        else:
            assert before_retry is None
        recovered = service.build(audit_ref, draft_ref)

        assert service.status(recovered).draft_ref == draft_ref
        assert service.build(audit_ref, draft_ref) == recovered
    finally:
        if process is not None and process.is_alive():
            process.terminate()
        if process is not None:
            process.join(timeout=10)
        results.close()
        results.join_thread()
        stack.orchestrator.close()


@pytest.mark.parametrize(
    "missing_subject", (PAPER_RELEASE_SUBJECT, PAPER_RELEASE_PENDING_SUBJECT)
)
def test_release_is_readable_only_when_prepared_and_commit_pointers_match(
    tmp_path: Path, missing_subject: str
) -> None:
    """Catch either half of a torn two-pointer release being exposed as current."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        audits = audit_service(stack)
        service = PaperReleaseService(audit_service=audits)
        release_ref = service.build(audits.audit(draft_ref), draft_ref)
        service.registry.files.unlink(Path("exit/current") / f"{missing_subject}.json")

        with pytest.raises(PaperIntegrityInvalid):
            service.status(release_ref)

        assert service.registry.current(missing_subject) is None
    finally:
        stack.orchestrator.close()


def test_status_does_not_repair_new_pending_over_old_commit(tmp_path: Path) -> None:
    """Catch read-only status hiding or repairing an interrupted new intent."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        audits = audit_service(stack)
        audit_ref = audits.audit(draft_ref)
        service = PaperReleaseService(audit_service=audits)
        release_ref = service.build(audit_ref, draft_ref)
        service.registry.set_current(PAPER_RELEASE_PENDING_SUBJECT, draft_ref)

        with pytest.raises(PaperIntegrityInvalid):
            service.status(release_ref)

        assert service.registry.current(PAPER_RELEASE_SUBJECT) == release_ref
        assert service.registry.current(PAPER_RELEASE_PENDING_SUBJECT) == draft_ref
        assert service.build(audit_ref, draft_ref) == release_ref
        assert service.registry.current(PAPER_RELEASE_PENDING_SUBJECT) == release_ref
    finally:
        stack.orchestrator.close()


def test_matching_non_release_pointer_pair_is_rejected(tmp_path: Path) -> None:
    """Catch pointer equality being accepted without reopening release identity."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        audits = audit_service(stack)
        service = PaperReleaseService(audit_service=audits)
        service.registry.set_current(PAPER_RELEASE_PENDING_SUBJECT, draft_ref)
        service.registry.set_current(PAPER_RELEASE_SUBJECT, draft_ref)

        with pytest.raises(PaperIntegrityInvalid):
            service.store.current()
    finally:
        stack.orchestrator.close()


@pytest.mark.parametrize(
    "attack",
    (
        "citation-mismatch",
        "numeric-contradiction",
        "unsupported-claim",
        "unit-overreach",
        "policy-overclaim",
    ),
)
def test_release_rejects_every_audited_fail_closed_draft_category(
    tmp_path: Path, attack: str
) -> None:
    """Catch release promotion that ignores any required audit category."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        forged_ref = forge_draft(stack, draft_ref, attack)
        audits = audit_service(stack)
        audit_ref = audits.audit(forged_ref)
        report = audits.status(audit_ref, forged_ref)
        assert report.verdict == "blocked" and report.findings
        service = PaperReleaseService(audit_service=audits)

        with pytest.raises(PaperSupportInvalid):
            service.build(audit_ref, forged_ref)

        assert service.store.current() is None
    finally:
        stack.orchestrator.close()


def test_release_rejects_stale_transition_or_superseded_analysis(
    tmp_path: Path,
) -> None:
    """Catch cached audit success after accepted analysis authority is withdrawn."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        audits = audit_service(stack)
        audit_ref = audits.audit(draft_ref)
        service = PaperReleaseService(audit_service=audits)
        stack.ledger_service.resolver.current = False  # type: ignore[attr-defined]

        with pytest.raises(PaperAuthorityInvalid):
            service.build(audit_ref, draft_ref)

        assert service.store.current() is None
    finally:
        stack.orchestrator.close()


def test_release_rejects_upstream_revision_after_audit(tmp_path: Path) -> None:
    """Catch a citation generation revision hidden behind a clean old audit."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        audits = audit_service(stack)
        audit_ref = audits.audit(draft_ref)
        advance_citation_generation(stack)
        service = PaperReleaseService(audit_service=audits)

        with pytest.raises(PaperAuthorityInvalid):
            service.build(audit_ref, draft_ref)

        assert service.store.current() is None
    finally:
        stack.orchestrator.close()


@pytest.mark.parametrize("kind", ("table", "figure"))
def test_release_rejects_mutated_bound_output_after_audit(
    tmp_path: Path, kind: str
) -> None:
    """Catch a clean cached report concealing changed table or figure bytes."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        analysis_root, report = install_reopening_resolver(stack)
        audits = audit_service(stack)
        audit_ref = audits.audit(draft_ref)
        binding = (
            stack.candidate.tables[0] if kind == "table" else stack.candidate.figures[0]
        )
        evidence = next(
            item for item in report.outputs if item.name == binding.output.name
        )
        output = analysis_root / evidence.relative_path
        output.chmod(0o600)
        output.write_bytes(output.read_bytes() + b"mutated-after-audit")
        service = PaperReleaseService(audit_service=audits)

        with pytest.raises(PaperIntegrityInvalid):
            service.build(audit_ref, draft_ref)

        assert service.store.current() is None
    finally:
        stack.orchestrator.close()
