"""Adversarial recovery tests for authenticated revision transactions."""

from __future__ import annotations

import json
import os
import shutil
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
from envresearch.research.revision_models import RevisionIntent


@pytest.mark.parametrize("alias", ("symlink", "hardlink", "fifo"))
def test_revision_journal_never_writes_external_or_special_files(
    tmp_path: Path, alias: str
) -> None:
    """Revision recovery refuses aliased and non-regular journal inodes."""
    orchestrator = _initialized(tmp_path)
    revisions = tmp_path / "revisions"
    revisions.mkdir(exist_ok=True)
    journal = revisions / "journal.jsonl"
    victim = tmp_path / "revision-victim.jsonl"
    victim.write_bytes(b"")
    if alias == "symlink":
        journal.symlink_to(victim)
    elif alias == "hardlink":
        os.link(victim, journal)
    else:
        os.mkfifo(journal, 0o600)

    with pytest.raises((OSError, ValueError), match="symlink|regular|link count"):
        _request(
            orchestrator, "frame-charters", reason="Secure revision", actor="researcher"
        )

    assert victim.read_bytes() == b""


@pytest.mark.parametrize("alias", ("symlink", "hardlink", "fifo"))
def test_recovery_rejects_replaced_revision_intent_without_reading_victim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias: str
) -> None:
    """A public intent alias cannot supply trusted crash-recovery state."""
    orchestrator = _initialized(tmp_path)

    def crash_before_journal(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("crash before journal")

    monkeypatch.setattr(orchestrator.revisions, "_append", crash_before_journal)
    with pytest.raises(RuntimeError, match="before journal"):
        _request(
            orchestrator,
            "frame-charters",
            reason="Recover securely",
            actor="researcher",
        )
    orchestrator.close()
    monkeypatch.undo()

    intent = next((tmp_path / "revisions").glob("rev-*/intent.json"))
    victim = tmp_path / "intent-victim.json"
    victim.write_bytes(intent.read_bytes())
    victim.chmod(0o600)
    intent.unlink()
    if alias == "symlink":
        intent.symlink_to(victim)
    elif alias == "hardlink":
        os.link(victim, intent)
    else:
        os.mkfifo(intent, 0o600)

    with pytest.raises((OSError, ValueError), match="symlink|regular|link count"):
        _recover(tmp_path)
    assert victim.is_file()


def test_recovery_rejects_journal_event_whose_archive_effect_was_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An authenticated event never substitutes for its missing durable side effect."""
    orchestrator = _initialized(tmp_path)
    real_append = orchestrator.revisions._append

    def crash_after_archive_event(intent: object, event: str) -> None:
        real_append(intent, event)  # type: ignore[arg-type]
        if event == "worker_namespace_archived":
            raise RuntimeError("crash after archive event")

    monkeypatch.setattr(orchestrator.revisions, "_append", crash_after_archive_event)
    with pytest.raises(RuntimeError, match="after archive event"):
        _request(
            orchestrator,
            "frame-charters",
            reason="Reconcile archive",
            actor="researcher",
        )
    orchestrator.close()
    monkeypatch.undo()

    archived = next(
        (tmp_path / "revisions").glob("rev-*/worker/work-orders/frame-charters.json")
    )
    archived.unlink()
    with pytest.raises(ValueError, match="archive side effect missing"):
        _recover(tmp_path)


def test_recovery_never_authenticates_modified_orphan_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash orphan must already have a protected binding before recovery."""
    orchestrator = _initialized(tmp_path)

    def crash_before_journal(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("crash before journal")

    monkeypatch.setattr(orchestrator.revisions, "_append", crash_before_journal)
    with pytest.raises(RuntimeError, match="before journal"):
        _request(
            orchestrator,
            "frame-charters",
            reason="Original reason",
            actor="original-actor",
        )
    orchestrator.close()
    monkeypatch.undo()

    intent = next((tmp_path / "revisions").glob("rev-*/intent.json"))
    payload = json.loads(intent.read_bytes())
    payload["actor"] = "forged-actor"
    payload["reason"] = "Forged reason"
    intent.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    intent.chmod(0o600)

    with pytest.raises(ValueError, match="intent.*authentication|intent.*protected"):
        _recover(tmp_path)


def test_completed_revision_recovery_revalidates_archived_generation(
    tmp_path: Path,
) -> None:
    """Completion never exempts a claimed archive from recovery validation."""
    orchestrator = _initialized(tmp_path)
    _request(
        orchestrator, "frame-charters", reason="Complete securely", actor="researcher"
    )
    orchestrator.close()
    archived = next(
        (tmp_path / "revisions").glob("rev-*/worker/work-orders/frame-charters.json")
    )
    archived.unlink()

    with pytest.raises(ValueError, match="archive|revision"):
        _recover(tmp_path)


def test_completed_revision_requires_its_exact_checkpoint_invalidation(
    tmp_path: Path,
) -> None:
    """An internally consistent checkpoint rollback cannot satisfy a revision."""
    orchestrator = _initialized(tmp_path)
    _request(
        orchestrator,
        "frame-charters",
        reason="Bind checkpoint evidence",
        actor="researcher",
    )
    orchestrator.close()
    (tmp_path / "events.jsonl").write_bytes(b"")
    shutil.rmtree(tmp_path / "node-checkpoints" / "superseded")

    with pytest.raises(ValueError, match="checkpoint side effect missing"):
        _recover(tmp_path)


def test_later_gate_context_cannot_mask_missing_revision_marker(
    tmp_path: Path,
) -> None:
    """Recovery binds the marker to the context that existed at intent time."""
    orchestrator = _initialized(tmp_path)
    orchestrator.advance()
    original = orchestrator.bound_gates.active_context("gate-1")
    assert original is not None
    revision = _request(
        orchestrator, "frame-charters", reason="Bind gate evidence", actor="researcher"
    )
    later = orchestrator.bound_gates.ensure(
        "gate-1", "Later framing review", original.artifact_refs
    )
    assert later.requested_at > revision.created_at
    marker = (
        tmp_path
        / "gate-contexts"
        / "gate-1"
        / "superseded"
        / f"{original.gate_id}.json"
    )
    marker.unlink()
    orchestrator.close()

    with pytest.raises(ValueError, match="gate side effect missing"):
        _recover(tmp_path)


def test_second_revision_recovers_when_prior_gate_was_already_superseded(
    tmp_path: Path,
) -> None:
    """An authenticated no-active-gate snapshot remains recoverable."""
    orchestrator = _initialized(tmp_path)
    orchestrator.advance()
    _request(
        orchestrator, "frame-charters", reason="First gate revision", actor="researcher"
    )
    submit(orchestrator, "frame-charters", candidate_payload())
    second = _request(
        orchestrator,
        "frame-charters",
        reason="Second pre-gate revision",
        actor="researcher",
    )
    assert second.generation == 2
    assert second.gate_targets[0].gate_id is None
    orchestrator.close()

    recovered = _recover(tmp_path)
    recovered.close()


def test_revision_recovery_requires_expected_owner_for_protected_intent(
    tmp_path: Path,
) -> None:
    """Protected intent ownership is checked independently of its HMAC."""
    orchestrator = _initialized(tmp_path)
    _request(
        orchestrator, "frame-charters", reason="Owner-bound intent", actor="researcher"
    )
    owner = os.geteuid()
    protected = next(
        (orchestrator.queue.control.path / "revision-intents").glob("*.json")
    )
    relative = protected.relative_to(orchestrator.queue.control.path)

    with pytest.raises(ValueError, match="ownership"):
        orchestrator.queue.control.storage.read_file(
            relative,
            description="protected revision intent",
            required_mode=0o600,
            required_owner=owner + 1,
        )


def _initialized(root: Path) -> ResearchOrchestrator:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(config(root, ResearchIntakeMode.BROAD_TOPIC), broad_brief())
    submit(orchestrator, "frame-charters", candidate_payload())
    return orchestrator


def _recover(root: Path) -> ResearchOrchestrator:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(config(root, ResearchIntakeMode.BROAD_TOPIC), broad_brief())
    return orchestrator


def _request(
    orchestrator: ResearchOrchestrator, node_id: str, *, reason: str, actor: str
) -> RevisionIntent:
    return orchestrator.request_revision(
        node_id,
        reason=reason,
        actor=actor,
        principal_capability=revision_capability(orchestrator),
    )
