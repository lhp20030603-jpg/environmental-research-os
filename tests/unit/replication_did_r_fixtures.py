"""Shared immutable fixtures for R-first DiD adapter tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from envresearch.models.artifact import (
    ArtifactEnvelope,
    ArtifactRef,
    ProducerIdentity,
    ResearchArtifact,
    seal_artifact,
)
from envresearch.replication.contracts import (
    AcquiredPackageInventory,
    ApprovedTier2Intake,
    ContainerRuntimeProfile,
    ExternalAdmission,
    InventoryFile,
    ReplicationBudget,
    Tier2ExpectedOutput,
)
from envresearch.replication.did_r import (
    AuthorReproductionMapping,
    DidEventStudySpec,
)

SHA256 = "a" * 64


def profile() -> ContainerRuntimeProfile:
    return ContainerRuntimeProfile(
        profile_id="r-did-v1",
        image_digest="ghcr.io/envresearch/r-did@sha256:" + SHA256,
        nonroot_uid_gid="10001:10001",
    )


def package(approved_ref: ArtifactRef) -> AcquiredPackageInventory:
    return AcquiredPackageInventory(
        approved_intake_ref=approved_ref,
        archive_sha256=SHA256,
        archive_bytes=10,
        files=(
            InventoryFile(path=Path("code/run.R"), bytes=1, sha256=SHA256),
            InventoryFile(path=Path("data/analysis.csv"), bytes=1, sha256=SHA256),
        ),
    )


def approved_intake() -> ApprovedTier2Intake:
    return ApprovedTier2Intake(
        proposal_ref=ArtifactRef(
            artifact_id="proposal", artifact_version=1, content_hash=SHA256
        ),
        approval=ExternalAdmission(
            approver_id="reviewer",
            rationale="approved package",
            approved_locator="https://example.org/tiny-did-package.tar.gz",
        ),
        approved_at=datetime(2026, 8, 10, tzinfo=UTC),
    )


def approved_artifact() -> ResearchArtifact[ApprovedTier2Intake]:
    return seal_artifact(
        ResearchArtifact(
            envelope=ArtifactEnvelope(
                artifact_id="approved-tier2-intake",
                artifact_version=1,
                run_id="tier2-intake",
                created_at=datetime(2026, 8, 10, tzinfo=UTC),
                producer=ProducerIdentity(component="tier2-intake", version="0.3.0"),
            ),
            payload=approved_intake(),
        )
    )


def artifact_ref(artifact: ResearchArtifact[ApprovedTier2Intake]) -> ArtifactRef:
    assert artifact.envelope.content_hash is not None
    return ArtifactRef(
        artifact_id=artifact.envelope.artifact_id,
        artifact_version=artifact.envelope.artifact_version,
        content_hash=artifact.envelope.content_hash,
    )


def workspace(tmp_path: Path) -> tuple[Path, Path]:
    repository = tmp_path / "run-root"
    input_root = repository / "artifacts/replication/acquired/archive/approval"
    output_root = repository / "artifacts/replication/runs/approval/attempt"
    input_root.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)
    return input_root, output_root


def cleanup_workspaces() -> None:
    """Temporary workspaces are owned and removed by pytest's tmp_path."""


def budget() -> ReplicationBudget:
    return ReplicationBudget(
        max_download_bytes=100,
        max_storage_bytes=100,
        max_memory_bytes=100,
        inactivity_seconds=10,
    )


def author_mapping(tmp_path: Path) -> AuthorReproductionMapping:
    input_root, output_root = workspace(tmp_path)
    return AuthorReproductionMapping(
        script_path=Path("code/run.R"),
        output_mappings=(
            Tier2ExpectedOutput(
                path="output/table-1.csv",
                expected_path="expected/table-1.csv",
                comparator="csv_numeric",
                absolute_tolerance=0.001,
            ),
        ),
        input_root=input_root,
        output_root=output_root,
        budget=budget(),
    )


def did_spec(tmp_path: Path) -> DidEventStudySpec:
    input_root, output_root = workspace(tmp_path)
    return DidEventStudySpec(
        data_path=Path("data/analysis.csv"),
        unit_column="municipality_id",
        time_column="year",
        treatment_column="treated",
        cohort_column="treatment_year",
        outcome_column="emissions",
        reference_period=-1,
        input_root=input_root,
        output_root=output_root,
        budget=budget(),
    )
