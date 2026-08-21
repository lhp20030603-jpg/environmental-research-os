"""Terminal integrity regressions for the research orchestrator."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from orchestrator_fixtures import (
    approve,
    broad_brief,
    config,
    ready_for_final_gate,
)

from envresearch.kernel.events import EventLog
from envresearch.kernel.gates import GateStore
from envresearch.models.artifact import ArtifactRef, ResearchArtifact, seal_artifact
from envresearch.models.intake import ResearchIntakeMode
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.gate_context import BoundGateManager
from envresearch.research.orchestrator import ResearchOrchestrator, ResearchRunPhase
from envresearch.storage.artifacts import ArtifactStore

PLAN_PATH = Path("artifacts/analysis-plan.yaml")
PLAN_V1_PATH = Path("artifacts/.versions/analysis-plan.yaml/0001.json")
PLAN_V2_PATH = Path("artifacts/.versions/analysis-plan.yaml/0002.json")


def test_fixed_clock_controls_sealed_artifact_and_bound_gate_identity(
    tmp_path: Path,
) -> None:
    fixed = datetime(2026, 8, 21, 1, 2, 3, tzinfo=UTC)
    later = fixed + timedelta(seconds=1)
    artifact_refs: list[ArtifactRef] = []
    context_hashes: list[str | None] = []
    for name, instant in (("first", fixed), ("second", fixed), ("later", later)):
        root = tmp_path / name
        lifecycle = ResearchArtifactLifecycle(
            root, "run-clock", clock=lambda instant=instant: instant
        )
        artifact = lifecycle.persist_structured(
            PLAN_PATH, broad_brief(), "research-intake", ()
        )
        artifact_refs.append(lifecycle.artifact_ref(PLAN_PATH))
        gates = GateStore(lifecycle.raw, EventLog(root / "events.jsonl"))
        context = BoundGateManager(
            root, gates, "researcher", clock=lambda instant=instant: instant
        ).ensure("gate-1", "Review", (lifecycle.artifact_ref(PLAN_PATH),))
        context_hashes.append(context.context_hash)
        assert artifact.envelope.created_at == instant

    assert artifact_refs[0] == artifact_refs[1]
    assert context_hashes[0] == context_hashes[1]
    assert artifact_refs[2] != artifact_refs[0]
    assert context_hashes[2] != context_hashes[0]


@pytest.mark.parametrize(
    "invalid_time",
    (
        datetime(2026, 8, 21, 1, 2, 3),  # noqa: DTZ001 - rejection fixture
        datetime(2026, 8, 21, 1, 2, 3, tzinfo=timezone(timedelta(hours=8))),
    ),
)
def test_invalid_gate_clock_leaves_no_partial_authority_and_retry_is_clean(
    tmp_path: Path, invalid_time: datetime
) -> None:
    tmp_path.mkdir(exist_ok=True)
    reference = ArtifactRef(
        artifact_id="review-input", artifact_version=1, content_hash="a" * 64
    )
    gates = GateStore(ArtifactStore(tmp_path), EventLog(tmp_path / "events.jsonl"))
    invalid = BoundGateManager(
        tmp_path, gates, "researcher", clock=lambda: invalid_time
    )

    with pytest.raises(ValueError, match="UTC"):
        invalid.ensure("gate-1", "Review", (reference,))

    assert not (tmp_path / "gate-contexts").exists()
    assert not (tmp_path / "gates").exists()
    assert not (tmp_path / "events.jsonl").exists()
    fixed = datetime(2026, 8, 21, 1, 2, 3, tzinfo=UTC)
    context = BoundGateManager(
        tmp_path, gates, "researcher", clock=lambda: fixed
    ).ensure("gate-1", "Review", (reference,))
    assert context.requested_at == fixed
    assert (tmp_path / "gate-contexts/gate-1/0001.json").is_file()
    assert (tmp_path / "gates/gate-1.json").is_file()
    assert len(EventLog(tmp_path / "events.jsonl").read_all()) == 1


def _completed_orchestrator(tmp_path: Path) -> ResearchOrchestrator:
    orchestrator = ready_for_final_gate(tmp_path)
    approve(orchestrator, "final-gate", accepted_major_ids=[])
    assert orchestrator.advance().phase is ResearchRunPhase.COMPLETE
    return orchestrator


@pytest.mark.parametrize("mutation", ("replace", "delete"))
def test_completed_run_fails_closed_when_reviewed_predecessor_changes(
    tmp_path: Path, mutation: str
) -> None:
    orchestrator = _completed_orchestrator(tmp_path)
    if mutation == "replace":
        reviewed = orchestrator.lifecycle.read_history(PLAN_PATH, 2)
        assert isinstance(reviewed.payload, dict)
        changed = seal_artifact(
            ResearchArtifact(
                envelope=reviewed.envelope.model_copy(update={"content_hash": None}),
                payload={
                    **reviewed.payload,
                    "assumptions": ["Replaced after completion"],
                },
            )
        )
        orchestrator.lifecycle.store.write_structured(PLAN_V2_PATH, changed)
    else:
        (tmp_path / PLAN_V2_PATH).unlink()
    orchestrator.close()

    recovered = ResearchOrchestrator()
    with pytest.raises(ValueError, match="completed final approval|stale|corrupt"):
        recovered.initialize(
            config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
        )


@pytest.mark.parametrize("corruption", ("wrong-schema", "wrong-node", "extra"))
def test_idempotent_history_adoption_rejects_complete_envelope_corruption(
    tmp_path: Path, corruption: str
) -> None:
    lifecycle = ResearchArtifactLifecycle(tmp_path, "run-envelope-identity")
    lifecycle.persist_structured(PLAN_PATH, broad_brief(), "research-intake", ())
    original = lifecycle.read_history(PLAN_PATH, 1)
    envelope_updates: dict[str, object] = {"content_hash": None}
    if corruption == "wrong-schema":
        envelope_updates["schema_version"] = "forged-schema"
    else:
        provenance = dict(original.envelope.provenance)
        if corruption == "wrong-node":
            provenance["node"] = "forged-node"
        else:
            provenance["unexpected"] = "forged-extra"
        envelope_updates["provenance"] = provenance
    forged = seal_artifact(
        ResearchArtifact(
            envelope=original.envelope.model_copy(update=envelope_updates),
            payload=original.payload,
        )
    )
    lifecycle.store.write_structured(PLAN_V1_PATH, forged)

    with pytest.raises(FileExistsError, match="conflicting immutable"):
        lifecycle.persist_structured(PLAN_PATH, broad_brief(), "research-intake", ())


def test_approved_plan_and_terminal_checkpoint_bind_reviewed_gate_context(
    tmp_path: Path,
) -> None:
    orchestrator = _completed_orchestrator(tmp_path)
    context = orchestrator.bound_gates.active_context("final-gate")
    assert context is not None
    assert context.context_hash is not None
    approved = orchestrator.lifecycle.read_artifact(PLAN_PATH)
    predecessor = context.artifact_refs[1]

    assert approved.envelope.provenance == {
        "node": "human-final-gate",
        "artifact_path": PLAN_PATH.as_posix(),
        "predecessor_ref": predecessor.model_dump(mode="json"),
        "gate_context_hash": context.context_hash,
    }
    checkpoint = json.loads(
        (tmp_path / "node-checkpoints/final-approval.json").read_text()
    )
    assert checkpoint["input_hashes"] == {
        "analysis-plan@3": approved.envelope.content_hash,
        f"final-gate-context@{context.revision}": context.context_hash,
        f"reviewed-plan@{predecessor.artifact_version}": predecessor.content_hash,
    }
