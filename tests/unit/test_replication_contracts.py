"""Tests for immutable Tier-2 replication intake contracts."""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark import ExpectedOutput
from envresearch.replication.contracts import (
    AcquiredPackageInventory,
    ApprovedTier2Intake,
    ContainerRuntimeProfile,
    DeclaredInput,
    ExternalAdmission,
    InventoryFile,
    ReplicationRunState,
    Tier2IntakeProposal,
)

SHA256 = "a" * 64
UNSAFE_ARCHIVE_PATHS = (
    Path("../outside.csv"),
    Path("/tmp/outside.csv"),
    Path(r"C:\\outside\\data.csv"),
    Path(r"\\\\server\\share\\data.csv"),
)
UNSAFE_SERIALIZED_PATHS = (
    "../outside.csv",
    "/tmp/outside.csv",
    r"C:\outside\data.csv",
    r"\\server\share\data.csv",
    "./data.csv",
    "data//analysis.csv",
    "data/./analysis.csv",
    "data/analysis.csv/",
)


def valid_proposal() -> dict[str, object]:
    """Return independently specified valid Tier-2 proposal data."""
    return {
        "schema_version": "tier2-intake-v1",
        "package_id": "card-2020-replication",
        "canonical_url": "https://example.org/packages/card-2020.tar.gz",
        "declared_version": "1.0.0",
        "doi": "10.1000/example.1",
        "license_name": "CC-BY-4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "declared_inputs": (
            {
                "path": Path("data/analysis.csv"),
                "purpose": "author-data",
                "required": True,
            },
        ),
        "expected_outputs": (
            {
                "path": "output/table-1.csv",
                "comparator": "csv_numeric",
                "expected_path": "expected/table-1.csv",
            },
        ),
        "runtime": {
            "profile_id": "r-did-v1",
            "image_digest": "ghcr.io/envresearch/r-did@sha256:" + SHA256,
            "nonroot_uid_gid": "10001:10001",
        },
        "budget": {
            "max_download_bytes": 1_000_000,
            "max_storage_bytes": 2_000_000,
            "max_memory_bytes": 512_000_000,
            "inactivity_seconds": 300,
        },
        "self_contained": True,
    }


def valid_approved_intake() -> dict[str, object]:
    """Return independently specified post-decision intake data."""
    return {
        "proposal_ref": ArtifactRef(
            artifact_id="proposal", artifact_version=1, content_hash=SHA256
        ),
        "approval": {
            "approver_id": "researcher-17",
            "rationale": "The public package has a compatible license.",
            "approved_locator": "https://example.org/packages/card-2020.tar.gz",
        },
        "approved_at": datetime(2026, 8, 10, 9, 0, tzinfo=UTC),
    }


def test_tier2_proposal_rejects_undeclared_secondary_input() -> None:
    """An empty declaration would permit unreviewed package inputs."""
    with pytest.raises(ValidationError, match="declared_inputs"):
        Tier2IntakeProposal.model_validate({**valid_proposal(), "declared_inputs": []})


def test_tier2_proposal_rejects_missing_expected_outputs() -> None:
    """A replay proposal must say which author outputs are checked."""
    with pytest.raises(ValidationError, match="expected_outputs"):
        Tier2IntakeProposal.model_validate({**valid_proposal(), "expected_outputs": []})


@pytest.mark.parametrize("path", UNSAFE_ARCHIVE_PATHS)
def test_declared_input_rejects_cross_platform_unconfined_paths(path: Path) -> None:
    """An input path must be canonical POSIX relative archive syntax."""
    with pytest.raises(ValidationError, match="safe relative path"):
        DeclaredInput(path=path, purpose="author-data", required=True)


@pytest.mark.parametrize("path", UNSAFE_SERIALIZED_PATHS)
def test_declared_input_rejects_noncanonical_serialized_paths(path: str) -> None:
    """Serialized input paths must have one canonical POSIX representation."""
    with pytest.raises(ValidationError, match="safe relative path"):
        DeclaredInput.model_validate_json(
            json.dumps({"path": path, "purpose": "author-data", "required": True})
        )


@pytest.mark.parametrize("field_name", ["path", "expected_path"])
@pytest.mark.parametrize("path", UNSAFE_ARCHIVE_PATHS)
def test_tier2_proposal_rejects_cross_platform_unconfined_output_paths(
    field_name: str, path: Path
) -> None:
    """Both output paths must remain canonical POSIX archive-relative paths."""
    payload = valid_proposal()
    expected_output = dict(
        cast(tuple[dict[str, object], ...], payload["expected_outputs"])[0]
    )
    expected_output[field_name] = str(path)
    payload["expected_outputs"] = (expected_output,)

    with pytest.raises(ValidationError, match="safe relative path"):
        Tier2IntakeProposal.model_validate(payload)


@pytest.mark.parametrize("field_name", ["path", "expected_path"])
@pytest.mark.parametrize("path", UNSAFE_SERIALIZED_PATHS)
def test_tier2_proposal_rejects_noncanonical_serialized_output_paths(
    field_name: str, path: str
) -> None:
    """Serialized expected outputs cannot hide noncanonical path syntax."""
    payload = valid_proposal()
    expected_output = dict(
        cast(tuple[dict[str, object], ...], payload["expected_outputs"])[0]
    )
    expected_output[field_name] = path
    payload["expected_outputs"] = (expected_output,)

    with pytest.raises(ValidationError, match="safe relative path"):
        Tier2IntakeProposal.model_validate_json(json.dumps(payload, default=str))


@pytest.mark.parametrize("field_name", ["path", "expected_path"])
@pytest.mark.parametrize(
    "path", [Path("data//analysis.csv"), Path("data/./analysis.csv"), Path("data/")]
)
def test_tier2_proposal_rejects_prebuilt_expected_output_without_raw_path_syntax(
    field_name: str, path: Path
) -> None:
    """A normalized legacy output object cannot prove canonical path spelling."""
    output = ExpectedOutput(
        path=path if field_name == "path" else Path("output/analysis.csv"),
        comparator="csv_numeric",
        expected_path=(
            path if field_name == "expected_path" else Path("expected/analysis.csv")
        ),
    )
    payload = valid_proposal()
    payload["expected_outputs"] = (output,)

    with pytest.raises(ValidationError, match="raw Tier-2 output declarations"):
        Tier2IntakeProposal.model_validate(payload)


def test_tier2_proposal_rejects_duplicate_declared_input_paths() -> None:
    """Duplicate input paths would make archive admission ambiguous."""
    payload = valid_proposal()
    payload["declared_inputs"] = (
        {"path": Path("data/analysis.csv"), "purpose": "author-data", "required": True},
        {"path": Path("data/analysis.csv"), "purpose": "author-code", "required": True},
    )

    with pytest.raises(ValidationError, match="unique"):
        Tier2IntakeProposal.model_validate(payload)


@pytest.mark.parametrize("field", ["package_id", "declared_version", "license_name"])
def test_tier2_proposal_rejects_blank_required_metadata(field: str) -> None:
    """Blank provenance metadata cannot support a reviewable admission."""
    with pytest.raises(ValidationError, match="nonblank"):
        Tier2IntakeProposal.model_validate({**valid_proposal(), field: " "})


def test_tier2_proposal_rejects_unpinned_runtime_image() -> None:
    """A mutable image tag would make the replay environment non-reproducible."""
    payload = valid_proposal()
    runtime = dict(cast(dict[str, object], payload["runtime"]))
    runtime["image_digest"] = "ghcr.io/envresearch/r-did:latest"
    payload["runtime"] = runtime

    with pytest.raises(ValidationError, match="@sha256:"):
        Tier2IntakeProposal.model_validate(payload)


def test_tier2_proposal_rejects_false_self_contained_flag() -> None:
    """Tier-2 intake must never silently permit unspecified external inputs."""
    with pytest.raises(ValidationError):
        Tier2IntakeProposal.model_validate(
            {**valid_proposal(), "self_contained": False}
        )


def test_approved_intake_requires_a_nonblank_human_decision_identity() -> None:
    """An anonymous approval cannot authorize external acquisition."""
    payload = valid_approved_intake()
    approval = dict(cast(dict[str, object], payload["approval"]))
    approval["approver_id"] = " "
    payload["approval"] = approval

    with pytest.raises(ValidationError, match="nonblank"):
        ApprovedTier2Intake.model_validate(payload)


def test_approved_intake_rejects_non_utc_decision_time() -> None:
    """Decision chronology must not depend on a local timezone."""
    with pytest.raises(ValidationError, match="UTC"):
        ApprovedTier2Intake.model_validate(
            {
                **valid_approved_intake(),
                "approved_at": datetime(
                    2026, 8, 10, 9, 0, tzinfo=timezone(timedelta(hours=8))
                ),
            }
        )


def test_approved_intake_cannot_claim_an_observed_archive_hash() -> None:
    """Archive identity may exist only after the approved locator is acquired."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ApprovedTier2Intake.model_validate(
            {**valid_approved_intake(), "archive_sha256": SHA256}
        )


@pytest.mark.parametrize("digest", ["A" * 64, "a" * 63])
def test_acquired_inventory_rejects_noncanonical_archive_hash(digest: str) -> None:
    """A post-acquisition archive digest must be a lowercase SHA-256 value."""
    with pytest.raises(ValidationError, match="lowercase SHA-256"):
        AcquiredPackageInventory(
            approved_intake_ref=ArtifactRef(
                artifact_id="approved", artifact_version=1, content_hash=SHA256
            ),
            archive_sha256=digest,
            archive_bytes=10,
            files=(),
        )


def test_acquired_inventory_rejects_duplicate_or_unconfined_file_paths() -> None:
    """Inventory paths must uniquely identify files inside the acquired archive."""
    with pytest.raises(ValidationError, match="unique"):
        AcquiredPackageInventory(
            approved_intake_ref=ArtifactRef(
                artifact_id="approved", artifact_version=1, content_hash=SHA256
            ),
            archive_sha256=SHA256,
            archive_bytes=10,
            files=(
                InventoryFile(path=Path("code/run.R"), bytes=1, sha256=SHA256),
                InventoryFile(path=Path("code/run.R"), bytes=1, sha256=SHA256),
            ),
        )


@pytest.mark.parametrize("path", UNSAFE_ARCHIVE_PATHS)
def test_inventory_file_rejects_cross_platform_unconfined_paths(path: Path) -> None:
    """Inventory entries must not escape through platform-specific syntax."""
    with pytest.raises(ValidationError, match="safe relative path"):
        InventoryFile(path=path, bytes=1, sha256=SHA256)


@pytest.mark.parametrize("path", UNSAFE_SERIALIZED_PATHS)
def test_inventory_file_rejects_noncanonical_serialized_paths(path: str) -> None:
    """Serialized inventories cannot contain normalized-away escape syntax."""
    with pytest.raises(ValidationError, match="safe relative path"):
        InventoryFile.model_validate_json(
            json.dumps({"path": path, "bytes": 1, "sha256": SHA256})
        )


def test_replication_run_states_are_explicit_and_closed() -> None:
    """Controllers may transition only among the durable pilot states."""
    assert {state.value for state in ReplicationRunState} == {
        "pending",
        "running",
        "paused",
        "passed",
        "exception",
    }


def test_runtime_profile_is_immutable() -> None:
    """The approved execution environment cannot be mutated after review."""
    profile = ContainerRuntimeProfile(
        profile_id="r-did-v1",
        image_digest="ghcr.io/envresearch/r-did@sha256:" + SHA256,
        nonroot_uid_gid="10001:10001",
    )

    with pytest.raises(ValidationError, match="frozen"):
        profile.image_digest = "ghcr.io/envresearch/r-did@sha256:" + "b" * 64


def test_external_admission_is_immutable() -> None:
    """The human approval record cannot be modified after it is created."""
    admission = ExternalAdmission.model_validate(
        {
            "approver_id": "researcher-17",
            "rationale": "The public package has a compatible license.",
            "approved_locator": "https://example.org/packages/card-2020.tar.gz",
        }
    )

    with pytest.raises(ValidationError, match="frozen"):
        admission.approver_id = "researcher-18"
