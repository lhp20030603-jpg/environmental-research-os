"""Frozen authority records for reviewed locally installed R package trees."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from envresearch.econometrics.method_authority import PackageRequirement
from envresearch.kernel.task_identity import payload_hash
from envresearch.models.artifact import ArtifactRef

STRICT = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9.]*$")
VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]*$")


class InstalledPackageAuthority(BaseModel):
    """Exact installed-tree authority imported into one frozen local pack."""

    model_config = STRICT

    schema_version: Literal["econometrics.frozen-r-package.v1"]
    authority_kind: Literal["frozen-local-tree"]
    package: str
    version: str
    observed_license: str
    description_sha256: str
    installed_tree_sha256: str
    package_relative_path: Path
    dependencies: tuple[PackageRequirement, ...] = ()
    r_version: str
    pack_hash: str
    observed_at: datetime

    @field_validator("package")
    @classmethod
    def package_name(cls, value: str) -> str:
        if not NAME.fullmatch(value):
            raise ValueError("installed package name is not canonical")
        return value

    @field_validator("version", "r_version")
    @classmethod
    def package_version(cls, value: str) -> str:
        if not VERSION.fullmatch(value):
            raise ValueError("installed package version is not canonical")
        return value

    @field_validator("description_sha256", "installed_tree_sha256", "pack_hash")
    @classmethod
    def digest(cls, value: str) -> str:
        if not SHA256.fullmatch(value):
            raise ValueError("installed package digest is invalid")
        return value

    @field_validator("observed_license")
    @classmethod
    def license_name(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("installed package license is not canonical")
        return value

    @field_validator("observed_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
            raise ValueError("installed package observation must be UTC-aware")
        return value

    @model_validator(mode="after")
    def bound_path(self) -> InstalledPackageAuthority:
        expected = Path("authorities/frozen-r-pack/library") / self.package
        if self.package_relative_path != expected:
            raise ValueError("installed package path is not identity-bound")
        return self

    def content_hash(self) -> str:
        """Return the canonical record digest."""
        return payload_hash(self.model_dump(mode="json"))

    def ref(self) -> ArtifactRef:
        """Return the package-level immutable reference used by results."""
        return ArtifactRef(
            artifact_id=f"r-package-authority-{self.package}-{self.version}",
            artifact_version=1,
            content_hash=self.content_hash(),
        )


def observed_now() -> datetime:
    """Return one UTC authority observation time."""
    return datetime.now(UTC)
