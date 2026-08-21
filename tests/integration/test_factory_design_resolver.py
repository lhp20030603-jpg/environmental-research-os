"""Integration behavior for exact, read-only V0.2 design handoff recovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from factory_fixtures import (
    PLAN_PATH,
    completed_orchestrator,
    factory_root,
    final_context_ref,
)

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.factory.design_resolver import V02ApprovedDesignResolver
from envresearch.factory.errors import (
    FactoryIntegrityInvalid,
    FactoryScopeExceeded,
    FactorySupportInvalid,
)
from envresearch.models.artifact import ArtifactRef
from envresearch.research.final_integrity import reopen_complete_final_exact


def test_resolver_publishes_and_reopens_the_exact_final_approved_design(
    tmp_path: Path,
) -> None:
    """Catch an adapter that cannot reconstruct the actually approved V0.2 design."""
    orchestrator = completed_orchestrator(tmp_path)
    plan_ref = orchestrator.lifecycle.artifact_ref(PLAN_PATH)
    context_ref = final_context_ref(orchestrator)
    resolver = V02ApprovedDesignResolver(orchestrator, factory_root(tmp_path))

    handoff_ref = resolver.build(plan_ref, context_ref)

    assert resolver.resolve(handoff_ref).plan_ref == plan_ref
    resolver.require_current(handoff_ref)


@pytest.mark.parametrize("factory", ("same", "nested", "symlink"))
def test_resolver_requires_a_physically_separate_factory_root(
    tmp_path: Path, factory: str
) -> None:
    """Catch a factory registry that aliases or nests below the research root."""
    orchestrator = completed_orchestrator(tmp_path)
    root = tmp_path if factory == "same" else tmp_path / "factory"
    if factory == "symlink":
        external = tmp_path.parent / f"{tmp_path.name}-external"
        external.mkdir()
        root.symlink_to(external, target_is_directory=True)

    with pytest.raises(FactoryScopeExceeded, match="separate|root|scope"):
        V02ApprovedDesignResolver(orchestrator, root)


def test_constructing_and_using_status_does_not_mutate_either_root(
    tmp_path: Path,
) -> None:
    """Catch resolve/status bootstrap writes before or after a read-only reopen."""
    orchestrator = completed_orchestrator(tmp_path)
    root = factory_root(tmp_path)
    writer = V02ApprovedDesignResolver(orchestrator, root)
    handoff_ref = writer.build(
        orchestrator.lifecycle.artifact_ref(PLAN_PATH), final_context_ref(orchestrator)
    )
    before = _root_snapshot(tmp_path, root)

    reader = V02ApprovedDesignResolver(orchestrator, root)
    reader.resolve(handoff_ref)
    reader.require_current(handoff_ref)

    assert _root_snapshot(tmp_path, root) == before


def test_build_restores_prior_pointers_after_its_second_reopen_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch a failed post-install reopen that leaves a prepared/current tear."""
    orchestrator = completed_orchestrator(tmp_path)
    root = factory_root(tmp_path)
    resolver = V02ApprovedDesignResolver(orchestrator, root)
    plan_ref = orchestrator.lifecycle.artifact_ref(PLAN_PATH)
    context_ref = final_context_ref(orchestrator)
    design_id = resolver._subject(resolver._design_id(plan_ref, context_ref))
    registry = ExitRegistry(root, create=True)
    calls = 0
    original_reopen = resolver._reopen

    def fail_second_reopen(plan: ArtifactRef, context: ArtifactRef) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("injected second reopen failure")
        return original_reopen(plan, context)

    monkeypatch.setattr(resolver, "_reopen", fail_second_reopen)

    with pytest.raises(FactoryIntegrityInvalid, match="cannot be authenticated"):
        resolver.build(plan_ref, context_ref)

    assert registry.current(design_id) is None
    assert registry.current(f"{design_id}-prepared") is None


def test_build_rollback_does_not_overwrite_a_newer_prepared_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch rollback that erases a concurrent writer's prepared pointer."""
    orchestrator = completed_orchestrator(tmp_path)
    root = factory_root(tmp_path)
    resolver = V02ApprovedDesignResolver(orchestrator, root)
    plan_ref = orchestrator.lifecycle.artifact_ref(PLAN_PATH)
    context_ref = final_context_ref(orchestrator)
    design_id = resolver._design_id(plan_ref, context_ref)
    subject = resolver._prepared_subject(design_id)
    registry = ExitRegistry(root, create=True)
    foreign = ArtifactRef(
        artifact_id="approved-design-handoff",
        artifact_version=1,
        content_hash="0" * 64,
    )
    calls = 0
    original_reopen = resolver._reopen

    def advance_then_fail(plan: ArtifactRef, context: ArtifactRef) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            registry.set_current(subject, foreign)
            raise ValueError("injected second reopen failure")
        return original_reopen(plan, context)

    monkeypatch.setattr(resolver, "_reopen", advance_then_fail)

    with pytest.raises(FactoryIntegrityInvalid, match="cannot be authenticated"):
        resolver.build(plan_ref, context_ref)

    assert ArtifactRef.model_validate_json(
        registry.files.read(Path("exit/current") / f"{subject}.json"), strict=True
    ) == foreign


def test_exact_final_reopen_rejects_a_substituted_context_reference(tmp_path: Path) -> None:
    """Catch reopening Final Gate through the current context instead of the supplied ref."""
    orchestrator = completed_orchestrator(tmp_path)
    plan_ref = orchestrator.lifecycle.artifact_ref(PLAN_PATH)
    context_ref = final_context_ref(orchestrator).model_copy(
        update={"artifact_version": 2}
    )

    with pytest.raises(ValueError, match="context|final"):
        reopen_complete_final_exact(
            lifecycle=orchestrator.lifecycle,
            gates=orchestrator.bound_gates,
            checkpoints=orchestrator.checkpoints,
            nodes=orchestrator._nodes,
            semantics=orchestrator.semantics,
            audit=orchestrator.audit,
            plan_ref=plan_ref,
            context_ref=context_ref,
        )


def test_resolve_and_current_check_do_not_mutate_a_completed_research_root(
    tmp_path: Path,
) -> None:
    """Catch read paths that write timestamps, checkpoints, or lifecycle promotions."""
    orchestrator = completed_orchestrator(tmp_path)
    resolver = V02ApprovedDesignResolver(orchestrator, factory_root(tmp_path))
    handoff_ref = resolver.build(
        orchestrator.lifecycle.artifact_ref(PLAN_PATH), final_context_ref(orchestrator)
    )
    before = {
        path.relative_to(tmp_path): (path.read_bytes(), path.stat().st_mode)
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    resolver.resolve(handoff_ref)
    resolver.require_current(handoff_ref)

    after = {
        path.relative_to(tmp_path): (path.read_bytes(), path.stat().st_mode)
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_resolve_rejects_a_prepared_pointer_that_no_longer_matches_current(
    tmp_path: Path,
) -> None:
    """Catch publication recovery that trusts current while prepared was replaced."""
    orchestrator = completed_orchestrator(tmp_path)
    resolver = V02ApprovedDesignResolver(orchestrator, factory_root(tmp_path))
    handoff_ref = resolver.build(
        orchestrator.lifecycle.artifact_ref(PLAN_PATH), final_context_ref(orchestrator)
    )
    handoff = resolver.resolve(handoff_ref)
    ExitRegistry(factory_root(tmp_path), create=False).set_current(
        resolver._prepared_subject(handoff.design_id),
        ArtifactRef(
            artifact_id=handoff_ref.artifact_id,
            artifact_version=handoff_ref.artifact_version,
            content_hash="0" * 64,
        ),
    )

    with pytest.raises(FactoryIntegrityInvalid, match="stale|corrupt"):
        resolver.resolve(handoff_ref)


def test_resolve_rejects_a_hardlinked_plan_substitution(tmp_path: Path) -> None:
    """Catch an alias that would let a source artifact evade exact file evidence."""
    orchestrator = completed_orchestrator(tmp_path)
    resolver = V02ApprovedDesignResolver(orchestrator, factory_root(tmp_path))
    handoff_ref = resolver.build(
        orchestrator.lifecycle.artifact_ref(PLAN_PATH), final_context_ref(orchestrator)
    )
    plan = tmp_path / PLAN_PATH
    alias = tmp_path / "fixture-plan-copy.yaml"
    alias.hardlink_to(plan)

    with pytest.raises((FactoryIntegrityInvalid, FactorySupportInvalid)):
        resolver.resolve(handoff_ref)


def test_final_reopen_rejects_a_gate_decision_that_is_not_approved(
    tmp_path: Path,
) -> None:
    """Catch a rejected Final Gate being mistaken for an approval binding."""
    orchestrator = completed_orchestrator(tmp_path)
    gate_path = tmp_path / "gates/final-gate.json"
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    gate["status"] = "rejected"
    gate["decision"]["status"] = "rejected"
    gate_path.write_text(json.dumps(gate), encoding="utf-8")

    with pytest.raises(ValueError, match="stale|corrupt"):
        reopen_complete_final_exact(
            lifecycle=orchestrator.lifecycle,
            gates=orchestrator.bound_gates,
            checkpoints=orchestrator.checkpoints,
            nodes=orchestrator._nodes,
            semantics=orchestrator.semantics,
            audit=orchestrator.audit,
            plan_ref=orchestrator.lifecycle.artifact_ref(PLAN_PATH),
            context_ref=final_context_ref(orchestrator),
        )


def test_final_reopen_rejects_an_approval_file_without_matching_events(
    tmp_path: Path,
) -> None:
    """Catch a rewritten gate file that has no independently recorded approval."""
    orchestrator = completed_orchestrator(tmp_path)
    gate_path = tmp_path / "gates/final-gate.json"
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    gate["decision"]["decided_by"] = gate["requested_by"]
    gate_path.write_text(json.dumps(gate), encoding="utf-8")

    with pytest.raises(ValueError, match="stale|corrupt"):
        reopen_complete_final_exact(
            lifecycle=orchestrator.lifecycle,
            gates=orchestrator.bound_gates,
            checkpoints=orchestrator.checkpoints,
            nodes=orchestrator._nodes,
            semantics=orchestrator.semantics,
            audit=orchestrator.audit,
            plan_ref=orchestrator.lifecycle.artifact_ref(PLAN_PATH),
            context_ref=final_context_ref(orchestrator),
        )


@pytest.mark.parametrize("target", ("manifest", "decision-log", "checkpoint", "plan"))
def test_resolve_rejects_mutated_final_design_evidence(
    tmp_path: Path, target: str
) -> None:
    """Catch source-byte changes after immutable approved-design publication."""
    orchestrator = completed_orchestrator(tmp_path)
    resolver = V02ApprovedDesignResolver(orchestrator, factory_root(tmp_path))
    handoff_ref = resolver.build(
        orchestrator.lifecycle.artifact_ref(PLAN_PATH), final_context_ref(orchestrator)
    )
    paths = {
        "manifest": tmp_path / "research-run-manifest.json",
        "decision-log": tmp_path / "decision-log.jsonl",
        "checkpoint": tmp_path / "node-checkpoints/final-approval.json",
        "plan": tmp_path / PLAN_PATH,
    }
    path = paths[target]
    path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises((FactoryIntegrityInvalid, FactorySupportInvalid)):
        resolver.resolve(handoff_ref)


def test_reopen_rejects_an_unapproved_plan_or_context_hash(tmp_path: Path) -> None:
    """Catch a validated plan or changed context digest presented as Final authority."""
    orchestrator = completed_orchestrator(tmp_path)
    with pytest.raises(ValueError, match="stale|corrupt"):
        reopen_complete_final_exact(
            lifecycle=orchestrator.lifecycle,
            gates=orchestrator.bound_gates,
            checkpoints=orchestrator.checkpoints,
            nodes=orchestrator._nodes,
            semantics=orchestrator.semantics,
            audit=orchestrator.audit,
            plan_ref=orchestrator.lifecycle.history_ref(PLAN_PATH, 2),
            context_ref=final_context_ref(orchestrator),
        )
    with pytest.raises(ValueError, match="stale|corrupt"):
        reopen_complete_final_exact(
            lifecycle=orchestrator.lifecycle,
            gates=orchestrator.bound_gates,
            checkpoints=orchestrator.checkpoints,
            nodes=orchestrator._nodes,
            semantics=orchestrator.semantics,
            audit=orchestrator.audit,
            plan_ref=orchestrator.lifecycle.artifact_ref(PLAN_PATH),
            context_ref=final_context_ref(orchestrator).model_copy(
                update={"content_hash": "0" * 64}
            ),
        )


def test_resolve_rejects_a_symlinked_plan_substitution(tmp_path: Path) -> None:
    """Catch a final artifact path redirected through an in-root symbolic link."""
    orchestrator = completed_orchestrator(tmp_path)
    resolver = V02ApprovedDesignResolver(orchestrator, factory_root(tmp_path))
    handoff_ref = resolver.build(
        orchestrator.lifecycle.artifact_ref(PLAN_PATH), final_context_ref(orchestrator)
    )
    plan = tmp_path / PLAN_PATH
    history = tmp_path / "artifacts/.versions/analysis-plan.yaml/0003.json"
    plan.unlink()
    plan.symlink_to(history)

    with pytest.raises((FactoryIntegrityInvalid, FactorySupportInvalid)):
        resolver.resolve(handoff_ref)


def test_reopen_rejects_a_superseded_final_context(tmp_path: Path) -> None:
    """Catch a historical Final Gate context presented after a durable supersession."""
    orchestrator = completed_orchestrator(tmp_path)
    context_ref = final_context_ref(orchestrator)
    marker = tmp_path / "gate-contexts/final-gate/superseded/final-gate.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="stale|corrupt"):
        reopen_complete_final_exact(
            lifecycle=orchestrator.lifecycle,
            gates=orchestrator.bound_gates,
            checkpoints=orchestrator.checkpoints,
            nodes=orchestrator._nodes,
            semantics=orchestrator.semantics,
            audit=orchestrator.audit,
            plan_ref=orchestrator.lifecycle.artifact_ref(PLAN_PATH),
            context_ref=context_ref,
        )


def test_resolve_rejects_a_current_pointer_advance_during_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch a current-pointer advance between source reopening and return."""
    orchestrator = completed_orchestrator(tmp_path)
    resolver = V02ApprovedDesignResolver(orchestrator, factory_root(tmp_path))
    handoff_ref = resolver.build(
        orchestrator.lifecycle.artifact_ref(PLAN_PATH), final_context_ref(orchestrator)
    )
    handoff = resolver.resolve(handoff_ref)
    original_reopen = resolver._reopen

    def advance_after_reopen(plan_ref: ArtifactRef, context_ref: ArtifactRef) -> object:
        state = original_reopen(plan_ref, context_ref)
        ExitRegistry(factory_root(tmp_path), create=False).set_current(
            resolver._subject(handoff.design_id),
            handoff_ref.model_copy(update={"content_hash": "0" * 64}),
        )
        return state

    monkeypatch.setattr(resolver, "_reopen", advance_after_reopen)

    with pytest.raises(FactoryIntegrityInvalid, match="stale|corrupt"):
        resolver.resolve(handoff_ref)


def _root_snapshot(*roots: Path) -> dict[Path, tuple[bytes, int]]:
    return {
        path: (path.read_bytes(), path.stat().st_mode)
        for root in roots
        for path in root.rglob("*")
        if path.is_file()
    }
