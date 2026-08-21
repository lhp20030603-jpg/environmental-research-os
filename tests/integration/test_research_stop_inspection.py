"""Read-only reconstruction boundaries for a blocked Research run."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
from orchestrator_fixtures import ready_for_final_gate, revision_capability, submit
from pydantic import TypeAdapter

from envresearch.models.artifact import ResearchArtifact, seal_artifact
from envresearch.models.design import (
    DesignFinding,
    DesignReviewPayload,
    ReviewSeverity,
)
from envresearch.research import stop_inspection as stop_module
from envresearch.research.audit_state import ResearchAuditState
from envresearch.research.orchestrator import ResearchOrchestrator
from envresearch.research.revisions import RevisionTransaction
from envresearch.research.stop_inspection import inspect_research_stop
from envresearch.workers.queue import FilesystemWorkerQueue


def _tree_state(root: Path) -> tuple[tuple[object, ...], ...]:
    state: list[tuple[object, ...]] = []
    for path in (root, *sorted(root.rglob("*"))):
        metadata = path.lstat()
        if stat.S_ISREG(metadata.st_mode):
            content: bytes | None = path.read_bytes()
        elif stat.S_ISLNK(metadata.st_mode):
            content = os.readlink(path).encode()
        else:
            content = None
        state.append(
            (
                path.relative_to(root).as_posix() if path != root else ".",
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_uid,
                stat.S_IMODE(metadata.st_mode),
                metadata.st_nlink,
                content,
            )
        )
    return tuple(state)


def _authority_state(root: Path) -> tuple[tuple[tuple[object, ...], ...], ...]:
    control = root.parent / f".{root.name}.worker-queue-control"
    return (
        _tree_state(root),
        *(() if not control.exists() else (_tree_state(control),)),
    )


def _inspect_repeated_unchanged(
    root: Path,
    error: type[BaseException] | None = None,
    match: str | None = None,
) -> object:
    before = _authority_state(root)
    descriptors = len(os.listdir("/dev/fd"))
    result: object = None
    for _ in range(3):
        if error is None:
            result = inspect_research_stop(root)
        else:
            with pytest.raises(error, match=match):
                inspect_research_stop(root)
        assert _authority_state(root) == before
        assert len(os.listdir("/dev/fd")) == descriptors
    return result


def _blocker() -> DesignFinding:
    return DesignFinding(
        finding_id="blocking-identification",
        severity=ReviewSeverity.BLOCKING,
        resolved=False,
        finding="The comparison design is not credible with available support.",
        evidence_refs=("evidence-1",),
        remediation="Provide a supported comparison design or stop.",
    )


@pytest.fixture
def blocked_research_root(tmp_path: Path) -> Path:
    root = tmp_path / "blocked-research"
    orchestrator = ready_for_final_gate(root)
    orchestrator.request_revision(
        "review-design",
        reason="Install the canonical blocking review",
        actor="critic",
        principal_capability=revision_capability(orchestrator),
    )
    submit(
        orchestrator,
        "review-design",
        DesignReviewPayload(review_id="review-blocked", findings=(_blocker(),)),
    )
    assert orchestrator.advance().phase.value == "blocked"
    orchestrator.close()
    return root


def _forbidden(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("a read-only stop inspection called a mutator")


def test_inspect_blocked_run_is_complete_and_zero_write(
    blocked_research_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calling any writer or omitting exact stop evidence breaks this test."""
    monkeypatch.setattr(ResearchOrchestrator, "initialize", _forbidden)
    monkeypatch.setattr(ResearchOrchestrator, "advance", _forbidden)
    monkeypatch.setattr(ResearchOrchestrator, "_summarize", _forbidden)
    monkeypatch.setattr(RevisionTransaction, "recover_pending", _forbidden)
    monkeypatch.setattr(ResearchAuditState, "sync", _forbidden)
    monkeypatch.setattr(FilesystemWorkerQueue, "issue", _forbidden)

    inspected = _inspect_repeated_unchanged(blocked_research_root)

    assert hasattr(inspected, "phase")
    assert inspected.phase == "blocked"
    assert inspected.stop_code == "RESEARCH_RUN_BLOCKED"
    assert inspected.run_id == "run-orchestration"
    assert inspected.review_ref is not None
    assert inspected.review_ref.artifact_id == "design-review-findings"
    assert tuple(item.artifact_id for item in inspected.findings) == (
        "blocking-identification",
    )
    expected_finding = hashlib.sha256(_blocker().model_dump_json().encode()).hexdigest()
    assert inspected.findings[0].content_hash == expected_finding
    assert inspected.checkpoints
    review_checkpoint = next(
        item for item in inspected.checkpoints if item.node_id == "review-design"
    )
    checkpoint_bytes = (
        blocked_research_root / "node-checkpoints/review-design.json"
    ).read_bytes()
    assert (
        review_checkpoint.checkpoint_sha256
        == hashlib.sha256(checkpoint_bytes).hexdigest()
    )
    review_evidence = next(
        item
        for item in inspected.research_evidence
        if item.relative_path == "artifacts/design-review-findings.json"
    )
    assert (
        review_evidence.sha256
        == hashlib.sha256(
            (blocked_research_root / review_evidence.relative_path).read_bytes()
        ).hexdigest()
    )


def test_inspection_rejects_nonblocked_and_missing_authority(tmp_path: Path) -> None:
    """Treating an incomplete or nonblocked run as a correct stop is a bug."""
    missing = tmp_path / "missing"
    missing.mkdir()
    _inspect_repeated_unchanged(missing, FileNotFoundError)

    waiting = tmp_path / "waiting"
    orchestrator = ready_for_final_gate(waiting)
    orchestrator.close()
    _inspect_repeated_unchanged(
        waiting, ValueError, match="not a correct-stop candidate"
    )


def test_inspection_rejects_malformed_review_and_changed_checkpoint(
    blocked_research_root: Path,
) -> None:
    """Reconstruction must authenticate persisted review and checkpoint bytes."""
    review = blocked_research_root / "artifacts/design-review-findings.json"
    original_review = review.read_bytes()
    review.write_bytes(b"{}")
    _inspect_repeated_unchanged(blocked_research_root, ValueError)
    review.write_bytes(original_review)

    checkpoint = blocked_research_root / "node-checkpoints/review-design.json"
    data = checkpoint.read_bytes()
    checkpoint.write_bytes(data.replace(b'"review-design"', b'"review-desigN"', 1))
    _inspect_repeated_unchanged(blocked_research_root, ValueError)


def test_inspection_rejects_review_with_wrong_provenance_path(
    blocked_research_root: Path,
) -> None:
    """A sealed review from another authority path cannot define this stop."""
    review = blocked_research_root / "artifacts/design-review-findings.json"
    artifact = TypeAdapter(ResearchArtifact[object]).validate_json(review.read_bytes())
    envelope = artifact.envelope.model_copy(
        update={
            "content_hash": None,
            "provenance": {
                **artifact.envelope.provenance,
                "artifact_path": "artifacts/not-design-review.json",
            },
        }
    )
    forged = seal_artifact(artifact.model_copy(update={"envelope": envelope}))
    review.write_bytes(
        json.dumps(
            forged.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )

    _inspect_repeated_unchanged(blocked_research_root, ValueError, match="provenance")


@pytest.mark.parametrize(
    "relative",
    (
        "artifacts/design-review-findings.json",
        "node-checkpoints/review-design.json",
    ),
)
def test_inspection_rejects_authority_replaced_before_tree_traversal(
    blocked_research_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
) -> None:
    """Review/checkpoint replacement must not produce mixed-generation evidence."""
    target = blocked_research_root / relative
    original = target.read_bytes()
    real_evidence = stop_module._research_evidence
    control = blocked_research_root.parent / (
        f".{blocked_research_root.name}.worker-queue-control"
    )
    control_before = _tree_state(control)
    descriptors = len(os.listdir("/dev/fd"))

    def replace_then_read(*args: object, **kwargs: object) -> object:
        replacement = target.with_suffix(target.suffix + ".replacement")
        replacement.write_bytes(original.replace(b'"', b" ", 1))
        os.replace(replacement, target)
        return real_evidence(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(stop_module, "_research_evidence", replace_then_read)
    with pytest.raises(ValueError, match="changed during inspection"):
        inspect_research_stop(blocked_research_root)
    assert _tree_state(control) == control_before
    assert len(os.listdir("/dev/fd")) == descriptors


def test_inspection_rejects_root_entry_inserted_after_tree_traversal(
    blocked_research_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A root-level insertion before the final boundary cannot be omitted."""
    real_evidence = stop_module._research_evidence
    inserted = False

    def insert_after_read(*args: object, **kwargs: object) -> object:
        nonlocal inserted
        evidence = real_evidence(*args, **kwargs)  # type: ignore[arg-type]
        if not inserted:
            (blocked_research_root / "late-entry.txt").write_bytes(b"late")
            inserted = True
        return evidence

    monkeypatch.setattr(stop_module, "_research_evidence", insert_after_read)
    with pytest.raises(ValueError, match="changed during inspection"):
        inspect_research_stop(blocked_research_root)
