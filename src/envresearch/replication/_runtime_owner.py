"""Authenticated identity of one active container client and workload."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ENGINE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_CONTAINER = re.compile(r"^envresearch-[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IMAGE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
_FROZEN = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class RuntimeLaunchIdentity(BaseModel):
    """Sealed unpredictable workload identity published before process spawn."""

    model_config = _FROZEN

    engine: str
    attempt_nonce: str
    container_name: str
    cidfile_path: str
    image_digest: str
    input_mount_sha256: str
    output_mount_sha256: str
    prepared_at: datetime

    @field_validator("engine")
    @classmethod
    def require_engine(cls, value: str) -> str:
        if not _ENGINE.fullmatch(value):
            raise ValueError("runtime launch engine identity is invalid")
        return value

    @field_validator("attempt_nonce", "input_mount_sha256", "output_mount_sha256")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("runtime launch digest identity is invalid")
        return value

    @field_validator("container_name")
    @classmethod
    def require_container_name(cls, value: str) -> str:
        if not _CONTAINER.fullmatch(value):
            raise ValueError("runtime launch container identity is invalid")
        return value

    @field_validator("image_digest")
    @classmethod
    def require_image_digest(cls, value: str) -> str:
        if not _IMAGE.fullmatch(value):
            raise ValueError("runtime launch image identity is invalid")
        return value

    @field_validator("prepared_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
            raise ValueError("runtime launch time must be UTC-aware")
        return value

    @model_validator(mode="after")
    def require_cidfile_binding(self) -> RuntimeLaunchIdentity:
        path = Path(self.cidfile_path)
        if (
            not path.is_absolute()
            or path.name != f"{self.attempt_nonce}.cid"
            or path.parent.name != ".runtime"
        ):
            raise ValueError("runtime launch cidfile identity is invalid")
        return self


class RuntimeOwnership(BaseModel):
    """Sealed process-group and container identity retained across a crash."""

    model_config = _FROZEN

    engine: str
    pid: int = Field(ge=1)
    pgid: int = Field(ge=1)
    process_birth_sha256: str
    attempt_nonce: str
    container_name: str
    container_id: str
    image_digest: str
    input_mount_sha256: str
    output_mount_sha256: str
    started_at: datetime

    def extends(self, launch: RuntimeLaunchIdentity) -> bool:
        """Return whether this active owner exactly extends a sealed launch."""
        return (
            self.engine == launch.engine
            and self.attempt_nonce == launch.attempt_nonce
            and self.container_name == launch.container_name
            and self.image_digest == launch.image_digest
            and self.input_mount_sha256 == launch.input_mount_sha256
            and self.output_mount_sha256 == launch.output_mount_sha256
        )

    @field_validator("engine")
    @classmethod
    def require_engine(cls, value: str) -> str:
        if not _ENGINE.fullmatch(value):
            raise ValueError("runtime owner engine identity is invalid")
        return value

    @field_validator("container_name")
    @classmethod
    def require_container_name(cls, value: str) -> str:
        if not _CONTAINER.fullmatch(value):
            raise ValueError("runtime owner container identity is invalid")
        return value

    @field_validator(
        "process_birth_sha256",
        "attempt_nonce",
        "container_id",
        "input_mount_sha256",
        "output_mount_sha256",
    )
    @classmethod
    def require_sha256(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("runtime owner digest identity is invalid")
        return value

    @field_validator("image_digest")
    @classmethod
    def require_image_digest(cls, value: str) -> str:
        if not _IMAGE.fullmatch(value):
            raise ValueError("runtime owner image identity is invalid")
        return value

    @field_validator("started_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
            raise ValueError("runtime owner start time must be UTC-aware")
        return value
