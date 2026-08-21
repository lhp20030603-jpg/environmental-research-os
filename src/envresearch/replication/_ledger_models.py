"""Private immutable models re-exported by the replication ledger."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from envresearch.models.artifact import ArtifactRef
from envresearch.replication._runtime_owner import (
    RuntimeLaunchIdentity,
    RuntimeOwnership,
)
from envresearch.replication.contracts import ReplicationException, ReplicationRunState

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FROZEN = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)
_SERIALIZED_FROZEN = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class VerificationPublicationError(ValueError):
    """Rejected promotion carrying the independently persisted report ref."""

    def __init__(self, message: str, report_ref: ArtifactRef) -> None:
        super().__init__(message)
        self.report_ref = report_ref


class ResourceObservation(BaseModel):
    """One bounded resource measurement recorded at a heartbeat."""

    model_config = _SERIALIZED_FROZEN

    elapsed_seconds: int = Field(ge=0)
    storage_bytes: int = Field(ge=0)
    memory_bytes: int = Field(ge=0)
    heartbeat_at: datetime

    @field_validator("heartbeat_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
            raise ValueError("heartbeat time must be UTC-aware")
        return value


class OutputResult(BaseModel):
    """Hash-bound comparison of one author-declared output."""

    model_config = _FROZEN

    path: str
    sha256: str
    comparator: Literal["exact", "json_numeric", "csv_numeric"]
    comparison_passed: bool
    raw_ref: ArtifactRef
    artifact_ref: ArtifactRef
    log_ref: ArtifactRef

    @field_validator("path")
    @classmethod
    def require_relative_path(cls, value: str) -> str:
        if (
            not value
            or value.startswith("/")
            or "\\" in value
            or any(part in {".", ".."} for part in value.split("/"))
        ):
            raise ValueError("output path must be a safe relative path")
        return value

    @field_validator("sha256")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("output hash must be a 64-character lowercase SHA-256")
        return value


class ReplicationRun(BaseModel):
    """Durable run state carried by every immutable ledger generation."""

    model_config = _SERIALIZED_FROZEN

    attempt_ref: ArtifactRef
    output_root: str
    approved_intake_ref: ArtifactRef
    acquired_inventory_ref: ArtifactRef
    runtime_ref: ArtifactRef
    declared_outputs: tuple[str, ...]
    max_growth_bytes: int = Field(ge=0)
    state: ReplicationRunState
    observations: tuple[ResourceObservation, ...] = ()
    observation_count: int = Field(default=0, ge=0)
    observation_chain_sha256: str = "0" * 64
    runtime_launch: RuntimeLaunchIdentity | None = None
    runtime_owner: RuntimeOwnership | None = None
    author_outputs: tuple[OutputResult, ...] = ()
    derived_ref: ArtifactRef | None = None
    derived_log_ref: ArtifactRef | None = None
    verification_ref: ArtifactRef | None = None
    exception: ReplicationException | None = None

    @field_validator("observation_chain_sha256")
    @classmethod
    def require_observation_chain(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("heartbeat observation chain is invalid")
        return value

    @property
    def verification_pending(self) -> bool:
        return (
            self.state is ReplicationRunState.RUNNING
            and self.verification_ref is None
            and bool(self.author_outputs)
            and self.derived_ref is not None
            and self.derived_log_ref is not None
        )

    @field_validator("exception", mode="before")
    @classmethod
    def restore_exception_refs(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        restored = dict(value)
        refs = restored.get("evidence_refs")
        if isinstance(refs, list):
            restored["evidence_refs"] = tuple(refs)
        return restored
