"""Durable policy constraints attached to research work orders."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, JsonValue

from envresearch.benchmarks.blind_claim_semantics import (
    require_source_independent_recommendation,
)
from envresearch.benchmarks.blind_paths import BlindArtifactPaths
from envresearch.benchmarks.claim_integrity import CitationIntegrityValidator
from envresearch.kernel.artifact_graph import ArtifactGraph, ArtifactNode
from envresearch.methods.registry import MethodProfileRegistry
from envresearch.models.benchmark_claims import ClaimFactMap, ClaimUsage
from envresearch.models.benchmark_evaluation import ExpertDimension, ExpertScoreSheet
from envresearch.models.enums import ArtifactLifecycle
from envresearch.research.run_binding import method_profile_digests
from envresearch.research.workflow import ResearchRunConfig
from envresearch.workers.contracts import WorkerRole
from envresearch.workers.filesystem import PinnedRoot

if TYPE_CHECKING:
    from envresearch.benchmarks.blind_artifacts import BlindArtifactLifecycle

_BLIND_COMMON = (
    "No network or connector access",
    "Use only the declared local inputs",
    "Do not read paths outside this isolated workspace",
    "Do not execute empirical analysis",
)
_CORE_EXPERT_DIMENSIONS = frozenset(
    {
        ExpertDimension.IDENTIFICATION_FIT,
        ExpertDimension.ASSUMPTIONS_THREATS,
        ExpertDimension.DATA_COMPATIBILITY,
    }
)
_CURATOR_SIDE_CHANNEL = re.compile(r"\bsources?\b", re.IGNORECASE)
_FACT_ID = re.compile(r"fact-[a-z0-9]+(?:-[a-z0-9]+)*")


@dataclass(frozen=True, slots=True)
class BlindWorkflowStatus:
    """Small recovery-facing snapshot without curator-side content."""

    case_id: str
    completed_nodes: tuple[str, ...]
    stale_nodes: tuple[str, ...]
    current_lineage: bool
    adjudication_required: bool
    third_score_locked: bool
    adjudication_completed: bool


def build_blind_graph(case_id: str) -> ArtifactGraph:
    """Return the canonical Task 5/Task 8 blind evaluation DAG."""
    paths = BlindArtifactPaths.for_case(case_id)
    return ArtifactGraph(
        (
            ArtifactNode("curate-source", "benchmark-curator", output_paths=(paths.source_sheet,)),
            ArtifactNode(
                "mask-brief", "benchmark-masker", ("curate-source",),
                (paths.source_sheet,), (paths.blinded_brief, paths.claim_fact_map),
            ),
            ArtifactNode(
                "validate-leakage", "benchmark-leakage-validator", ("mask-brief",),
                (paths.source_sheet, paths.blinded_brief), (paths.leakage_report,),
            ),
            ArtifactNode(
                "recommend-method", "benchmark-recommender", ("validate-leakage",),
                (paths.blinded_brief, paths.leakage_report), (paths.recommendation,),
            ),
            ArtifactNode(
                "verify-citations", "benchmark-leakage-validator", ("recommend-method",),
                (paths.source_sheet, paths.claim_fact_map, paths.recommendation),
                (paths.citation_report,),
            ),
            ArtifactNode(
                "expert-score-1", "benchmark-expert", ("recommend-method",),
                (paths.blinded_brief, paths.recommendation), (paths.expert_one,),
            ),
            ArtifactNode(
                "expert-score-2", "benchmark-expert", ("recommend-method",),
                (paths.blinded_brief, paths.recommendation), (paths.expert_two,),
            ),
        )
    )


def work_order_constraints(
    config: ResearchRunConfig, *, entry_order: bool
) -> tuple[str, ...]:
    """Return baseline constraints plus explicit config binding at entry."""
    constraints = (
        "Use only declared local inputs",
        "Do not execute empirical analysis",
    )
    if not entry_order:
        return constraints
    weights = json.dumps(
        config.ranking_policy.model_dump(mode="json")["weights"],
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = config.config_sha256 or "unbound-direct-run"
    return constraints + (
        f"Explicit run config SHA-256: {digest}",
        f"Ranking weights: {weights}",
    )


def canonical_blind_json(value: object) -> bytes:
    """Serialize one deterministic worker-visible JSON document."""
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def publish_isolated_workspace(
    workspace: Path, documents: tuple[tuple[str, bytes], ...]
) -> None:
    """Atomically expose one exact workspace before its queue becomes live."""
    expected = tuple(sorted(name for name, _ in documents))
    if workspace.exists():
        _require_workspace(workspace, documents, expected)
        return
    workspace.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".blind-stage-", dir=workspace.parent) as temp:
        stage = Path(temp) / "workspace"
        pinned = PinnedRoot(stage, private=True)
        try:
            for name, data in documents:
                pinned.write_file_noreplace(Path(name), data, mode=0o600)
        finally:
            pinned.close()
        os.rename(stage, workspace)
    _require_workspace(workspace, documents, expected)


def extend_isolated_workspace(
    workspace: Path,
    documents: tuple[tuple[str, bytes], ...],
    existing_documents: tuple[tuple[str, bytes], ...],
    publish: Callable[[], object],
    rollback: Callable[[], object],
) -> None:
    """Keep validation, publication, and rollback in one pinned boundary."""
    current = tuple(sorted(name for name, _ in existing_documents))
    final = tuple(sorted((*current, *(name for name, _ in documents))))
    pinned = PinnedRoot(workspace, private=True)
    try:
        names = pinned.list_directory(Path())
        if names not in (current, final):
            raise ValueError("isolated workspace contains unexpected entries")
        expected = dict((*existing_documents, *documents))
        if any(
            pinned.read_file(Path(name), description="isolated input") != expected[name]
            for name in names
        ):
            raise ValueError("isolated workspace contains unexpected entries")
        if names == current:
            for name, data in documents:
                pinned.write_file_noreplace(Path(name), data, mode=0o600)
        _require_pinned_workspace(pinned, (*existing_documents, *documents), final)
        try:
            publish()
            _require_pinned_workspace(pinned, (*existing_documents, *documents), final)
        except BaseException:
            rollback()
            raise
    finally:
        pinned.close()


def archive_isolated_roots(
    run_root: Path, roots: tuple[Path, ...], revision_id: str) -> None:
    """Move every role root into one revision namespace."""
    archive = run_root / "revisions" / revision_id / "isolated"
    for root in roots:
        if root.exists():
            destination = archive / root.relative_to(run_root / "isolated")
            destination.parent.mkdir(parents=True, exist_ok=True)
            root.rename(destination)


def _require_workspace(
    workspace: Path, documents: tuple[tuple[str, bytes], ...], expected: tuple[str, ...]
) -> None:
    pinned = PinnedRoot(workspace, private=True)
    try:
        _require_pinned_workspace(pinned, documents, expected)
    finally:
        pinned.close()


def _require_pinned_workspace(
    pinned: PinnedRoot, documents: tuple[tuple[str, bytes], ...],
    expected: tuple[str, ...],
) -> None:
    if pinned.list_directory(Path()) != expected or any(
        pinned.read_file(Path(name), description="isolated input") != data
        for name, data in documents
    ):
        raise ValueError("isolated workspace contains unexpected entries")


def write_blind_visible(root: Path, filename: str, payload: object) -> None:
    """Create or verify one exact worker-visible output."""
    pinned = PinnedRoot(root, private=True)
    try:
        value = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        data, path = canonical_blind_json(value), Path(filename)
        if pinned.exists(path):
            if pinned.read_file(path, description="isolated output") != data:
                raise ValueError("isolated output identity collision")
        else:
            pinned.write_file_noreplace(path, data, mode=0o600)
    finally:
        pinned.close()


def blind_order_constraints(role: WorkerRole) -> tuple[str, ...]:
    """Return the disclosure policy for one blind evaluation worker."""
    if role is WorkerRole.BENCHMARK_RECOMMENDER:
        return _BLIND_COMMON + (
            "Use exactly the three ordered inputs in this work order",
            "Return one method recommendation without external lookups",
        )
    if role is WorkerRole.BENCHMARK_EXPERT:
        return _BLIND_COMMON + (
            "Score independently without seeking another score",
            "Return one complete five-dimension score sheet",
        )
    if role is WorkerRole.BENCHMARK_ADJUDICATOR:
        return _BLIND_COMMON + (
            "Submit and lock a blind third score before disagreement review",
            "Return one complete five-dimension score sheet",
        )
    raise ValueError("unsupported blind work-order role")


def blind_profile_payload(root: Path) -> dict[str, object]:
    """Return the pinned recommender projection and its canonical registry digest."""
    registry = MethodProfileRegistry.discover(root)
    profiles = {
        profile_id: _blind_profile_value(
            profile.model_dump(
                mode="json",
                exclude={"methodological_references", "estimator_entrypoint"},
            )
        )
        for profile_id, profile in sorted(registry.profiles.items())
    }
    identity = {
        "profile_sha256": method_profile_digests(registry),
        "profiles": profiles,
    }
    return {
        "registry_sha256": hashlib.sha256(
            json.dumps(
                identity,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest(),
        **identity,
    }


def _blind_profile_value(value: object) -> object:
    """Remove curator-side vocabulary from the worker-visible projection."""
    if isinstance(value, str):
        return _CURATOR_SIDE_CHANNEL.sub("evidence input", value)
    if isinstance(value, list):
        return [_blind_profile_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _blind_profile_value(item) for key, item in value.items()}
    return value


def blind_expert_rubric() -> dict[str, object]:
    """Return the source-independent rubric copied into every scoring root."""
    return {
        "rubric_version": "blind-method-v1",
        "dimensions": [item.value for item in ExpertDimension],
        "score_range": [0, 4],
        "independent_review": True,
    }


def blind_claim_usages(payload: JsonValue, mapping: ClaimFactMap) -> tuple[ClaimUsage, ...]:
    """Bind only structured fact-reference leaves to their exact mapped claims."""
    if not isinstance(payload, Mapping):
        raise TypeError("recommendation payload must be a mapping")
    fact_refs = payload.get("fact_refs")
    if (
        not isinstance(fact_refs, Sequence)
        or isinstance(fact_refs, (str, bytes, bytearray))
        or not fact_refs
    ):
        raise ValueError("citation integrity validation failed")
    require_source_independent_recommendation(
        payload, CitationIntegrityValidator._is_source_dependent
    )
    claims = {entry.fact_id: entry.claim_id for entry in mapping.entries}
    usages: list[ClaimUsage] = []
    seen: set[str] = set()
    for index, fact_id in enumerate(fact_refs):
        if (
            not isinstance(fact_id, str)
            or _FACT_ID.fullmatch(fact_id) is None
            or fact_id not in claims
            or fact_id in seen
        ):
            raise ValueError("citation integrity validation failed")
        seen.add(fact_id)
        usages.append(
            ClaimUsage(
                claim_id=claims[fact_id],
                statement_sha256=hashlib.sha256(fact_id.encode()).hexdigest(),
                json_pointer=f"/fact_refs/{index}",
            )
        )
    return tuple(usages)


def blind_scores_require_adjudication(
    first: ExpertScoreSheet, second: ExpertScoreSheet
) -> bool:
    """Apply the approved verdict, Critical, and core-gap trigger policy."""
    if first.verdict != second.verdict or first.critical_findings or second.critical_findings:
        return True
    first_scores = {item.dimension: item.score for item in first.scores}
    second_scores = {item.dimension: item.score for item in second.scores}
    return any(
        abs(first_scores[item] - second_scores[item]) > 1
        for item in _CORE_EXPERT_DIMENSIONS
    )


def blind_workflow_status(
    graph: ArtifactGraph,
    artifacts: BlindArtifactLifecycle,
    case_id: str,
    *,
    third_score_locked: bool,
) -> BlindWorkflowStatus:
    """Derive current and stale graph nodes from exact Task 8 envelopes."""
    completed: list[str] = []
    stale: list[str] = []
    for node in graph.nodes:
        states = tuple(_artifact_state(artifacts, path) for path in node.output_paths)
        if states and all(state is ArtifactLifecycle.VALIDATED for state in states):
            completed.append(node.node_id)
        elif any(state is ArtifactLifecycle.SUPERSEDED for state in states):
            stale.append(node.node_id)
    paths = artifacts.paths(case_id)
    scores_current = all(
        _artifact_state(artifacts, path) is ArtifactLifecycle.VALIDATED
        for path in (paths.expert_one, paths.expert_two)
    )
    try:
        if not scores_current:
            raise ValueError("expert scores are not current")
        first, second = (
            artifacts.lifecycle.read_payload(path, ExpertScoreSheet)
            for path in (paths.expert_one, paths.expert_two)
        )
        adjudication_required = blind_scores_require_adjudication(first, second)
    except (FileNotFoundError, ValueError):
        adjudication_required = False
    adjudication = artifacts.paths(case_id).adjudication
    return BlindWorkflowStatus(
        case_id=case_id,
        completed_nodes=tuple(completed),
        stale_nodes=tuple(stale),
        current_lineage=not stale and len(completed) == len(graph.nodes),
        adjudication_required=adjudication_required,
        third_score_locked=third_score_locked,
        adjudication_completed=(
            _artifact_state(artifacts, adjudication) is ArtifactLifecycle.VALIDATED
        ),
    )


def _artifact_state(artifacts: BlindArtifactLifecycle, path: Path) -> ArtifactLifecycle | None:
    if not (artifacts.lifecycle.workspace / path).exists():
        return None
    return artifacts.lifecycle.current_envelope(path).validation_status
