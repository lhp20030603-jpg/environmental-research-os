"""Real Task 5 handoff and source-derived Task 6 evaluation authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from envresearch.models.artifact import ArtifactRef
from envresearch.models.design import DesignFinding, ReviewSeverity
from envresearch.personal_validation._strict import (
    AgentDispatchObservation,
    ReviewerBehavioralContract,
    canonical_json,
)
from envresearch.personal_validation.canonical_cases import (
    DEFAULT_PROTOCOL_ROOT,
    DisposableAttemptRoots,
    LoadedProtocol,
    load_protocol_v1,
    run_case,
)
from envresearch.personal_validation.contracts import (
    CASE_ORDER,
    PersonalValidationAttempt,
    SystemSnapshot,
)
from envresearch.personal_validation.errors import PersonalValidationIntegrityInvalid
from envresearch.personal_validation.evaluation import derive_evaluation
from envresearch.personal_validation.private_store import PersonalValidationStore
from envresearch.personal_validation.review_contracts import (
    AgentReviewResponse,
    CaseBehaviorEvaluation,
    ExternalAccessRecord,
    ExternalAccessRequest,
    ReviewAssignment,
)
from envresearch.personal_validation.roots import RootExclusionSet
from envresearch.personal_validation.service import PersonalValidationService
from envresearch.personal_validation.targets import model_ref

SHA = "a" * 64


@dataclass(slots=True)
class RealCase:
    loaded: LoadedProtocol
    store: PersonalValidationStore
    roots: DisposableAttemptRoots
    service: PersonalValidationService
    attempt_ref: ArtifactRef
    attempt: PersonalValidationAttempt
    position: int

    def close(self) -> None:
        self.roots.close()
        self.store.close()


def _real_case(
    tmp_path: Path, kind: str, *, publish_protocol_contracts: bool = False
) -> RealCase:
    loaded = load_protocol_v1()
    repository = DEFAULT_PROTOCOL_ROOT.parents[2]
    git_common = tmp_path / "git-common"
    vault = tmp_path / "vault"
    git_common.mkdir(parents=True)
    vault.mkdir(parents=True)
    exclusions = RootExclusionSet(
        repository=repository,
        git_common_dir=git_common,
        worktrees=(repository,),
        obsidian_roots=(vault,),
    )
    store = PersonalValidationStore.create(tmp_path / "private", exclusions)
    for snapshot in loaded.input_snapshots:
        store.publish(snapshot.snapshot_id, snapshot)
    for case in loaded.cases:
        store.publish(case.case_id, case)
    store.publish(loaded.protocol.protocol_id, loaded.protocol)
    position = tuple(case.kind for case in loaded.cases).index(kind)
    if publish_protocol_contracts:
        expected = loaded.expected_behaviors[position]
        reviewer = loaded.reviewer_contracts[position]
        store.publish(expected.behavior_id, expected)
        store.publish(reviewer.contract_id, reviewer)
    system_payload: dict[str, object] = {
        "schema_version": "personal.system-snapshot.v1",
        "git_commit": "task6-review-authority",
        "execution_tree_sha256": SHA,
        "uv_lock_sha256": SHA,
        "capability_manifest_sha256": SHA,
        "method_profile_sha256": SHA,
        "protocol_ref": loaded.protocol_ref,
        "runtime_versions": (("python", "3.13"),),
        "clean_worktree": True,
    }
    from envresearch.personal_validation._strict import materialize_id

    system_payload["snapshot_id"] = materialize_id(
        "personal-system-snapshot-", system_payload
    )
    system = SystemSnapshot.model_validate(system_payload)
    system_ref = store.publish(system.snapshot_id, system)
    case_ref = loaded.protocol.cases[position].case_ref
    roots = DisposableAttemptRoots.for_case(
        case_ref=case_ref,
        repository_root=repository,
        protocol_ref=loaded.protocol_ref,
        store=store,
        exclusions=exclusions,
        session_nonce=f"authority-{kind}",
        system_snapshot_ref=system_ref,
    )
    try:
        prepared = run_case(case_ref, roots)
        attempt = store.load(prepared.attempt_ref, PersonalValidationAttempt)
        factory = (
            roots.research.factory_service(roots.paper.release_service)
            if attempt.target.target_type == "completed-factory-run"
            else None
        )
        service = PersonalValidationService(
            store=store,
            factory_service=factory,
            session_nonce=f"authority-{kind}",
            system_snapshot_ref=system_ref,
            attempt_inventory_ref=attempt.attempt_inventory_ref,
        )
        return RealCase(
            loaded, store, roots, service, prepared.attempt_ref, attempt, position
        )
    except BaseException:
        roots.close()
        store.close()
        raise


@pytest.mark.parametrize("kind", CASE_ORDER)
def test_real_task5_handoff_publishes_exact_contract_and_task6_flow(
    tmp_path: Path, kind: str
) -> None:
    case = _real_case(tmp_path, kind)
    try:
        prepared = case.service.prepare_bundle(case.attempt_ref, role="scientific")
        assert (
            case.store.load(
                prepared.bundle.behavioral_contract_ref, ReviewerBehavioralContract
            )
            == case.loaded.reviewer_contracts[case.position]
        )
        evaluation_ref = case.service.evaluate_case(case.attempt_ref)
        evaluation = case.store.load(evaluation_ref, CaseBehaviorEvaluation)
        assert evaluation.verdict == "expected-behavior-observed"
        assignment_ref = case.service.assign_review(
            case.attempt_ref,
            prepared.bundle_ref,
            role="scientific",
            invocation_id=f"real-{kind}",
        )
        observed = AgentDispatchObservation(
            schema_version="personal.agent-dispatch-observation.v1",
            invocation_id=f"real-{kind}",
            observed_model_id="review-model-v1",
            observed_runtime_id="review-runtime-v1",
            dispatched_at=datetime(2026, 8, 21, tzinfo=UTC),
        )
        dispatch_ref = case.service.record_dispatch(
            assignment_ref, canonical_json(observed.model_dump(mode="json"))
        )
        response = AgentReviewResponse(
            schema_version="personal.agent-review-response.v1",
            role="scientific",
            findings=(),
            external_access_requests=(),
            completion_status="complete",
        )
        assert case.service.record_review(
            assignment_ref,
            dispatch_ref,
            canonical_json(response.model_dump(mode="json")),
        ).publication_ref.artifact_id.startswith("personal-review-publication-")
    finally:
        case.close()


def test_incompatibility_target_substitution_cannot_receive_oracle_credit(
    tmp_path: Path,
) -> None:
    incompatible = _real_case(
        tmp_path / "incompatible",
        "data-method-incompatibility",
        publish_protocol_contracts=True,
    )
    successful = _real_case(
        tmp_path / "successful",
        "successful-end-to-end",
        publish_protocol_contracts=True,
    )
    try:
        substituted = incompatible.attempt.model_copy(
            update={"target": successful.attempt.target}
        )
        with pytest.raises(PersonalValidationIntegrityInvalid, match="source"):
            derive_evaluation(
                incompatible.store,
                model_ref(substituted.attempt_id, substituted),
                substituted,
                incompatible.loaded.cases[incompatible.position],
            )
    finally:
        successful.close()
        incompatible.close()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("primary_method_profile_ref", "rdd@0.2.0"),
        ("estimand_ref", "estimand-spec@999:forged"),
        ("estimand", None),
    ),
)
def test_incompatibility_semantic_plan_mutation_is_observed(
    tmp_path: Path, field: str, value: object
) -> None:
    case = _real_case(
        tmp_path, "data-method-incompatibility", publish_protocol_contracts=True
    )
    try:
        target = case.attempt.target
        assert target.target_type == "completed-factory-run"
        changed_plan = target.run.design.plan.model_copy(update={field: value})
        changed_design = target.run.design.model_copy(update={"plan": changed_plan})
        changed_run = target.run.model_copy(update={"design": changed_design})
        changed_target = target.model_copy(update={"run": changed_run})
        changed_attempt = case.attempt.model_copy(update={"target": changed_target})
        with pytest.raises(PersonalValidationIntegrityInvalid, match="source"):
            derive_evaluation(
                case.store,
                model_ref(changed_attempt.attempt_id, changed_attempt),
                changed_attempt,
                case.loaded.cases[case.position],
            )
    finally:
        case.close()


@pytest.mark.parametrize("mutation", ["target", "witness", "successor-audit"])
def test_challenge_revision_semantic_mutation_is_observed(
    tmp_path: Path, mutation: str
) -> None:
    case = _real_case(
        tmp_path, "evidence-citation-challenge", publish_protocol_contracts=True
    )
    try:
        target = case.attempt.target
        assert target.target_type == "completed-factory-run"
        revision = target.run.release.revision
        assert revision is not None
        if mutation == "target":
            witness = revision.closure_witnesses[0].model_copy(
                update={
                    "predecessor_target": revision.closure_witnesses[
                        0
                    ].predecessor_target.model_copy(update={"start": 0, "end": 1})
                }
            )
            revision = revision.model_copy(update={"closure_witnesses": (witness,)})
        elif mutation == "witness":
            revision = revision.model_copy(update={"closure_witnesses": ()})
        else:
            revision = revision.model_copy(
                update={
                    "successor_audit_ref": revision.successor_audit_ref.model_copy(
                        update={"content_hash": "b" * 64}
                    )
                }
            )
        release = target.run.release.model_copy(update={"revision": revision})
        run = target.run.model_copy(update={"release": release})
        changed = case.attempt.model_copy(
            update={"target": target.model_copy(update={"run": run})}
        )
        with pytest.raises(PersonalValidationIntegrityInvalid, match="source"):
            derive_evaluation(
                case.store,
                model_ref(changed.attempt_id, changed),
                changed,
                case.loaded.cases[case.position],
            )
    finally:
        case.close()


def test_correct_stop_finding_kind_is_observed_from_finding_authority(
    tmp_path: Path,
) -> None:
    case = _real_case(tmp_path, "correct-stop", publish_protocol_contracts=True)
    try:
        finding = DesignFinding(
            finding_id="not-a-blocker",
            severity=ReviewSeverity.MAJOR,
            resolved=False,
            finding="A nonblocking finding was substituted.",
            evidence_refs=("evidence-1",),
            residual_risk="The exact blocker identity is absent.",
        )
        finding_ref = case.store.publish(finding.finding_id, finding)
        target = case.attempt.target
        assert target.target_type == "correct-stop"
        inspection = target.inspection.model_copy(update={"findings": (finding_ref,)})
        changed = case.attempt.model_copy(
            update={"target": target.model_copy(update={"inspection": inspection})}
        )
        with pytest.raises(PersonalValidationIntegrityInvalid, match="source"):
            derive_evaluation(
                case.store,
                model_ref(changed.attempt_id, changed),
                changed,
                case.loaded.cases[case.position],
            )
    finally:
        case.close()


@pytest.mark.parametrize("outcome", ["success", "failed"])
def test_external_access_provenance_is_minted_only_from_orchestrator_receipt(
    tmp_path: Path, outcome: str
) -> None:
    case = _real_case(
        tmp_path, "successful-end-to-end", publish_protocol_contracts=True
    )
    try:
        bundle = case.service.prepare_bundle(case.attempt_ref, role="scientific")
        assignment_ref = case.service.assign_review(
            case.attempt_ref,
            bundle.bundle_ref,
            role="scientific",
            invocation_id=f"external-{outcome}",
        )
        assignment = case.store.load(assignment_ref, ReviewAssignment)
        observed = AgentDispatchObservation(
            schema_version="personal.agent-dispatch-observation.v1",
            invocation_id=assignment.invocation_id,
            observed_model_id="review-model-v1",
            observed_runtime_id="review-runtime-v1",
            dispatched_at=datetime(2026, 8, 21, tzinfo=UTC),
        )
        review_dispatch_ref = case.service.record_dispatch(
            assignment_ref, canonical_json(observed.model_dump(mode="json"))
        )
        request = ExternalAccessRequest(
            provider="web",
            operation="read-official-documentation",
            source_locator="https://example.org/policy",
            local_finding_keys=(),
        )
        access_dispatch_ref = case.service.record_external_access_dispatch(
            assignment_ref,
            assignment_ref,
            canonical_json(request.model_dump(mode="json")),
        )
        access_receipt_ref = case.service.record_external_access_receipt(
            access_dispatch_ref,
            b'{"status":"observed"}',
            outcome=outcome,
            observed_at=datetime(2026, 8, 21, tzinfo=UTC),
        )
        response = AgentReviewResponse(
            schema_version="personal.agent-review-response.v1",
            role="scientific",
            findings=(),
            external_access_requests=(request,),
            completion_status=(
                "complete" if outcome == "success" else "external-verification-pending"
            ),
        )
        recorded = case.service.record_review(
            assignment_ref,
            review_dispatch_ref,
            canonical_json(response.model_dump(mode="json")),
            external_access_receipt_refs=(access_receipt_ref,),
        )
        if outcome == "success":
            record = case.store.load(
                recorded.access_record_refs[0], ExternalAccessRecord
            )
            assert record.receipt_ref == access_receipt_ref
        else:
            assert recorded.access_record_refs == ()
    finally:
        case.close()
