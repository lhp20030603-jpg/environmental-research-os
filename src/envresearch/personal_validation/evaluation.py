"""Independent case evaluation and source-authority reopening."""

from __future__ import annotations

import hashlib

from envresearch.factory.contracts import ResearchFactoryRun
from envresearch.models.artifact import ArtifactRef
from envresearch.models.design import (
    EstimandSpecPayload,
    MethodCandidateRole,
    MethodCandidatesPayload,
    ReviewSeverity,
)
from envresearch.models.method_screening import MethodRequirementKind
from envresearch.personal_validation._strict import (
    CaseBehaviorObservation,
    CorrectStopExpectedBehavior,
    EvidenceChallengeExpectedBehavior,
    IncompatibilityExpectedBehavior,
    ObservationKind,
    SuccessfulRunExpectedBehavior,
    artifact_ref_key,
    materialize_id,
)
from envresearch.personal_validation.canonical_handoff import (
    CanonicalCaseSourceAuthority,
    reopen_case_source,
    source_ref,
)
from envresearch.personal_validation.contracts import (
    AttemptRootInventory,
    CompletedFactoryRunTarget,
    CorrectStopTarget,
    PersonalCanonicalCase,
    PersonalValidationAttempt,
    PersonalValidationProtocol,
)
from envresearch.personal_validation.errors import PersonalValidationIntegrityInvalid
from envresearch.personal_validation.events import (
    PersonalValidationEvent,
    PersonalWriterHistory,
    session_events,
)
from envresearch.personal_validation.private_store import PersonalValidationStore
from envresearch.personal_validation.review_contracts import CaseBehaviorEvaluation
from envresearch.personal_validation.snapshots import require_correct_stop_inventory
from envresearch.personal_validation.targets import model_ref
from envresearch.research.semantic_validation import artifact_ref_token

ExpectedBehavior = (
    SuccessfulRunExpectedBehavior
    | CorrectStopExpectedBehavior
    | IncompatibilityExpectedBehavior
    | EvidenceChallengeExpectedBehavior
)
ObservationKey = tuple[ObservationKind, str | None, str | None]


def matching_events(
    events: tuple[PersonalValidationEvent, ...], operation: str, reference: ArtifactRef
) -> tuple[PersonalValidationEvent, ...]:
    return tuple(
        event
        for event in events
        if event.operation == operation and event.object_ref == reference
    )


def attempt_session(store: PersonalValidationStore, attempt_ref: ArtifactRef) -> str:
    matches = matching_events(store.read_events(), "attempt-completed", attempt_ref)
    if len(matches) != 1:
        raise PersonalValidationIntegrityInvalid(
            "attempt completion authority is invalid",
            finding_kind="attempt-event-invalid",
        )
    return matches[0].session_id


def attempt_authority(
    store: PersonalValidationStore, session_id: str, attempt_ref: ArtifactRef
) -> tuple[
    PersonalWriterHistory,
    PersonalValidationAttempt,
    PersonalCanonicalCase,
    PersonalValidationProtocol,
]:
    history = store._writer_events()
    matches = matching_events(
        session_events(history.events, session_id), "attempt-completed", attempt_ref
    )
    if len(matches) != 1:
        raise PersonalValidationIntegrityInvalid(
            "attempt authority changed", finding_kind="attempt-event-invalid"
        )
    attempt = store.load(attempt_ref, PersonalValidationAttempt)
    case = store.load(attempt.case_ref, PersonalCanonicalCase)
    protocol = store.load(attempt.protocol_ref, PersonalValidationProtocol)
    bound = any(
        item.case_ref == attempt.case_ref and item.kind == case.kind
        for item in protocol.cases
    )
    if not bound:
        raise PersonalValidationIntegrityInvalid(
            "attempt protocol binding is invalid",
            finding_kind="protocol-case-binding-invalid",
        )
    return history, attempt, case, protocol


def derive_evaluation(
    store: PersonalValidationStore,
    attempt_ref: ArtifactRef,
    attempt: PersonalValidationAttempt,
    case: PersonalCanonicalCase,
) -> CaseBehaviorEvaluation:
    try:
        attempt = PersonalValidationAttempt.model_validate(
            attempt.model_dump(mode="python"), strict=True
        )
    except ValueError as error:
        raise PersonalValidationIntegrityInvalid(
            "attempt target failed strict source reopening",
            finding_kind="attempt-source-invalid",
        ) from error
    if (
        model_ref(attempt.attempt_id, attempt) != attempt_ref
        or store.load(attempt.case_ref, PersonalCanonicalCase) != case
    ):
        raise PersonalValidationIntegrityInvalid(
            "evaluation attempt or case reference is not exact",
            finding_kind="evaluation-source-substitution",
        )
    expected = _load_expected(store, case)
    inventory = store.load(attempt.attempt_inventory_ref, AttemptRootInventory)
    target_ref = (
        attempt.target.run_ref
        if isinstance(attempt.target, CompletedFactoryRunTarget)
        else attempt.target.inspection_ref
    )
    authority = reopen_case_source(store, attempt_ref)
    if (
        authority.target_ref != target_ref
        or authority.case_kind != case.kind
        or authority.attempt_ref != attempt_ref
    ):
        raise PersonalValidationIntegrityInvalid(
            "case source closure differs from exact attempt",
            finding_kind="case-source-authority-invalid",
        )
    observed: dict[ObservationKey, tuple[ArtifactRef, ...]] = {}
    semantic_match = True
    if isinstance(attempt.target, CorrectStopTarget):
        require_correct_stop_inventory(inventory)
        semantic_match = _observe_correct_stop(
            attempt,
            authority,
            expected,
            target_ref,
            observed,
        )
    else:
        semantic_match = _observe_completed(
            attempt,
            authority,
            expected,
            target_ref,
            observed,
        )
    observations = tuple(
        CaseBehaviorObservation(
            observation_kind=key[0],
            evidence_refs=observed[key],
            exact_code=key[1],
            exact_finding_kind=key[2],
        )
        for key in sorted(
            observed, key=lambda item: tuple(value or "" for value in item)
        )
    )
    requirement_keys = {
        (item.observation_kind, item.exact_code, item.exact_finding_kind)
        for item in expected.requirements
    }
    observation_keys = set(observed)
    payload: dict[str, object] = {
        "schema_version": "personal.case-behavior-evaluation.v1",
        "case_ref": attempt.case_ref,
        "attempt_ref": attempt_ref,
        "expected_behavior_ref": case.expected_behavior_ref,
        "target_ref": target_ref,
        "inventory_ref": attempt.attempt_inventory_ref,
        "verifier_version": "personal-case-verifier-v1",
        "observations": observations,
        "verdict": "expected-behavior-observed"
        if semantic_match and requirement_keys <= observation_keys
        else "behavior-deviation",
    }
    payload["evaluation_id"] = materialize_id("personal-evaluation-", payload)
    return CaseBehaviorEvaluation.model_validate(payload)


def _observe_correct_stop(
    attempt: PersonalValidationAttempt,
    authority: CanonicalCaseSourceAuthority,
    expected: ExpectedBehavior,
    target_ref: ArtifactRef,
    observed: dict[ObservationKey, tuple[ArtifactRef, ...]],
) -> bool:
    assert isinstance(attempt.target, CorrectStopTarget)
    inspection = attempt.target.inspection
    refs = tuple(
        sorted(
            (
                ArtifactRef(
                    artifact_id=item.finding_id,
                    artifact_version=1,
                    content_hash=hashlib.sha256(
                        item.model_dump_json().encode()
                    ).hexdigest(),
                )
                for item in authority.blocker_findings
            ),
            key=artifact_ref_key,
        )
    )
    blocking = tuple(
        item
        for item in authority.blocker_findings
        if not item.resolved and item.severity is ReviewSeverity.BLOCKING
    )
    if blocking and refs == inspection.findings:
        observed[
            ("correct-stop-blocker", inspection.stop_code, "design-blocking-finding")
        ] = tuple(sorted({target_ref, *refs}, key=artifact_ref_key))
    observed[("namespace-absent", None, None)] = (attempt.attempt_inventory_ref,)
    return isinstance(expected, CorrectStopExpectedBehavior) and (
        inspection.stop_code == expected.blocker_code
        and refs == inspection.findings
        and any(
            item.node_id == expected.blocker_gate
            and item.checkpoint_sha256 == expected.expected_checkpoint_sha256
            for item in inspection.checkpoints
        )
    )


def _observe_completed(
    attempt: PersonalValidationAttempt,
    authority: CanonicalCaseSourceAuthority,
    expected: ExpectedBehavior,
    target_ref: ArtifactRef,
    observed: dict[ObservationKey, tuple[ArtifactRef, ...]],
) -> bool:
    assert isinstance(attempt.target, CompletedFactoryRunTarget)
    run = attempt.target.run
    if run.binding_report.verdict == "coherent":
        observed[("factory-chain-coherent", None, None)] = (target_ref,)
    if run.release.audit_report.verdict == "clean":
        observed[("successor-release-clean", None, None)] = (run.release.audit_ref,)
    if isinstance(expected, IncompatibilityExpectedBehavior):
        return _observe_incompatibility(run, authority, expected, observed)
    if isinstance(expected, EvidenceChallengeExpectedBehavior):
        return _observe_challenge(run, authority, expected, observed)
    return isinstance(expected, SuccessfulRunExpectedBehavior)


def _observe_incompatibility(
    run: ResearchFactoryRun,
    authority: CanonicalCaseSourceAuthority,
    expected: IncompatibilityExpectedBehavior,
    observed: dict[ObservationKey, tuple[ArtifactRef, ...]],
) -> bool:
    if (
        authority.method_candidates_artifact is None
        or authority.estimand_artifact is None
    ):
        return False
    methods = MethodCandidatesPayload.model_validate(
        authority.method_candidates_artifact.payload
    )
    estimand = EstimandSpecPayload.model_validate(authority.estimand_artifact.payload)
    method_ref = source_ref(authority.method_candidates_artifact)
    estimand_ref = source_ref(authority.estimand_artifact)
    rejected = tuple(
        item for item in methods.candidates if item.role is MethodCandidateRole.REJECTED
    )
    valid_rdd = tuple(
        item
        for item in rejected
        if item.method_profile_ref.split("@", 1)[0] == "rdd"
        and item.rejection_evidence is not None
        and item.rejection_evidence.requirement_kind
        is MethodRequirementKind.FEATURE_SET
    )
    if valid_rdd:
        observed[
            (
                "method-rejected",
                "METHOD_DATA_INCOMPATIBLE",
                "missing-running-variable-cutoff",
            )
        ] = (method_ref,)
    primary = methods.primary.method_profile_ref.split("@", 1)[0]
    if primary == "hedonic" and methods.primary.estimand_compatible:
        observed[("compatible-method-retained", None, None)] = (method_ref,)
    plan = run.design.plan
    rejection = valid_rdd[0].rejection_evidence if valid_rdd else None
    unmet = rejection.requirement_refs if rejection is not None else ()
    return (
        primary == expected.retained_method
        and len(valid_rdd) == 1
        and valid_rdd[0].method_profile_ref.split("@", 1)[0] == expected.rejected_method
        and unmet == expected.unmet_requirements
        and estimand_ref == expected.estimand_anchor_ref
        and estimand_ref.content_hash == expected.estimand_sha256
        and methods.estimand_ref == artifact_ref_token(estimand_ref)
        and plan.estimand_ref == artifact_ref_token(estimand_ref)
        and plan.estimand == estimand
    )


def _observe_challenge(
    run: ResearchFactoryRun,
    authority: CanonicalCaseSourceAuthority,
    expected: EvidenceChallengeExpectedBehavior,
    observed: dict[ObservationKey, tuple[ArtifactRef, ...]],
) -> bool:
    release = run.release
    audit = authority.predecessor_audit
    revision = release.revision
    if audit is None or revision is None:
        return False
    for finding in audit.findings:
        key: ObservationKey = (
            "predecessor-audit-blocked",
            "PAPER_AUDIT_BLOCKED",
            finding.finding_kind,
        )
        observed[key] = tuple(
            sorted(
                {
                    *observed.get(key, ()),
                    revision.predecessor_audit_ref,
                    finding.draft_ref,
                },
                key=artifact_ref_key,
            )
        )
    audit_closure = tuple(
        (item.finding_id, item.finding_kind, item.code, item.target, item.claim_ids)
        for item in audit.findings
    )
    witnesses = tuple(
        (
            item.finding_id,
            item.finding_kind,
            item.code,
            item.predecessor_target,
            item.claim_ids,
        )
        for item in revision.closure_witnesses
    )
    closed = (
        audit.verdict == "blocked"
        and audit.audit_id == revision.predecessor_audit_ref.artifact_id
        and hashlib.sha256(audit.model_dump_json().encode()).hexdigest()
        == revision.predecessor_audit_ref.content_hash
        and audit_closure == witnesses
        and revision.successor_audit_ref == release.audit_ref
        and revision.successor_ref == release.draft_ref
        and release.revision_ref is not None
        and release.revision_ref in release.revision_refs
    )
    if closed:
        assert release.revision_ref is not None
        observed[("revision-closure-complete", None, None)] = (release.revision_ref,)
    return closed and tuple(sorted(item.finding_kind for item in audit.findings)) == (
        expected.predecessor_finding_kinds
    )


def _load_expected(
    store: PersonalValidationStore, case: PersonalCanonicalCase
) -> ExpectedBehavior:
    if case.kind == "successful-end-to-end":
        return store.load(case.expected_behavior_ref, SuccessfulRunExpectedBehavior)
    if case.kind == "correct-stop":
        return store.load(case.expected_behavior_ref, CorrectStopExpectedBehavior)
    if case.kind == "data-method-incompatibility":
        return store.load(case.expected_behavior_ref, IncompatibilityExpectedBehavior)
    return store.load(case.expected_behavior_ref, EvidenceChallengeExpectedBehavior)
