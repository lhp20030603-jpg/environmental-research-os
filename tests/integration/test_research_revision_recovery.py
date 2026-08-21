"""Crash recovery and evidence-retention tests for revision transactions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from orchestrator_fixtures import (
    broad_brief,
    candidate_payload,
    config,
    revision_capability,
    submit,
)

from envresearch.models.intake import ResearchIntakeMode
from envresearch.research.orchestrator import ResearchOrchestrator


@pytest.mark.parametrize("boundary", ("artifact", "namespace", "checkpoint", "audit"))
def test_interrupted_revision_resumes_at_every_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    if boundary == "artifact":
        real = orchestrator.lifecycle.supersede
        target = orchestrator.lifecycle
        name = "supersede"
    elif boundary == "namespace":
        real = orchestrator.queue.archive_generation
        target = orchestrator.queue
        name = "archive_generation"
    elif boundary == "checkpoint":
        real = orchestrator.checkpoints.invalidate
        target = orchestrator.checkpoints
        name = "invalidate"
    else:
        real = orchestrator.audit.record_revision
        target = orchestrator.audit
        name = "record_revision"

    def crash_after(*args: object, **kwargs: object) -> object:
        real(*args, **kwargs)  # type: ignore[misc]
        raise RuntimeError(f"crash after {boundary}")

    monkeypatch.setattr(target, name, crash_after)
    with pytest.raises(RuntimeError, match=f"after {boundary}"):
        orchestrator.request_revision(
            "frame-charters",
            reason="Recover revision",
            actor="researcher",
            principal_capability=revision_capability(orchestrator),
        )
    orchestrator.close()
    monkeypatch.undo()

    recovered = ResearchOrchestrator()
    recovered.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    revision = recovered.request_revision(
        "frame-charters",
        reason="Recover revision",
        actor="researcher",
        principal_capability=revision_capability(recovered),
    )
    assert revision.node_id == "frame-charters"
    assert (tmp_path / "work-orders/frame-charters.json").exists()
    assert not (tmp_path / "node-checkpoints/frame-charters.json").exists()


def test_recovery_backfills_intent_event_before_any_revision_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    real_append = orchestrator.revisions._append

    def die_before_intent_event(intent: object, event: str) -> None:
        if event == "revision_intent":
            raise RuntimeError("crash before revision intent event")
        real_append(intent, event)  # type: ignore[arg-type]

    monkeypatch.setattr(orchestrator.revisions, "_append", die_before_intent_event)
    with pytest.raises(RuntimeError, match="before revision intent"):
        orchestrator.request_revision(
            "frame-charters",
            reason="Recover intent event",
            actor="researcher",
            principal_capability=revision_capability(orchestrator),
        )
    orchestrator.close()
    monkeypatch.undo()

    recovered = ResearchOrchestrator()
    recovered.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    events = [
        json.loads(line)["event"]
        for line in (tmp_path / "revisions/journal.jsonl").read_text().splitlines()
    ]
    assert events[0] == "revision_intent"
    assert events[-1] == "revision_completed"


def test_revision_fails_closed_when_authenticated_submission_is_missing(
    tmp_path: Path,
) -> None:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    submission = tmp_path / "worker-submissions/frame-charters"
    submission.rename(tmp_path / "missing-submission-evidence")

    with pytest.raises((FileNotFoundError, ValueError), match="submission|generation"):
        orchestrator.request_revision(
            "frame-charters",
            reason="Must retain evidence",
            actor="researcher",
            principal_capability=revision_capability(orchestrator),
        )

    assert (tmp_path / "node-checkpoints/frame-charters.json").exists()
    assert (
        "superseded" not in (tmp_path / "artifacts/candidate-charters.json").read_text()
    )
