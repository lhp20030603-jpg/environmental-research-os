"""Crash recovery for every Task 6 object-to-event transaction."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from personal_validation_review_fixtures import (
    CRASH_EXIT,
    ReviewCase,
    Role,
    make_review_case,
    open_process_service,
    run_transaction,
    terminate_transaction_process,
)

from envresearch.personal_validation._strict import (
    AgentDispatchObservation,
    canonical_json,
)
from envresearch.personal_validation.contracts import materialize_id
from envresearch.personal_validation.errors import (
    PersonalValidationAuthorityInvalid,
    PersonalValidationIntegrityInvalid,
)
from envresearch.personal_validation.evaluation import (
    attempt_authority,
    attempt_session,
)
from envresearch.personal_validation.report import objects, publish_event_object
from envresearch.personal_validation.review_bundle import ReviewBundle
from envresearch.personal_validation.review_contracts import (
    AgentFindingResponse,
    CaseBehaviorEvaluation,
    ExternalAccessRequest,
)


class InjectedCrash(RuntimeError):
    pass


@pytest.fixture
def recovery_case(tmp_path: Path):  # type: ignore[no-untyped-def]
    case = make_review_case(tmp_path)
    try:
        yield case
    finally:
        case.close()


def _arm_crash(case: ReviewCase, boundary: str) -> None:
    fired = False

    def inject(observed: str) -> None:
        nonlocal fired
        if not fired and observed == boundary:
            fired = True
            raise InjectedCrash(boundary)

    case.service.failure_injector = inject


def _store_snapshot(case: ReviewCase) -> tuple[tuple[str, int, bytes], ...]:
    root = case.store.root.lexical_path
    return tuple(
        (str(path.relative_to(root)), path.stat().st_mode, path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def _assert_replay(action: Callable[[], object]) -> object:
    event_count = None
    recovered = action()
    if hasattr(recovered, "completion_event_id"):
        event_count = recovered.completion_event_id
    replayed = action()
    assert replayed == recovered
    if event_count is not None:
        assert replayed.completion_event_id == event_count
    return recovered


def _divergent_finding(bundle: ReviewBundle) -> AgentFindingResponse:
    return AgentFindingResponse(
        local_finding_key="divergent-finding",
        domain="method-identification",
        severity="minor",
        target_refs=bundle.target_refs,
        evidence_refs=(bundle.evidence_refs[0],),
        problem="A divergent retry changed the raw response.",
        impact="The orphan would no longer represent an exact retry.",
        repair_proposal="Retry the exact original canonical response bytes.",
    )


def test_bundle_orphan_rejects_divergence_then_recovers_exactly(
    recovery_case: ReviewCase,
) -> None:
    action = lambda: recovery_case.service.prepare_bundle(
        recovery_case.attempt_ref, role="scientific"
    )
    _arm_crash(recovery_case, "bundle-object")
    with pytest.raises(InjectedCrash, match="bundle-object"):
        action()
    orphan_ref, orphan = objects(
        recovery_case.store, "personal-review-bundle-", ReviewBundle
    )[0]
    payload = orphan.model_dump(mode="python", exclude={"bundle_id"})
    payload["projection_policy_sha256"] = "f" * 64
    payload["bundle_id"] = materialize_id("personal-review-bundle-", payload)
    divergent = ReviewBundle.model_validate(payload)
    before = _store_snapshot(recovery_case)
    session_id = attempt_session(recovery_case.store, recovery_case.attempt_ref)
    with recovery_case.store.session_lock(session_id):
        history, *_ = attempt_authority(
            recovery_case.store, session_id, recovery_case.attempt_ref
        )
        with pytest.raises(PersonalValidationIntegrityInvalid, match="orphaned"):
            publish_event_object(
                recovery_case.service,
                history,
                session_id,
                "bundle-published",
                divergent.bundle_id,
                divergent,
                slot=lambda item: (
                    item.attempt_ref == recovery_case.attempt_ref
                    and item.role == "scientific"
                ),
                boundary="bundle",
            )
    assert _store_snapshot(recovery_case) == before
    recovered = _assert_replay(action)
    assert recovered.bundle_ref == orphan_ref


@pytest.mark.parametrize(
    ("role", "boundary"),
    (("scientific", "review-object"), ("evidence", "publication-object")),
)
def test_review_closure_orphan_rejects_raw_divergence_then_recovers(
    recovery_case: ReviewCase, role: Role, boundary: str
) -> None:
    prepared = recovery_case.prepare(role)
    assignment = recovery_case.assign(role, invocation_id=f"recover-{role}")
    receipt = recovery_case.record_dispatch(assignment)
    raw = recovery_case.canonical_response_bytes(role=role)
    action = lambda: recovery_case.service.record_review(assignment, receipt, raw)
    _arm_crash(recovery_case, boundary)
    with pytest.raises(InjectedCrash, match=boundary):
        action()
    divergent = recovery_case.canonical_response_bytes(
        role=role,
        findings=(_divergent_finding(prepared.bundle),),
    )
    before = _store_snapshot(recovery_case)
    with pytest.raises(PersonalValidationAuthorityInvalid, match="divergent"):
        recovery_case.service.record_review(assignment, receipt, divergent)
    assert _store_snapshot(recovery_case) == before
    _assert_replay(action)


def test_evaluation_orphan_rejects_divergence_then_recovers_exactly(
    recovery_case: ReviewCase,
) -> None:
    action = lambda: recovery_case.service.evaluate_case(recovery_case.attempt_ref)
    _arm_crash(recovery_case, "evaluation-object")
    with pytest.raises(InjectedCrash, match="evaluation-object"):
        action()
    orphan_ref, evaluation = objects(
        recovery_case.store, "personal-evaluation-", CaseBehaviorEvaluation
    )[0]
    payload = evaluation.model_dump(mode="python", exclude={"evaluation_id"})
    payload["verdict"] = "behavior-deviation"
    payload["evaluation_id"] = materialize_id("personal-evaluation-", payload)
    divergent = CaseBehaviorEvaluation.model_validate(payload)
    before = _store_snapshot(recovery_case)
    session_id = attempt_session(recovery_case.store, recovery_case.attempt_ref)
    with recovery_case.store.session_lock(session_id):
        history, *_ = attempt_authority(
            recovery_case.store, session_id, recovery_case.attempt_ref
        )
        with pytest.raises(PersonalValidationIntegrityInvalid, match="orphaned"):
            publish_event_object(
                recovery_case.service,
                history,
                session_id,
                "evaluation-published",
                divergent.evaluation_id,
                divergent,
                slot=lambda item: item.attempt_ref == recovery_case.attempt_ref,
                boundary="evaluation",
            )
    assert _store_snapshot(recovery_case) == before
    assert _assert_replay(action) == orphan_ref


def test_report_orphan_rejects_role_substitution_then_recovers_exactly(
    recovery_case: ReviewCase,
) -> None:
    scientific = recovery_case.record("scientific", invocation_id="report-science")
    evidence = recovery_case.record("evidence", invocation_id="report-evidence")
    synthesis = recovery_case.record(
        "synthesis",
        invocation_id="report-synthesis",
        primary_publication_refs=(
            scientific.publication_ref,
            evidence.publication_ref,
        ),
    )
    evaluation_ref = recovery_case.service.evaluate_case(recovery_case.attempt_ref)
    action = lambda: recovery_case.service.finalize_report(
        recovery_case.attempt_ref,
        evaluation_ref,
        scientific,
        evidence,
        synthesis,
    )
    _arm_crash(recovery_case, "report-object")
    with pytest.raises(InjectedCrash, match="report-object"):
        action()
    before = _store_snapshot(recovery_case)
    with pytest.raises(PersonalValidationAuthorityInvalid, match="role"):
        recovery_case.service.finalize_report(
            recovery_case.attempt_ref,
            evaluation_ref,
            evidence,
            scientific,
            synthesis,
        )
    assert _store_snapshot(recovery_case) == before
    _assert_replay(action)


@pytest.mark.parametrize(
    ("action", "boundary"),
    (
        ("bundle", "bundle-object"),
        ("bundle", "bundle-event"),
        ("evaluation", "evaluation-object"),
        ("evaluation", "evaluation-event"),
        ("dispatch", "dispatch-object"),
        ("dispatch", "dispatch-event"),
    ),
)
def test_spawn_termination_recovers_exact_transaction_with_fresh_service(
    recovery_case: ReviewCase,
    action: str,
    boundary: str,
) -> None:
    config = recovery_case.process_config()
    arguments = recovery_case.transaction_arguments(action, boundary)
    assert (
        terminate_transaction_process(config, action, boundary, arguments) == CRASH_EXIT
    )
    fresh_store, fresh = open_process_service(config)
    try:
        first = run_transaction(fresh, action, arguments)
        assert run_transaction(fresh, action, arguments) == first
    finally:
        fresh_store.close()


def test_spawned_external_dispatch_orphan_rejects_same_slot_divergence(
    recovery_case: ReviewCase,
) -> None:
    prepared = recovery_case.prepare("scientific")
    assignment = recovery_case.service.assign_review(
        recovery_case.attempt_ref,
        prepared.bundle_ref,
        role="scientific",
        invocation_id="spawn-external-dispatch",
    )
    request = ExternalAccessRequest(
        provider="web",
        operation="read-official-documentation",
        source_locator="https://example.org/policy",
        local_finding_keys=(),
    )
    raw = canonical_json(request.model_dump(mode="json"))
    config = recovery_case.process_config()
    assert (
        terminate_transaction_process(
            config,
            "external-dispatch",
            "external-dispatch-object",
            (assignment, assignment, raw),
        )
        == CRASH_EXIT
    )
    fresh_store, fresh = open_process_service(config)
    try:
        divergent = request.model_copy(update={"local_finding_keys": ("finding-1",)})
        before = _store_snapshot(recovery_case)
        with pytest.raises(
            (PersonalValidationAuthorityInvalid, PersonalValidationIntegrityInvalid),
            match="divergent|orphan",
        ):
            fresh.record_external_access_dispatch(
                assignment,
                assignment,
                canonical_json(divergent.model_dump(mode="json")),
            )
        assert _store_snapshot(recovery_case) == before
        first = fresh.record_external_access_dispatch(assignment, assignment, raw)
        assert (
            fresh.record_external_access_dispatch(assignment, assignment, raw) == first
        )
    finally:
        fresh_store.close()


@pytest.mark.parametrize(
    ("action", "boundary"),
    (
        ("assignment", "assignment-object"),
        ("external-dispatch", "external-dispatch-event"),
        ("external-receipt", "external-receipt-object"),
        ("external-receipt", "external-receipt-event"),
        ("review", "review-object"),
        ("review", "publication-object"),
        ("review", "publication-event"),
        ("report", "report-object"),
        ("report", "report-event"),
    ),
)
def test_spawn_termination_recovers_remaining_task6_boundaries(
    recovery_case: ReviewCase,
    action: str,
    boundary: str,
) -> None:
    arguments = recovery_case.transaction_arguments(action, boundary)
    config = recovery_case.process_config()
    assert (
        terminate_transaction_process(config, action, boundary, arguments) == CRASH_EXIT
    )
    fresh_store, fresh = open_process_service(config)
    try:
        first = run_transaction(fresh, action, arguments)
        assert run_transaction(fresh, action, arguments) == first
    finally:
        fresh_store.close()


@pytest.mark.parametrize(
    ("action", "boundary"),
    (
        ("dispatch", "dispatch-object"),
        ("external-receipt", "external-receipt-object"),
    ),
)
def test_spawned_orphan_rejects_dispatch_or_receipt_divergence(
    recovery_case: ReviewCase, action: str, boundary: str
) -> None:
    arguments = recovery_case.transaction_arguments(action, boundary)
    config = recovery_case.process_config()
    assert (
        terminate_transaction_process(config, action, boundary, arguments) == CRASH_EXIT
    )
    changed = list(arguments)
    if action == "dispatch":
        observed = AgentDispatchObservation.model_validate_json(changed[1])
        changed[1] = canonical_json(
            observed.model_copy(
                update={"observed_model_id": "divergent-model"}
            ).model_dump(mode="json")
        )
    else:
        changed[2] = "failed"
    fresh_store, fresh = open_process_service(config)
    try:
        before = _store_snapshot(recovery_case)
        with pytest.raises(
            (PersonalValidationAuthorityInvalid, PersonalValidationIntegrityInvalid),
            match="divergent|orphan",
        ):
            run_transaction(fresh, action, tuple(changed))
        assert _store_snapshot(recovery_case) == before
        exact = run_transaction(fresh, action, arguments)
        assert run_transaction(fresh, action, arguments) == exact
    finally:
        fresh_store.close()
