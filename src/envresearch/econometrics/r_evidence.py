"""Immutable evidence models for trusted local R execution."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from envresearch.econometrics.installed_package_authority import (
    InstalledPackageAuthority,
)
from envresearch.econometrics.method_authority import MethodAuthority

STRICT_FROZEN = ConfigDict(
    extra="forbid", frozen=True, strict=True, validate_default=True
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PackageAuthority = MethodAuthority | InstalledPackageAuthority


class GeneratedRScript(BaseModel):
    """One repository-generated, hash-bound R script."""

    model_config = STRICT_FROZEN

    template_id: str
    path: Path
    sha256: str

    @field_validator("template_id")
    @classmethod
    def require_template_id(cls, value: str) -> str:
        """Require a canonical versioned template identifier."""
        if not value or value != value.strip():
            raise ValueError("template ID must be canonical and nonblank")
        return value

    @field_validator("sha256")
    @classmethod
    def require_script_digest(cls, value: str) -> str:
        """Require the exact generated-script SHA-256."""
        if not SHA256.fullmatch(value):
            raise ValueError("script SHA-256 is invalid")
        return value


class RCommandResult(BaseModel):
    """Bounded raw outcome returned by an injected no-shell executor."""

    model_config = STRICT_FROZEN

    return_code: int
    stdout: bytes
    stderr: bytes


class RRuntimeIdentity(BaseModel):
    """Reviewed local R executable identity."""

    model_config = STRICT_FROZEN

    source_executable: Path
    executable: Path
    sha256: str
    version: str
    device: int = Field(ge=0)
    inode: int = Field(gt=0)
    size_bytes: int = Field(gt=0)

    @field_validator("sha256")
    @classmethod
    def require_runtime_digest(cls, value: str) -> str:
        """Require a canonical executable digest."""
        if not SHA256.fullmatch(value):
            raise ValueError("runtime SHA-256 is invalid")
        return value


class EnvironmentEntry(BaseModel):
    """One explicitly emitted runtime environment entry."""

    model_config = STRICT_FROZEN

    name: str
    value: str


class RExecutionEvidence(BaseModel):
    """Authenticated bounded evidence for one generated-script execution."""

    model_config = STRICT_FROZEN

    runtime: RRuntimeIdentity
    script: GeneratedRScript
    argv: tuple[str, ...]
    environment: tuple[EnvironmentEntry, ...]
    return_code: int
    stdout_sha256: str
    stderr_sha256: str
    redacted_stdout: str
    redacted_stderr: str
    workspace_bytes: int = Field(ge=0)
    package_authorities: tuple[PackageAuthority, ...] = ()

    @field_validator("stdout_sha256", "stderr_sha256")
    @classmethod
    def require_output_digest(cls, value: str) -> str:
        """Require canonical output hashes."""
        if not SHA256.fullmatch(value):
            raise ValueError("output SHA-256 is invalid")
        return value
