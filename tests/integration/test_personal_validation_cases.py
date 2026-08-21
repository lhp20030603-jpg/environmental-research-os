"""Canonical Personal Validation protocol and case-runner boundaries."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from envresearch.models.artifact import ArtifactRef
from envresearch.models.design import MethodCandidatesPayload
from envresearch.personal_validation.canonical_cases import (
    DEFAULT_PROTOCOL_ROOT,
    DisposableAttemptRoots,
    load_protocol_v1,
    run_case,
)
from envresearch.personal_validation.case_stops import require_no_oracle_leak
from envresearch.personal_validation.contracts import (
    CASE_ORDER,
    PersonalCanonicalCase,
    PersonalCanonicalCaseBinding,
    PersonalValidationAttempt,
    PersonalValidationProtocol,
    SystemSnapshot,
    materialize_id,
)
from envresearch.personal_validation.errors import PersonalValidationIntegrityInvalid
from envresearch.personal_validation.private_store import PersonalValidationStore
from envresearch.personal_validation.roots import RootExclusionSet

SHA = "a" * 64


def test_protocol_reopens_exact_four_case_and_policy_closure() -> None:
    loaded = load_protocol_v1()

    assert tuple(case.kind for case in loaded.cases) == (
        "successful-end-to-end",
        "correct-stop",
        "data-method-incompatibility",
        "evidence-citation-challenge",
    )
    assert tuple(item.case_kind for item in loaded.expected_behaviors) == tuple(
        case.kind for case in loaded.cases
    )
    assert tuple(item.case_kind for item in loaded.reviewer_contracts) == tuple(
        case.kind for case in loaded.cases
    )
    assert loaded.protocol_ref.artifact_id == loaded.protocol.protocol_id
    assert loaded.protocol.scope == "personal-advisory-only"
    assert loaded.protocol.blocks == ()
    assert tuple(
        artifact.policy_kind
        for artifact in (
            loaded.policy_artifacts.scientific,
            loaded.policy_artifacts.evidence,
            loaded.policy_artifacts.synthesis,
            loaded.policy_artifacts.external_access,
            loaded.policy_artifacts.rubric,
            loaded.policy_artifacts.report_schema,
        )
    ) == (
        "scientific",
        "evidence",
        "synthesis",
        "external-access",
        "rubric",
        "report-schema",
    )


@pytest.mark.parametrize(
    "relative",
    (
        "correct-stop/case.json",
        "correct-stop/expected-behavior.json",
        "correct-stop/reviewer-contract.json",
        "policies/scientific.json",
        "rubric.json",
        "report-schema.json",
    ),
)
def test_protocol_rejects_transitive_byte_mutation(
    tmp_path: Path, relative: str
) -> None:
    copied = tmp_path / "v1"
    shutil.copytree(DEFAULT_PROTOCOL_ROOT, copied)
    target = copied / relative
    raw = target.read_bytes()
    target.write_bytes(raw[:-2] + (b" " if raw[-2:-1] != b" " else b"\t") + raw[-1:])

    with pytest.raises(PersonalValidationIntegrityInvalid, match="protocol"):
        load_protocol_v1(copied)


def test_reviewer_contract_cannot_embed_expected_behavior_oracle(
    tmp_path: Path,
) -> None:
    copied = tmp_path / "v1"
    shutil.copytree(DEFAULT_PROTOCOL_ROOT, copied)
    target = copied / "correct-stop/reviewer-contract.json"
    text = target.read_text(encoding="utf-8")
    target.write_text(
        text.replace(
            "Judge only the observable stop evidence.",
            "Judge only the observable stop evidence. RESEARCH_RUN_BLOCKED",
        ),
        encoding="utf-8",
    )

    with pytest.raises(PersonalValidationIntegrityInvalid, match="protocol"):
        load_protocol_v1(copied)


@pytest.mark.parametrize(
    "seeded_value",
    (
        "rdd",
        "hedonic",
        "review-design",
        "known_cutoff",
        "running_variable",
        "policy-overclaim",
        "RESEARCH_RUN_BLOCKED",
    ),
)
def test_rematerialized_reviewer_contract_rejects_every_oracle_value(
    seeded_value: str,
) -> None:
    loaded = load_protocol_v1()
    behavior = next(
        item
        for item in loaded.expected_behaviors
        if seeded_value in item.model_dump_json()
    )
    reviewer = loaded.reviewer_contracts[
        tuple(item.case_kind for item in loaded.expected_behaviors).index(
            behavior.case_kind
        )
    ]
    payload = reviewer.model_dump(mode="python", exclude={"contract_id"})
    payload["review_question"] = f"Judge observable evidence tagged {seeded_value}."
    payload["contract_id"] = materialize_id("personal-reviewer-contract-", payload)
    rematerialized = type(reviewer).model_validate(payload)

    with pytest.raises(ValueError, match="oracle"):
        require_no_oracle_leak(
            rematerialized.model_dump_json().encode(),
            behavior,
            loaded.cases[
                tuple(case.kind for case in loaded.cases).index(behavior.case_kind)
            ].expected_behavior_ref,
        )


@pytest.mark.parametrize("field", ("artifact_id", "content_hash"))
def test_rematerialized_reviewer_rejects_expected_behavior_ref_fields(
    field: str,
) -> None:
    loaded = load_protocol_v1()
    position = 1
    behavior = loaded.expected_behaviors[position]
    reviewer = loaded.reviewer_contracts[position]
    expected_ref = loaded.cases[position].expected_behavior_ref
    payload = reviewer.model_dump(mode="python", exclude={"contract_id"})
    payload["review_question"] += f" Tagged {getattr(expected_ref, field)}."
    payload["contract_id"] = materialize_id("personal-reviewer-contract-", payload)
    rematerialized = type(reviewer).model_validate(payload)

    with pytest.raises(ValueError, match="oracle"):
        require_no_oracle_leak(
            rematerialized.model_dump_json().encode(), behavior, expected_ref
        )


@pytest.mark.parametrize(
    ("kind", "field", "changed"),
    (
        (
            "successful-end-to-end",
            "prohibited_outcomes",
            ("different-success-meaning",),
        ),
        ("correct-stop", "blocker_gate", "final-gate"),
        (
            "data-method-incompatibility",
            "unmet_requirements",
            ("known_cutoff",),
        ),
        (
            "evidence-citation-challenge",
            "predecessor_finding_kinds",
            ("different-finding",),
        ),
    ),
)
def test_self_consistent_semantic_mutation_cannot_redefine_v1(
    tmp_path: Path, kind: str, field: str, changed: object
) -> None:
    copied = tmp_path / "v1"
    shutil.copytree(DEFAULT_PROTOCOL_ROOT, copied)
    loaded = load_protocol_v1(copied)
    position = tuple(case.kind for case in loaded.cases).index(kind)
    behavior = loaded.expected_behaviors[position]
    behavior_payload = behavior.model_dump(mode="python", exclude={"behavior_id"})
    behavior_payload[field] = changed
    behavior_payload["behavior_id"] = materialize_id(
        "personal-expected-behavior-", behavior_payload
    )
    changed_behavior = type(behavior).model_validate(behavior_payload)
    behavior_bytes = changed_behavior.model_dump_json().encode()
    (copied / kind / "expected-behavior.json").write_bytes(behavior_bytes)

    case = loaded.cases[position]
    case_payload = case.model_dump(mode="python", exclude={"case_id"})
    case_payload["expected_behavior_ref"] = ArtifactRef(
        artifact_id=changed_behavior.behavior_id,
        artifact_version=1,
        content_hash=hashlib.sha256(behavior_bytes).hexdigest(),
    )
    case_payload["case_id"] = materialize_id("personal-case-", case_payload)
    changed_case = PersonalCanonicalCase.model_validate(case_payload)
    case_bytes = changed_case.model_dump_json().encode()
    (copied / kind / "case.json").write_bytes(case_bytes)

    protocol_payload = loaded.protocol.model_dump(
        mode="python", exclude={"protocol_id"}
    )
    bindings = list(protocol_payload["cases"])
    bindings[position] = PersonalCanonicalCaseBinding(
        case_ref=ArtifactRef(
            artifact_id=changed_case.case_id,
            artifact_version=1,
            content_hash=hashlib.sha256(case_bytes).hexdigest(),
        ),
        kind=kind,  # type: ignore[arg-type]
    )
    protocol_payload["cases"] = tuple(bindings)
    protocol_payload["protocol_id"] = materialize_id(
        "personal-protocol-", protocol_payload
    )
    changed_protocol = PersonalValidationProtocol.model_validate(protocol_payload)
    (copied / "protocol.json").write_bytes(changed_protocol.model_dump_json().encode())

    with pytest.raises(PersonalValidationIntegrityInvalid, match="protocol"):
        load_protocol_v1(copied)


def _real_roots(tmp_path: Path, kind: str, nonce: str = "canonical-session"):
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
        assert store.publish(snapshot.snapshot_id, snapshot) in {
            case.input_snapshot_ref for case in loaded.cases
        }
    for case, binding in zip(loaded.cases, loaded.protocol.cases, strict=True):
        assert store.publish(case.case_id, case) == binding.case_ref
    assert (
        store.publish(loaded.protocol.protocol_id, loaded.protocol)
        == loaded.protocol_ref
    )
    payload: dict[str, object] = {
        "schema_version": "personal.system-snapshot.v1",
        "git_commit": "task5-synthetic-fixture",
        "execution_tree_sha256": SHA,
        "uv_lock_sha256": SHA,
        "capability_manifest_sha256": SHA,
        "method_profile_sha256": SHA,
        "protocol_ref": loaded.protocol_ref,
        "runtime_versions": (("python", "3.13"),),
        "clean_worktree": True,
    }
    payload["snapshot_id"] = materialize_id("personal-system-snapshot-", payload)
    system = SystemSnapshot.model_validate(payload)
    system_ref = store.publish(system.snapshot_id, system)
    position = tuple(case.kind for case in loaded.cases).index(kind)
    case_ref = loaded.protocol.cases[position].case_ref
    roots = DisposableAttemptRoots.for_case(
        case_ref=case_ref,
        repository_root=repository,
        protocol_ref=loaded.protocol_ref,
        store=store,
        exclusions=exclusions,
        session_nonce=nonce,
        system_snapshot_ref=system_ref,
    )
    assert all(
        path.is_relative_to(roots.case_root) for path in roots.logical_roots.values()
    )
    assert tuple(sorted(roots.logical_roots)) == tuple(
        sorted(loaded.cases[0].required_logical_roots)
    )
    return loaded, store, roots, case_ref


@pytest.mark.parametrize("kind", CASE_ORDER)
def test_real_canonical_case_reopens_complete_target(tmp_path: Path, kind: str) -> None:
    loaded, store, roots, case_ref = _real_roots(tmp_path, kind)
    try:
        prepared = run_case(case_ref, roots)
        attempt = store.load(prepared.attempt_ref, PersonalValidationAttempt)
        if kind == "correct-stop":
            assert attempt.target.target_type == "correct-stop"
            assert attempt.target.inspection.stop_code == "RESEARCH_RUN_BLOCKED"
            checkpoint = next(
                item
                for item in attempt.target.inspection.checkpoints
                if item.node_id == "review-design"
            )
            checkpoint_bytes = (
                roots.research_design / "node-checkpoints/review-design.json"
            ).read_bytes()
            digest = hashlib.sha256(checkpoint_bytes).hexdigest()
            behavior = loaded.expected_behaviors[1]
            assert behavior.expected_checkpoint_sha256 == digest
            assert checkpoint.checkpoint_sha256 == digest
            assert behavior.expected_checkpoint_ref == ArtifactRef(
                artifact_id="review-design-checkpoint",
                artifact_version=1,
                content_hash=digest,
            )
            assert not tuple(roots.paper_root.iterdir())
            assert not tuple(roots.factory_root.iterdir())
            return
        assert attempt.target.target_type == "completed-factory-run"
        run = attempt.target.run
        assert run.binding_report.verdict == "coherent"
        assert run.release.audit_report.verdict == "clean"
        assert run.output_refs
        if kind == "data-method-incompatibility":
            orchestrator = roots.research._state["orchestrator"]
            methods = orchestrator.lifecycle.read_payload(
                Path("artifacts/method-candidates.json"), MethodCandidatesPayload
            )
            rejected = next(
                item for item in methods.candidates if item.role.value == "rejected"
            )
            assert rejected.method_profile_ref == "rdd@0.2.0"
            assert rejected.rejection_evidence.requirement_refs == (
                "known_cutoff",
                "running_variable",
            )
            behavior = loaded.expected_behaviors[2]
            estimand_ref = orchestrator.lifecycle.artifact_ref(
                Path("artifacts/estimand-spec.yaml")
            )
            assert behavior.estimand_anchor_ref == estimand_ref
            assert behavior.estimand_sha256 == estimand_ref.content_hash
        if kind == "evidence-citation-challenge":
            revision = run.release.revision
            assert revision is not None
            draft_ref, audit_ref = roots.paper._state["blocked_pair"]
            audit = roots.paper.audit_service.store.load(audit_ref)
            assert audit.draft_ref == draft_ref
            assert tuple(
                (
                    item.finding_id,
                    item.finding_kind,
                    item.code,
                    item.target,
                    item.claim_ids,
                )
                for item in audit.findings
            ) == tuple(
                (
                    item.finding_id,
                    item.finding_kind,
                    item.code,
                    item.predecessor_target,
                    item.claim_ids,
                )
                for item in revision.closure_witnesses
            )
            assert revision.successor_audit_ref == run.release.audit_ref
    finally:
        roots.close()
        store.close()
