"""Read-only reconstruction of one exact blocked Research authority."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import TypeAlias

from pydantic import BaseModel, TypeAdapter

from envresearch.kernel.gates import GateRequest
from envresearch.kernel.node_checkpoint_schema import read_checkpoint_at
from envresearch.models.artifact import ArtifactRef, ResearchArtifact, verify_artifact
from envresearch.models.design import DesignFinding, DesignReviewPayload, ReviewSeverity
from envresearch.models.enums import GateStatus
from envresearch.research.artifact_lifecycle_support import artifact_ref
from envresearch.research.gate_context import BoundGateContext
from envresearch.research.gate_policy import GATE_ORDER
from envresearch.research.orchestrator import ResearchOrchestrator
from envresearch.research.orchestrator_summary import summarize
from envresearch.research.stop_contracts import (
    ResearchCheckpointEvidence,
    ResearchFileEvidence,
    ResearchStopInspection,
)
from envresearch.research.workflow import ResearchRunPhase, ResearchRunSummary
from envresearch.workers.filesystem import PinnedRoot

_READ_FLAGS = (
    os.O_RDONLY
    | os.O_CLOEXEC
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)
_DIRECTORY_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
)
_REVIEW_PATH = Path("artifacts/design-review-findings.json")
_ObservedFile: TypeAlias = tuple[Path, bytes, tuple[int, ...]]


def inspect_research_stop(workspace: Path) -> ResearchStopInspection:
    """Reopen and reconstruct a blocked run without mutation or recovery."""
    from envresearch.factory.authority import open_existing_research_authority

    orchestrator = open_existing_research_authority(workspace)
    try:
        with PinnedRoot(orchestrator.workspace, create=False) as root:
            findings, review_ref, review_observation = _review_evidence(root)
            summary = _pure_summary(orchestrator, findings)
            if summary.phase is not ResearchRunPhase.BLOCKED:
                raise ValueError("research run is not a correct-stop candidate")
            if not findings or review_ref is None or review_observation is None:
                raise ValueError("blocked research run has no unresolved blocker")
            gate_ref, context_sha256, gate_observations = _blocking_gate_evidence(
                orchestrator, root
            )
            checkpoints, checkpoint_observations = _checkpoint_evidence(
                orchestrator, summary
            )
            evidence = _research_evidence(root)
            _require_observations(
                root,
                (review_observation, *gate_observations, *checkpoint_observations),
            )
            if _pure_summary(orchestrator, findings) != summary:
                raise ValueError("research authority changed during inspection")
            if _research_evidence(root) != evidence:
                raise ValueError("research authority changed during inspection")
            root.require_attached()
            return ResearchStopInspection(
                schema_version="research.stop-inspection.v1",
                run_id=summary.run_id,
                phase="blocked",
                stop_code="RESEARCH_RUN_BLOCKED",
                blocking_gate_ref=gate_ref,
                blocking_gate_context_sha256=context_sha256,
                findings=_finding_refs(findings),
                review_ref=review_ref,
                checkpoints=checkpoints,
                research_evidence=evidence,
            )
    finally:
        orchestrator.close()


def load_open_design_findings(
    orchestrator: ResearchOrchestrator,
) -> tuple[DesignFinding, ...]:
    """Load only unresolved blocking findings from the current exact review."""
    with PinnedRoot(orchestrator.workspace, create=False) as root:
        return _review_evidence(root)[0]


def _pure_summary(
    orchestrator: ResearchOrchestrator, findings: tuple[DesignFinding, ...]
) -> ResearchRunSummary:
    return summarize(
        run_id=orchestrator.config.run_id,
        graph=orchestrator.graph,
        workspace=orchestrator.workspace,
        lifecycle=orchestrator.lifecycle,
        checkpoints=orchestrator.checkpoints,
        gate_lookup=orchestrator.bound_gates.active_gate,
        has_open_blocker=lambda: bool(findings),
        require_complete_final=_forbid_complete_target,
        gate_order=GATE_ORDER,
    )


def _review_evidence(
    root: PinnedRoot,
) -> tuple[tuple[DesignFinding, ...], ArtifactRef | None, _ObservedFile | None]:
    try:
        observation = _observe_relative(root, _REVIEW_PATH)
    except FileNotFoundError:
        return (), None, None
    data = observation[1]
    artifact = TypeAdapter(ResearchArtifact[object]).validate_json(data)
    verify_artifact(artifact)
    canonical = _canonical_model(artifact)
    if data != canonical or artifact.envelope.artifact_id != _REVIEW_PATH.stem:
        raise ValueError("design review bytes are not canonical")
    if artifact.envelope.provenance.get("artifact_path") != _REVIEW_PATH.as_posix():
        raise ValueError("design review provenance path mismatch")
    review = DesignReviewPayload.model_validate(artifact.payload)
    findings = tuple(
        sorted(
            (
                finding
                for finding in review.findings
                if not finding.resolved and finding.severity is ReviewSeverity.BLOCKING
            ),
            key=lambda finding: finding.finding_id,
        )
    )
    return findings, artifact_ref(artifact.envelope), observation


def _forbid_complete_target() -> None:
    raise ValueError("complete research run is not a correct-stop candidate")


def _finding_refs(findings: tuple[DesignFinding, ...]) -> tuple[ArtifactRef, ...]:
    return tuple(
        ArtifactRef(
            artifact_id=finding.finding_id,
            artifact_version=1,
            content_hash=hashlib.sha256(finding.model_dump_json().encode()).hexdigest(),
        )
        for finding in findings
    )


def _blocking_gate_evidence(
    orchestrator: ResearchOrchestrator, root: PinnedRoot
) -> tuple[ArtifactRef | None, str | None, tuple[_ObservedFile, ...]]:
    for base_gate_id in GATE_ORDER:
        gate = orchestrator.bound_gates.active_gate(base_gate_id)
        if gate is None or gate.status is not GateStatus.REJECTED:
            continue
        context = orchestrator.bound_gates.active_context(base_gate_id)
        if context is None or context.gate_id != gate.id:
            raise ValueError("blocking gate has no exact active context")
        gate_observation = _read_canonical_model(
            root, Path("gates") / f"{gate.id}.json", gate
        )
        context_observation = _read_canonical_model(
            root,
            Path("gate-contexts") / base_gate_id / f"{context.revision:04d}.json",
            context,
        )
        return (
            ArtifactRef(
                artifact_id=gate.id,
                artifact_version=context.revision,
                content_hash=hashlib.sha256(gate_observation[1]).hexdigest(),
            ),
            hashlib.sha256(context_observation[1]).hexdigest(),
            (gate_observation, context_observation),
        )
    return None, None, ()


def _read_canonical_model(
    root: PinnedRoot, path: Path, model: BaseModel
) -> _ObservedFile:
    observation = _observe_relative(root, path)
    data = observation[1]
    expected = _canonical_model(model)
    if data != expected:
        raise ValueError(f"{path.name} bytes are not canonical")
    if isinstance(model, GateRequest):
        GateRequest.model_validate_json(data)
    elif isinstance(model, BoundGateContext):
        BoundGateContext.model_validate_json(data)
    return observation


def _canonical_model(model: BaseModel) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _checkpoint_evidence(
    orchestrator: ResearchOrchestrator, summary: ResearchRunSummary
) -> tuple[tuple[ResearchCheckpointEvidence, ...], tuple[_ObservedFile, ...]]:
    nodes = {node.node_id: node for node in orchestrator.graph.nodes}
    directory = orchestrator.checkpoints._checkpoints_fd
    evidence: list[ResearchCheckpointEvidence] = []
    observations: list[_ObservedFile] = []
    for node_id in sorted(summary.completed_nodes):
        filename = f"{node_id}.json"
        before = os.stat(filename, dir_fd=directory, follow_symlinks=False)
        checkpoint, data = read_checkpoint_at(directory, filename, node_id)
        after = os.stat(filename, dir_fd=directory, follow_symlinks=False)
        if _stable_metadata(before) != _stable_metadata(after):
            raise ValueError("research checkpoint changed during inspection")
        observations.append(
            (Path("node-checkpoints") / filename, data, _stable_metadata(after))
        )
        references = {
            (artifact_id, int(version), digest): ArtifactRef(
                artifact_id=artifact_id,
                artifact_version=int(version),
                content_hash=digest,
            )
            for key, digest in checkpoint.input_hashes.items()
            for artifact_id, version in (key.rsplit("@", 1),)
        }
        for path in nodes[node_id].output_paths:
            if path.name == "evidence-matrix.meta.json":
                continue
            reference = orchestrator.lifecycle.artifact_ref(path)
            references[
                (
                    reference.artifact_id,
                    reference.artifact_version,
                    reference.content_hash,
                )
            ] = reference
        evidence.append(
            ResearchCheckpointEvidence(
                node_id=node_id,
                checkpoint_sha256=hashlib.sha256(data).hexdigest(),
                artifact_refs=tuple(references[key] for key in sorted(references)),
            )
        )
    return tuple(evidence), tuple(observations)


def _research_evidence(root: PinnedRoot) -> tuple[ResearchFileEvidence, ...]:
    root.require_attached()
    entries = [_directory_evidence(Path("."), os.fstat(root.fd))]
    entries.extend(_walk_directory(root.fd, Path()))
    root.require_attached()
    return tuple(sorted(entries, key=lambda item: item.relative_path))


def _walk_directory(parent_fd: int, parent: Path) -> list[ResearchFileEvidence]:
    entries: list[ResearchFileEvidence] = []
    directory_before = os.fstat(parent_fd)
    names = tuple(sorted(os.listdir(parent_fd)))
    for name in names:
        relative = parent / name
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISREG(before.st_mode):
            data = _read_exact_file(parent_fd, name, before)
            entries.append(
                ResearchFileEvidence(
                    relative_path=relative.as_posix(),
                    kind="file",
                    sha256=hashlib.sha256(data).hexdigest(),
                    size_bytes=before.st_size,
                    mode=stat.S_IMODE(before.st_mode),
                )
            )
        elif stat.S_ISDIR(before.st_mode):
            entries.append(_directory_evidence(relative, before))
            descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
            try:
                if _identity(os.fstat(descriptor)) != _identity(before):
                    raise ValueError("research directory changed during inspection")
                entries.extend(_walk_directory(descriptor, relative))
                _require_unchanged(parent_fd, name, before)
            finally:
                os.close(descriptor)
        elif stat.S_ISLNK(before.st_mode):
            target = os.readlink(name, dir_fd=parent_fd)
            _require_unchanged(parent_fd, name, before)
            entries.append(
                ResearchFileEvidence(
                    relative_path=relative.as_posix(),
                    kind="symlink",
                    sha256=None,
                    size_bytes=before.st_size,
                    mode=stat.S_IMODE(before.st_mode),
                    symlink_target=target,
                )
            )
        else:
            raise ValueError("research root contains an unsupported file kind")
    if tuple(sorted(os.listdir(parent_fd))) != names or _stable_metadata(
        os.fstat(parent_fd)
    ) != _stable_metadata(directory_before):
        raise ValueError("research directory changed during inspection")
    return entries


def _observe_relative(root: PinnedRoot, relative: Path) -> _ObservedFile:
    with root.directory(relative.parent) as parent_fd:
        before = os.stat(relative.name, dir_fd=parent_fd, follow_symlinks=False)
        data = _read_exact_file(parent_fd, relative.name, before)
    return relative, data, _stable_metadata(before)


def _require_observations(
    root: PinnedRoot, observations: tuple[_ObservedFile, ...]
) -> None:
    for expected in observations:
        if _observe_relative(root, expected[0]) != expected:
            raise ValueError("research authority changed during inspection")


def _read_exact_file(parent_fd: int, name: str, before: os.stat_result) -> bytes:
    descriptor = os.open(name, _READ_FLAGS, dir_fd=parent_fd)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
            raise ValueError("research file changed during inspection")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if _stable_metadata(after) != _stable_metadata(opened):
            raise ValueError("research file changed during inspection")
        _require_unchanged(parent_fd, name, after)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _directory_evidence(
    relative: Path, metadata: os.stat_result
) -> ResearchFileEvidence:
    return ResearchFileEvidence(
        relative_path=relative.as_posix(),
        kind="directory",
        sha256=None,
        size_bytes=metadata.st_size,
        mode=stat.S_IMODE(metadata.st_mode),
    )


def _require_unchanged(parent_fd: int, name: str, expected: os.stat_result) -> None:
    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if _stable_metadata(current) != _stable_metadata(expected):
        raise ValueError("research entry changed during inspection")


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _stable_metadata(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


__all__ = ["inspect_research_stop", "load_open_design_findings"]
