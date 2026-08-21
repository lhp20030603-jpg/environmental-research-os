"""Direct checkpoint bindings for one approved Final Gate transition."""

from __future__ import annotations

from pathlib import Path

from envresearch.models.artifact import ArtifactRef

FINAL_INPUT_PATHS = (
    Path("artifacts/analysis-plan.yaml"),
    Path("gate-bindings/reviewed-plan.json"),
    Path("gate-bindings/final-gate-context.json"),
)


def terminal_refs(
    approved: ArtifactRef,
    predecessor: ArtifactRef,
    context_hash: str,
    context_revision: int,
    citation_report: ArtifactRef | None = None,
) -> tuple[ArtifactRef, ...]:
    refs = (
        approved,
        ArtifactRef(
            artifact_id="reviewed-plan",
            artifact_version=predecessor.artifact_version,
            content_hash=predecessor.content_hash,
        ),
        ArtifactRef(
            artifact_id="final-gate-context",
            artifact_version=context_revision,
            content_hash=context_hash,
        ),
    )
    return refs if citation_report is None else (*refs, citation_report)
