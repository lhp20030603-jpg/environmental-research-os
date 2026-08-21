"""Descriptor and deterministic-authority regressions for canonical cases."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from test_personal_validation_cases import _real_roots

from envresearch.models.artifact import ArtifactRef
from envresearch.models.design import MethodCandidatesPayload
from envresearch.personal_validation.canonical_cases import (
    DisposableAttemptRoots,
    run_case,
)
from envresearch.personal_validation.case_stops import snapshot_exclusion_trees
from envresearch.personal_validation.contracts import (
    AttemptRootInventory,
    PersonalValidationAttempt,
)
from envresearch.personal_validation.errors import PersonalValidationIntegrityInvalid


def _semantic_digest(
    roots: DisposableAttemptRoots, attempt: PersonalValidationAttempt
) -> str:
    research = roots.research
    orchestrator = research._state["orchestrator"]
    methods = orchestrator.lifecycle.read_payload(
        Path("artifacts/method-candidates.json"), MethodCandidatesPayload
    )
    rejected = next(
        item for item in methods.candidates if item.role.value == "rejected"
    )
    payload = {
        "estimand_ref": research.estimand_ref.model_dump(mode="json"),
        "requirements": rejected.rejection_evidence.requirement_refs,
        "factory": attempt.target.run.binding_report.verdict,
        "release": attempt.target.run.release.audit_report.verdict,
    }
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _checkpoint_authority(
    roots: DisposableAttemptRoots,
) -> tuple[ArtifactRef, bytes]:
    data = (roots.research_design / "node-checkpoints/review-design.json").read_bytes()
    return (
        ArtifactRef(
            artifact_id="review-design-checkpoint",
            artifact_version=1,
            content_hash=hashlib.sha256(data).hexdigest(),
        ),
        data,
    )


def test_exact_retry_and_fresh_session_use_new_pinned_namespace(tmp_path: Path) -> None:
    loaded, store, roots, case_ref = _real_roots(
        tmp_path / "first", "data-method-incompatibility"
    )
    before = snapshot_exclusion_trees(roots.exclusions)
    try:
        first = run_case(case_ref, roots)
        events = tuple(store.read_events())
        assert run_case(case_ref, roots) == first
        assert tuple(store.read_events()) == events
        first_attempt = store.load(first.attempt_ref, PersonalValidationAttempt)
        first_inventory = store.load(
            first_attempt.attempt_inventory_ref, AttemptRootInventory
        )
        _, other_store, other_roots, other_case_ref = _real_roots(
            tmp_path / "fresh", "data-method-incompatibility", "fresh-session"
        )
        try:
            fresh = run_case(other_case_ref, other_roots)
            fresh_attempt = other_store.load(
                fresh.attempt_ref, PersonalValidationAttempt
            )
            fresh_inventory = other_store.load(
                fresh_attempt.attempt_inventory_ref, AttemptRootInventory
            )
            assert (fresh.session_ref, fresh.attempt_ref) != (
                first.session_ref,
                first.attempt_ref,
            )
            assert fresh_attempt.target != first_attempt.target
            assert fresh_inventory != first_inventory
            assert other_roots.case_root != roots.case_root
            assert other_roots.research is not roots.research
            assert other_roots.research.estimand_ref == roots.research.estimand_ref
            assert (
                loaded.expected_behaviors[2].estimand_anchor_ref
                == roots.research.estimand_ref
            )
            assert _semantic_digest(other_roots, fresh_attempt) == _semantic_digest(
                roots, first_attempt
            )
        finally:
            other_roots.close()
            other_store.close()
        assert snapshot_exclusion_trees(roots.exclusions) == before
    finally:
        roots.close()
        store.close()


def test_fresh_correct_stop_reopens_equal_real_checkpoint_bytes(tmp_path: Path) -> None:
    attempts = []
    authorities = []
    resources = []
    try:
        for name in ("first", "fresh"):
            loaded, store, roots, case_ref = _real_roots(
                tmp_path / name, "correct-stop", f"{name}-session"
            )
            resources.append((roots, store))
            prepared = run_case(case_ref, roots)
            attempts.append(store.load(prepared.attempt_ref, PersonalValidationAttempt))
            authorities.append(_checkpoint_authority(roots))
            assert (
                loaded.expected_behaviors[1].expected_checkpoint_ref
                == authorities[-1][0]
            )
        assert authorities[0] == authorities[1]
        assert attempts[0].attempt_inventory_ref != attempts[1].attempt_inventory_ref
        assert attempts[0].target != attempts[1].target
        semantic_digests = tuple(
            hashlib.sha256(
                f"{attempt.target.inspection.stop_code}:{authority[0].content_hash}".encode()
            ).hexdigest()
            for attempt, authority in zip(attempts, authorities, strict=True)
        )
        assert semantic_digests[0] == semantic_digests[1]
    finally:
        for roots, store in reversed(resources):
            roots.close()
            store.close()


def test_parent_swap_to_excluded_root_fails_before_mutation(tmp_path: Path) -> None:
    _, store, roots, case_ref = _real_roots(tmp_path, "correct-stop")
    excluded = roots.exclusions.obsidian_roots[0]
    before = snapshot_exclusion_trees(roots.exclusions)
    moved = roots.case_root.with_name(roots.case_root.name + "-moved")
    roots.case_root.rename(moved)
    roots.case_root.symlink_to(excluded, target_is_directory=True)
    try:
        with pytest.raises((PersonalValidationIntegrityInvalid, ValueError)):
            run_case(case_ref, roots)
        assert snapshot_exclusion_trees(roots.exclusions) == before
        assert not tuple(excluded.iterdir())
    finally:
        roots.close()
        store.close()
