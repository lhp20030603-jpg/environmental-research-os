"""Private persistence for bounded execution and verification evidence."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from envresearch.models.artifact import ArtifactRef, ResearchArtifact
from envresearch.replication._service_support import artifact_ref, persist_payload
from envresearch.replication.container import ContainerResult
from envresearch.replication.verify import VerificationReport
from envresearch.storage.research_artifacts import ResearchArtifactStore


def persist_execution_log(
    store: ResearchArtifactStore,
    stage: str,
    result: ContainerResult,
    inputs: tuple[ArtifactRef, ...],
) -> ArtifactRef:
    if stage not in {"author-reproduction", "derived-did-event-study"}:
        raise ValueError("execution log stage is not admitted")
    payload = {
        "stage": stage,
        "stdout_sha256": result.stdout_sha256,
        "stderr_sha256": result.stderr_sha256,
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
        "stdout": "[redacted]",
        "stderr": "[redacted]",
    }
    return persist_payload(
        store,
        "tier2-execution-log",
        "logs",
        payload,
        inputs,
        "tier2-replication",
    )


def persist_verification_report(
    store: ResearchArtifactStore, report: VerificationReport
) -> ArtifactRef:
    artifact = cast(ResearchArtifact[object], report.artifact)
    reference = artifact_ref(artifact)
    store.write_structured(
        Path(f"artifacts/replication/verifications/{reference.content_hash}.json"),
        artifact,
    )
    return reference
