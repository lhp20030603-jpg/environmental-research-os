"""Strict immutable contracts for two-phase Tier-2 replication intake."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark import ExpectedOutput

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UID_GID = re.compile(r"^[1-9][0-9]*:[1-9][0-9]*$")
_STRICT_FROZEN = ConfigDict(
    extra="forbid", frozen=True, strict=True, validate_default=True
)


def _require_nonblank(value: str, field_name: str) -> str:
    """Reject blank or whitespace-padded durable text values."""
    if not value.strip() or value != value.strip():
        raise ValueError(f"{field_name} must be a canonical nonblank string")
    return value


def _require_canonical_archive_path(value: object, field_name: str) -> object:
    """Reject noncanonical or platform-specific archive-relative path values."""
    if isinstance(value, Path):
        raw_path = value.as_posix()
    elif isinstance(value, str):
        raw_path = value
    else:
        return value

    canonical = PurePosixPath(raw_path)
    if (
        not raw_path
        or "\\" in raw_path
        or canonical.is_absolute()
        or re.match(r"^[A-Za-z]:", raw_path)
        or any(part in {".", ".."} for part in raw_path.split("/"))
        or raw_path != canonical.as_posix()
    ):
        raise ValueError(f"{field_name} must be a safe relative path")
    return value


def _require_safe_relative_path(value: Path, field_name: str) -> Path:
    """Keep package declarations and inventories inside their archive root."""
    _require_canonical_archive_path(value, field_name)
    return value


def _require_sha256(value: str, field_name: str) -> str:
    """Require a canonical lowercase SHA-256 digest."""
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a 64-character lowercase SHA-256")
    return value


def _require_utc(value: datetime, field_name: str) -> datetime:
    """Reject ambiguous approval and execution timestamps."""
    if value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
        raise ValueError(f"{field_name} must be UTC-aware")
    return value


class DeclaredInput(BaseModel):
    """One required author-provided file admitted from the package archive."""

    model_config = _STRICT_FROZEN

    path: Path
    purpose: Literal["author-data", "author-code", "author-output-target"]
    required: Literal[True]

    @field_validator("path", mode="before")
    @classmethod
    def require_canonical_path(cls, value: object) -> object:
        """Reject serialized paths that cannot be safely re-emitted as POSIX."""
        return _require_canonical_archive_path(value, "declared input path")

    @field_validator("path")
    @classmethod
    def require_safe_path(cls, value: Path) -> Path:
        """Require a confined archive-relative input path."""
        return _require_safe_relative_path(value, "declared input path")


class ReplicationBudget(BaseModel):
    """Hard resource limits declared before an approved acquisition runs."""

    model_config = _STRICT_FROZEN

    max_download_bytes: int = Field(gt=0)
    max_storage_bytes: int = Field(gt=0)
    max_memory_bytes: int = Field(gt=0)
    inactivity_seconds: int = Field(gt=0)


class ContainerRuntimeProfile(BaseModel):
    """Pinned, non-root container runtime required for a Tier-2 replay."""

    model_config = _STRICT_FROZEN

    profile_id: Literal["r-did-v1"]
    image_digest: str
    nonroot_uid_gid: str

    @field_validator("image_digest")
    @classmethod
    def require_pinned_image(cls, value: str) -> str:
        """Require a canonical immutable OCI image reference."""
        _require_nonblank(value, "image_digest")
        name, separator, digest = value.partition("@sha256:")
        if not separator or not name or "@sha256:" in digest:
            raise ValueError("image_digest must contain a single @sha256: reference")
        _require_sha256(digest, "image digest")
        return value

    @field_validator("nonroot_uid_gid")
    @classmethod
    def require_nonroot_uid_gid(cls, value: str) -> str:
        """Require an explicit non-root numeric UID:GID mapping."""
        if not _UID_GID.fullmatch(value) or value == "0:0":
            raise ValueError("nonroot_uid_gid must be a non-root numeric UID:GID")
        return value


class Tier2ExpectedOutput(BaseModel):
    """Raw lexical output declaration preserved for a Tier-2 package review."""

    model_config = _STRICT_FROZEN

    path: str
    comparator: Literal["exact", "json_numeric", "csv_numeric"]
    expected_path: str
    absolute_tolerance: float = Field(default=0.0, ge=0.0)
    relative_tolerance: float = Field(default=0.0, ge=0.0)

    @field_validator("path", "expected_path")
    @classmethod
    def require_canonical_path(cls, value: str) -> str:
        """Retain exactly one valid POSIX spelling for each output location."""
        _require_canonical_archive_path(value, "Tier-2 output path")
        return value

    @field_validator("absolute_tolerance", "relative_tolerance", mode="before")
    @classmethod
    def require_finite_tolerance(cls, value: object) -> object:
        """Reject values that could make every finite comparison pass."""
        try:
            numeric = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return value
        if not math.isfinite(numeric):
            raise ValueError("tolerance must be finite")
        return value


class Tier2IntakeProposal(BaseModel):
    """Pre-acquisition declaration subject to an independent human decision."""

    model_config = _STRICT_FROZEN

    schema_version: Literal["tier2-intake-v1"]
    package_id: str
    canonical_url: HttpUrl
    declared_version: str
    doi: str | None
    license_name: str
    license_url: HttpUrl
    declared_inputs: tuple[DeclaredInput, ...]
    expected_outputs: tuple[Tier2ExpectedOutput, ...]
    runtime: ContainerRuntimeProfile
    budget: ReplicationBudget
    self_contained: Literal[True]

    @field_validator("package_id", "declared_version", "license_name")
    @classmethod
    def require_nonblank_metadata(cls, value: str) -> str:
        """Keep key proposal metadata auditable and canonical."""
        return _require_nonblank(value, "proposal metadata")

    @field_validator("doi")
    @classmethod
    def require_nonblank_doi(cls, value: str | None) -> str | None:
        """Keep optional DOI values meaningful whenever supplied."""
        if value is not None:
            return _require_nonblank(value, "doi")
        return value

    @field_validator("declared_inputs", "expected_outputs")
    @classmethod
    def require_positive_declarations(
        cls,
        value: tuple[DeclaredInput, ...] | tuple[Tier2ExpectedOutput, ...],
    ) -> tuple[DeclaredInput, ...] | tuple[Tier2ExpectedOutput, ...]:
        """Require at least one declared input and output before approval."""
        if not value:
            raise ValueError("declared_inputs and expected_outputs must be nonempty")
        return value

    @field_validator("declared_inputs")
    @classmethod
    def require_unique_input_paths(
        cls, value: tuple[DeclaredInput, ...]
    ) -> tuple[DeclaredInput, ...]:
        """Reject ambiguous repeated package input paths."""
        paths = tuple(item.path.as_posix() for item in value)
        if len(set(paths)) != len(paths):
            raise ValueError("declared input paths must be unique")
        return value

    @field_validator("expected_outputs")
    @classmethod
    def require_unique_output_paths(
        cls, value: tuple[Tier2ExpectedOutput, ...]
    ) -> tuple[Tier2ExpectedOutput, ...]:
        """Reject ambiguous repeated output targets."""
        paths = tuple(item.path for item in value)
        if len(set(paths)) != len(paths):
            raise ValueError("expected output paths must be unique")
        return value

    @field_validator("expected_outputs", mode="before")
    @classmethod
    def require_canonical_serialized_output_paths(cls, value: object) -> object:
        """Validate output paths before Pydantic normalizes serialized values."""
        if not isinstance(value, (list, tuple)):
            return value
        for output in value:
            if isinstance(output, ExpectedOutput):
                raise ValueError(  # noqa: TRY004 - Pydantic must aggregate this validation error.
                    "expected_outputs must use raw Tier-2 output declarations"
                )
            elif isinstance(output, Mapping):
                _require_canonical_archive_path(
                    output.get("path"), "expected output path"
                )
                _require_canonical_archive_path(
                    output.get("expected_path"), "expected comparison path"
                )
        return value


class ExternalAdmission(BaseModel):
    """Immutable human authorization of the exact external acquisition URL."""

    model_config = _STRICT_FROZEN

    approver_id: str
    rationale: str
    approved_locator: HttpUrl

    @field_validator("approver_id", "rationale")
    @classmethod
    def require_nonblank_decision_text(cls, value: str) -> str:
        """Require attributable human approval evidence."""
        return _require_nonblank(value, "admission decision field")


class ApprovedTier2Intake(BaseModel):
    """The approved pre-acquisition decision, without observed archive facts."""

    model_config = _STRICT_FROZEN

    proposal_ref: ArtifactRef
    approval: ExternalAdmission
    approved_at: datetime

    @field_validator("approved_at")
    @classmethod
    def require_utc_approval_time(cls, value: datetime) -> datetime:
        """Keep the approval decision independently ordered and auditable."""
        return _require_utc(value, "approved_at")


class InventoryFile(BaseModel):
    """One regular file observed after the approved archive is acquired."""

    model_config = _STRICT_FROZEN

    path: Path
    bytes: int = Field(ge=0)
    sha256: str

    @field_validator("path", mode="before")
    @classmethod
    def require_canonical_path(cls, value: object) -> object:
        """Reject serialized paths that cannot be safely re-emitted as POSIX."""
        return _require_canonical_archive_path(value, "inventory file path")

    @field_validator("path")
    @classmethod
    def require_safe_path(cls, value: Path) -> Path:
        """Require inventory paths to remain confined to the archive root."""
        return _require_safe_relative_path(value, "inventory file path")

    @field_validator("sha256")
    @classmethod
    def require_file_hash(cls, value: str) -> str:
        """Record each file with a canonical content digest."""
        return _require_sha256(value, "file sha256")


class AcquiredPackageInventory(BaseModel):
    """Post-acquisition archive identity and its observed file inventory."""

    model_config = _STRICT_FROZEN

    approved_intake_ref: ArtifactRef
    archive_sha256: str
    archive_bytes: int = Field(ge=0)
    files: tuple[InventoryFile, ...]

    @field_validator("archive_sha256")
    @classmethod
    def require_archive_hash(cls, value: str) -> str:
        """Record the acquired archive with its canonical observed digest."""
        return _require_sha256(value, "archive sha256")

    @field_validator("files")
    @classmethod
    def require_unique_inventory_paths(
        cls, value: tuple[InventoryFile, ...]
    ) -> tuple[InventoryFile, ...]:
        """Reject duplicate archive members after path normalization."""
        paths = tuple(item.path.as_posix() for item in value)
        if len(set(paths)) != len(paths):
            raise ValueError("inventory file paths must be unique")
        return value


class ReplicationRunState(StrEnum):
    """Closed durable state machine for a Tier-2 replication run."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    PASSED = "passed"
    EXCEPTION = "exception"


class ReplicationException(BaseModel):
    """Typed evidence recorded when autonomous replication cannot continue."""

    model_config = _STRICT_FROZEN

    code: str
    message: str
    evidence_refs: tuple[ArtifactRef, ...] = ()

    @field_validator("code", "message")
    @classmethod
    def require_nonblank_exception_text(cls, value: str) -> str:
        """Keep exception records specific enough for a later human decision."""
        return _require_nonblank(value, "exception field")
