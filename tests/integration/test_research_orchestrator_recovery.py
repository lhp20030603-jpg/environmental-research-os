"""Crash recovery and immutable lifecycle tests for research orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from orchestrator_fixtures import (
    approve,
    broad_brief,
    candidate_payload,
    config,
    estimand,
    literature,
    ready_for_final_gate,
    structured_brief,
    submit,
)

import envresearch.storage.research_artifacts as research_artifact_storage
from envresearch.kernel.artifact_graph import ArtifactNode
from envresearch.models.artifact import ArtifactRef, ResearchArtifact, seal_artifact
from envresearch.models.enums import ArtifactLifecycle
from envresearch.models.intake import ResearchBriefPayload, ResearchIntakeMode
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.orchestrator import ResearchOrchestrator, ResearchRunPhase


@pytest.mark.parametrize(
    ("mode", "original", "changed"),
    (
        (
            ResearchIntakeMode.BROAD_TOPIC,
            broad_brief(),
            ResearchBriefPayload(
                intake_mode=ResearchIntakeMode.BROAD_TOPIC,
                broad_topic="Changed broad topic",
            ),
        ),
        (
            ResearchIntakeMode.STRUCTURED_BRIEF,
            structured_brief(),
            ResearchBriefPayload(
                intake_mode=ResearchIntakeMode.STRUCTURED_BRIEF,
                structured_brief="A different structured research question.",
            ),
        ),
    ),
)
def test_recovery_rejects_changed_bound_intake(
    tmp_path: Path,
    mode: ResearchIntakeMode,
    original: ResearchBriefPayload,
    changed: ResearchBriefPayload,
) -> None:
    first = ResearchOrchestrator()
    first.initialize(config(tmp_path, mode), original)
    first.close()

    with pytest.raises((FileExistsError, ValueError), match="conflicting|bound"):
        ResearchOrchestrator().initialize(config(tmp_path, mode), changed)


def test_final_approval_preserves_versions_recovers_and_stops_before_execute(
    tmp_path: Path,
) -> None:
    orchestrator = ready_for_final_gate(tmp_path)

    approve(orchestrator, "final-gate", accepted_major_ids=[])
    complete = orchestrator.advance()

    assert complete.phase is ResearchRunPhase.COMPLETE
    assert complete.approved_artifact == Path("artifacts/analysis-plan.yaml")
    assert not (tmp_path / "artifacts/results-registry.json").exists()
    assert not (tmp_path / "artifacts/execution-plan.yaml").exists()
    history = sorted(
        (tmp_path / "artifacts/.versions/analysis-plan.yaml").glob("*.json")
    )
    assert [
        json.loads(path.read_text())["envelope"]["validation_status"]
        for path in history
    ] == ["produced", "validated", "approved"]
    hashes = [
        json.loads(path.read_text())["envelope"]["content_hash"] for path in history
    ]
    assert len(set(hashes)) == 3
    for artifact_path in (
        Path("artifacts/evidence-matrix.csv"),
        Path("artifacts/identification-memo.md"),
    ):
        for version in (1, 2):
            verified = orchestrator.lifecycle.read_history(artifact_path, version)
            assert verified.envelope.artifact_version == version
        validated = orchestrator.lifecycle.read_history(artifact_path, 2)
        assert validated.payload["authoritative_content_hash"]  # type: ignore[index]

    orchestrator.close()
    recovered = ResearchOrchestrator()
    recovered.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    resumed = recovered.advance()
    assert resumed.phase is ResearchRunPhase.COMPLETE
    assert resumed.pending_work_order_nodes == ()


@pytest.mark.parametrize(
    "boundary",
    ("invalidation", "approved-history", "approved-current", "compose", "terminal"),
)
def test_final_gate_recovers_after_every_approval_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    orchestrator = ready_for_final_gate(tmp_path)
    approve(orchestrator, "final-gate", accepted_major_ids=[])

    if boundary == "invalidation":
        real = orchestrator.checkpoints.invalidate

        def crash(*args: object, **kwargs: object) -> object:
            real(*args, **kwargs)  # type: ignore[arg-type]
            raise RuntimeError("crash after invalidation")

        monkeypatch.setattr(orchestrator.checkpoints, "invalidate", crash)
    elif boundary == "approved-history":
        real_history = orchestrator.lifecycle._history

        def crash_history(*args: object, **kwargs: object) -> object:
            result = real_history(*args, **kwargs)  # type: ignore[arg-type]
            if args[4] == 3:
                raise RuntimeError("crash after approved history")
            return result

        monkeypatch.setattr(orchestrator.lifecycle, "_history", crash_history)
    elif boundary == "approved-current":
        real_upgrade = orchestrator.lifecycle._publish_upgrade

        def crash_upgrade(*args: object, **kwargs: object) -> None:
            real_upgrade(*args, **kwargs)  # type: ignore[arg-type]
            raise RuntimeError("crash after approved current")

        monkeypatch.setattr(orchestrator.lifecycle, "_publish_upgrade", crash_upgrade)
    else:
        real_publish = orchestrator.checkpoints.publish

        def crash_checkpoint(
            node: ArtifactNode, *args: object, **kwargs: object
        ) -> object:
            result = real_publish(node, *args, **kwargs)  # type: ignore[arg-type]
            target = "compose-plan" if boundary == "compose" else "final-approval"
            if node.node_id == target:
                raise RuntimeError(f"crash after {boundary} checkpoint")
            return result

        monkeypatch.setattr(orchestrator.checkpoints, "publish", crash_checkpoint)

    with pytest.raises(RuntimeError, match="crash after"):
        orchestrator.advance()
    orchestrator.close()

    recovered = ResearchOrchestrator()
    recovered.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    assert recovered.advance().phase is ResearchRunPhase.COMPLETE


@pytest.mark.parametrize("crash_after_write", (1, 2, 3))
def test_structured_promotion_resumes_every_write_boundary_without_rehashing_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_after_write: int
) -> None:
    lifecycle = ResearchArtifactLifecycle(tmp_path, "run-lifecycle")
    real_write = lifecycle.store.write_structured
    writes = 0

    def crash_after(path: Path, artifact: object) -> object:
        nonlocal writes
        result = real_write(path, artifact)  # type: ignore[arg-type]
        writes += 1
        if writes == crash_after_write:
            raise RuntimeError("simulated lifecycle crash")
        return result

    monkeypatch.setattr(lifecycle.store, "write_structured", crash_after)
    with pytest.raises(RuntimeError, match="lifecycle crash"):
        lifecycle.persist_structured(
            Path("artifacts/research-brief.yaml"),
            broad_brief(),
            "research-intake",
            (),
        )
    version_root = tmp_path / "artifacts/.versions/research-brief.yaml"
    durable_before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in version_root.glob("*.json")
    }

    recovered = ResearchArtifactLifecycle(tmp_path, "run-lifecycle")
    promoted = recovered.persist_structured(
        Path("artifacts/research-brief.yaml"),
        broad_brief(),
        "research-intake",
        (),
    )

    for relative, data in durable_before.items():
        assert (tmp_path / relative).read_bytes() == data
    assert promoted == recovered.read_artifact(Path("artifacts/research-brief.yaml"))


@pytest.mark.parametrize("identity", ("wrong-run", "wrong-input"))
def test_idempotent_adoption_rejects_foreign_lifecycle_identity(
    tmp_path: Path, identity: str
) -> None:
    path = Path("artifacts/estimand-spec.yaml")
    ref_a = ArtifactRef(artifact_id="source", artifact_version=1, content_hash="a" * 64)
    ref_b = ArtifactRef(artifact_id="source", artifact_version=1, content_hash="b" * 64)
    ResearchArtifactLifecycle(tmp_path, "run-a").persist_structured(
        path, estimand(), "estimand-designer", (ref_a,)
    )
    retry = ResearchArtifactLifecycle(
        tmp_path, "run-b" if identity == "wrong-run" else "run-a"
    )

    with pytest.raises(FileExistsError, match="conflicting immutable"):
        retry.persist_structured(
            path,
            estimand(),
            "estimand-designer",
            (ref_a if identity == "wrong-run" else ref_b,),
        )


def test_lifecycle_identity_binds_complete_artifact_path(tmp_path: Path) -> None:
    lifecycle = ResearchArtifactLifecycle(tmp_path, "run-path-identity")
    first_path = Path("artifacts/a/shared.yaml")
    second_path = Path("artifacts/b/shared.yaml")

    first = lifecycle.persist_structured(
        first_path, broad_brief(), "research-intake", ()
    )
    second = lifecycle.persist_structured(
        second_path, broad_brief(), "research-intake", ()
    )

    assert first.envelope.provenance["artifact_path"] == first_path.as_posix()
    assert second.envelope.provenance["artifact_path"] == second_path.as_posix()
    assert lifecycle.read_history(first_path, 2) == first
    assert lifecycle.read_history(second_path, 2) == second


def test_history_namespace_binds_complete_filename_with_suffix(tmp_path: Path) -> None:
    lifecycle = ResearchArtifactLifecycle(tmp_path, "run-suffix-identity")
    yaml_path = Path("artifacts/shared.yaml")
    json_path = Path("artifacts/shared.json")

    yaml_artifact = lifecycle.persist_structured(
        yaml_path, broad_brief(), "research-intake", ()
    )
    json_artifact = lifecycle.persist_structured(
        json_path, broad_brief(), "research-intake", ()
    )

    assert lifecycle.read_history(yaml_path, 2) == yaml_artifact
    assert lifecycle.read_history(json_path, 2) == json_artifact


def test_final_gate_rejects_same_status_approval_from_foreign_producer(
    tmp_path: Path,
) -> None:
    orchestrator = ready_for_final_gate(tmp_path)
    approve(orchestrator, "final-gate", accepted_major_ids=[])
    path = Path("artifacts/analysis-plan.yaml")
    predecessor = orchestrator.lifecycle.read_history(path, 2)
    orchestrator.lifecycle.promote_status(
        path,
        ArtifactLifecycle.APPROVED,
        "foreign-approver",
        predecessor_ref=orchestrator.lifecycle.history_ref(path, 2),
        predecessor_component=predecessor.envelope.producer,
        expected_inputs=predecessor.envelope.input_artifacts,
        gate_context_hash="a" * 64,
    )

    with pytest.raises(FileExistsError, match="immutable|producer|transition"):
        orchestrator.advance()


def test_final_gate_rejects_current_plan_different_from_reviewed_version(
    tmp_path: Path,
) -> None:
    orchestrator = ready_for_final_gate(tmp_path)
    approve(orchestrator, "final-gate", accepted_major_ids=[])
    path = Path("artifacts/analysis-plan.yaml")
    reviewed = orchestrator.lifecycle.read_history(path, 2)
    assert isinstance(reviewed.payload, dict)
    changed_payload = {**reviewed.payload, "assumptions": ["Changed after review"]}
    changed = seal_artifact(
        ResearchArtifact(
            envelope=reviewed.envelope.model_copy(update={"content_hash": None}),
            payload=changed_payload,
        )
    )
    orchestrator.lifecycle.store.write_structured(path, changed)

    with pytest.raises(FileExistsError, match="reviewed|predecessor|immutable"):
        orchestrator.advance()


@pytest.mark.parametrize(
    "crash_destination", ("evidence-matrix.csv", "evidence-matrix.meta.json")
)
def test_csv_publication_recovers_death_after_each_pair_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_destination: str
) -> None:
    lifecycle = ResearchArtifactLifecycle(tmp_path, "run-csv-recovery")
    real_replace = research_artifact_storage._replace_and_sync

    def crash_after_csv(source: Path, destination: Path) -> None:
        real_replace(source, destination)
        if destination.name == crash_destination:
            raise RuntimeError("process died after pair rename")

    monkeypatch.setattr(research_artifact_storage, "_replace_and_sync", crash_after_csv)
    with pytest.raises(RuntimeError, match="after pair rename"):
        lifecycle._persist_csv(literature(), "literature-reviewer", ())
    csv_path = tmp_path / "artifacts/evidence-matrix.csv"
    metadata_path = tmp_path / "artifacts/evidence-matrix.meta.json"
    assert csv_path.exists()
    assert metadata_path.exists() is crash_destination.endswith("meta.json")

    monkeypatch.setattr(research_artifact_storage, "_replace_and_sync", real_replace)
    recovered = ResearchArtifactLifecycle(tmp_path, "run-csv-recovery")
    recovered._persist_csv(literature(), "literature-reviewer", ())

    assert csv_path.exists()
    assert metadata_path.exists()
    assert recovered.artifact_ref(Path("artifacts/evidence-matrix.csv"))


def test_gate_context_recovers_crash_before_gate_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    real_request = orchestrator.gates.request

    def crash_before_request(*args: object, **kwargs: object) -> None:
        raise RuntimeError("process died before gate request")

    monkeypatch.setattr(orchestrator.gates, "request", crash_before_request)
    with pytest.raises(RuntimeError, match="before gate request"):
        orchestrator.advance()
    assert orchestrator.bound_gates.active_context("gate-1") is not None
    assert not (tmp_path / "gates/gate-1.json").exists()

    monkeypatch.setattr(orchestrator.gates, "request", real_request)
    assert orchestrator.advance().pending_gate_ids == ("gate-1",)


@pytest.mark.parametrize("event_published", (False, True))
def test_gate_context_recovers_request_file_and_event_publication_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event_published: bool
) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    real_append = orchestrator.gates._append_if_missing

    def crash_during_request(event: object) -> None:
        if event_published:
            real_append(event)  # type: ignore[arg-type]
        raise RuntimeError("process died during gate request")

    monkeypatch.setattr(orchestrator.gates, "_append_if_missing", crash_during_request)
    with pytest.raises(RuntimeError, match="during gate request"):
        orchestrator.advance()
    assert (tmp_path / "gates/gate-1.json").exists()

    monkeypatch.setattr(orchestrator.gates, "_append_if_missing", real_append)
    assert orchestrator.advance().pending_gate_ids == ("gate-1",)
