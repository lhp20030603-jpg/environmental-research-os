"""Frozen authority records for external econometric method packages."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from envresearch.kernel.task_identity import payload_hash
from envresearch.models.artifact import ArtifactRef

STRICT_FROZEN = ConfigDict(
    extra="forbid", frozen=True, strict=True, validate_default=True
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9.]*$")
VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]*$")
SPDX_ID = (
    r"(?:MIT|Apache-2\.0|BSD-(?:2|3)-Clause|GPL-(?:2\.0|3\.0)-(?:only|or-later))"
)
SPDX = re.compile(rf"^{SPDX_ID}(?: OR {SPDX_ID})*$")


class PackageRequirement(BaseModel):
    """Exact package dependency, or one runtime-bound R base dependency."""

    model_config = STRICT_FROZEN

    package: str
    version: str
    base: bool = False

    @field_validator("package")
    @classmethod
    def require_package(cls, value: str) -> str:
        if not IDENTIFIER.fullmatch(value):
            raise ValueError("dependency package is not canonical")
        return value

    @field_validator("version")
    @classmethod
    def require_version(cls, value: str) -> str:
        if not VERSION.fullmatch(value):
            raise ValueError("dependency version is not canonical")
        return value


class MethodAuthorityProposal(BaseModel):
    """One immutable request to admit an official R package source release."""

    model_config = STRICT_FROZEN

    package: str
    version: str
    source_url: str
    source_sha256: str
    license: str
    description_license: str
    dependencies: tuple[PackageRequirement, ...] = ()

    @field_validator("package")
    @classmethod
    def require_package(cls, value: str) -> str:
        if not IDENTIFIER.fullmatch(value):
            raise ValueError("package name is not canonical")
        return value

    @field_validator("version")
    @classmethod
    def require_version(cls, value: str) -> str:
        if not VERSION.fullmatch(value):
            raise ValueError("package version is not canonical")
        return value

    @field_validator("source_url")
    @classmethod
    def require_https_source(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("package source must be one canonical HTTPS URL")
        return value

    @field_validator("source_sha256")
    @classmethod
    def require_source_digest(cls, value: str) -> str:
        if not SHA256.fullmatch(value):
            raise ValueError("source SHA-256 is invalid")
        return value

    @field_validator("license")
    @classmethod
    def require_license(cls, value: str) -> str:
        if not SPDX.fullmatch(value):
            raise ValueError("license must be a supported SPDX OR expression")
        alternatives = value.split(" OR ")
        if len(alternatives) != len(set(alternatives)):
            raise ValueError("license alternatives must be unique")
        return value

    @field_validator("description_license")
    @classmethod
    def require_description_license(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("DESCRIPTION license must be canonical and nonblank")
        return value

    @field_validator("dependencies")
    @classmethod
    def require_dependencies(
        cls, value: tuple[PackageRequirement, ...]
    ) -> tuple[PackageRequirement, ...]:
        identities = tuple((item.package, item.version, item.base) for item in value)
        if len(identities) != len(set(identities)):
            raise ValueError("dependencies must be unique canonical names")
        return value


class MethodAuthority(BaseModel):
    """Observed source and installed-tree identity for one admitted package."""

    model_config = STRICT_FROZEN

    proposal: MethodAuthorityProposal
    installed_tree_sha256: str
    source_relative_path: Path
    package_relative_path: Path
    description_sha256: str
    observed_license: str
    observed_at: datetime

    @field_validator("installed_tree_sha256", "description_sha256")
    @classmethod
    def require_digest(cls, value: str) -> str:
        if not SHA256.fullmatch(value):
            raise ValueError("authority SHA-256 is invalid")
        return value

    @field_validator("source_relative_path", "package_relative_path")
    @classmethod
    def require_relative_path(cls, value: Path) -> Path:
        if value.is_absolute() or any(part in {"", ".", ".."} for part in value.parts):
            raise ValueError("authority paths must be canonical and relative")
        return value

    @field_validator("observed_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
            raise ValueError("authority observation must be UTC-aware")
        return value

    @field_validator("observed_license")
    @classmethod
    def require_observed_license(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("observed license must be canonical and nonblank")
        return value

    @model_validator(mode="after")
    def require_bound_paths(self) -> MethodAuthority:
        source = self.source_relative_path.as_posix()
        package = self.package_relative_path.as_posix()
        if self.proposal.source_sha256 not in source:
            raise ValueError("source path is not bound to its digest")
        if package != f"authorities/r-library/{self.proposal.package}":
            raise ValueError("package path is not bound to its identity")
        if self.observed_license != self.proposal.description_license:
            raise ValueError("observed license is not bound to the proposal")
        return self

    def content_hash(self) -> str:
        """Return the canonical content digest for this authority record."""
        return payload_hash(self.model_dump(mode="json"))

    def ref(self) -> ArtifactRef:
        """Return the immutable reference consumed by analysis evidence."""
        return ArtifactRef(
            artifact_id=(
                f"r-package-authority-{self.proposal.package}-{self.proposal.version}"
            ),
            artifact_version=1,
            content_hash=self.content_hash(),
        )
