"""Validated contracts for deterministic benchmark replays."""

import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationError,
    ValidationInfo,
    field_validator,
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DECLARED_ENVIRONMENT_ALLOWLIST = frozenset(
    {"LANG", "LC_ALL", "TZ", "SOURCE_DATE_EPOCH", "PYTHONHASHSEED"}
)
_PUBLIC_METADATA_FIELDS = frozenset(
    {
        "source_version",
        "source_archive",
        "source_sha256",
        "doi",
        "license_name",
        "license_url",
    }
)


def _require_safe_relative_path(value: Path, field_name: str) -> Path:
    """Reject paths that can escape a case or run workspace."""
    if value.is_absolute() or ".." in value.parts:
        raise ValueError(f"{field_name} must be a safe relative path")
    return value


def validate_command_environment(environment: Mapping[str, str]) -> None:
    """Allow only deterministic or locale environment names without values."""
    for name in environment:
        if not _ENVIRONMENT_NAME.fullmatch(name):
            raise ValueError(f"environment variable name '{name}' is invalid")
        if name.upper() not in _DECLARED_ENVIRONMENT_ALLOWLIST:
            raise ValueError(f"environment variable '{name}' is not allowed")


class CommandSpec(BaseModel):
    """A non-shell command that executes within a benchmark case."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True
    )

    argv: list[str] = Field(min_length=1)
    cwd: Path = Path(".")
    timeout_seconds: int = Field(default=3600, ge=1, le=86400)
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("argv")
    @classmethod
    def require_nonempty_arguments(cls, value: list[str]) -> list[str]:
        """Reject blank command tokens before command execution."""
        if any(not argument.strip() for argument in value):
            raise ValueError("argv entries must not be empty")
        return value

    @field_validator("cwd")
    @classmethod
    def require_safe_cwd(cls, value: Path) -> Path:
        """Keep command working directories within the benchmark case."""
        return _require_safe_relative_path(value, "cwd")

    @field_validator("env")
    @classmethod
    def require_allowlisted_environment(cls, value: dict[str, str]) -> dict[str, str]:
        """Keep manifest-declared variables deterministic, local, and non-secret."""
        try:
            validate_command_environment(value)
        except ValueError as error:
            raise ValidationError.from_exception_data(
                cls.__name__,
                [
                    {
                        "type": "value_error",
                        "loc": (),
                        "input": None,
                        "ctx": {"error": error},
                    }
                ],
            ) from error
        return value


class ExpectedOutput(BaseModel):
    """One expected artifact and the comparison policy applied to it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    comparator: Literal["exact", "json_numeric", "csv_numeric"]
    expected_path: Path
    absolute_tolerance: float = Field(default=0.0, ge=0.0)
    relative_tolerance: float = Field(default=0.0, ge=0.0)

    @field_validator("absolute_tolerance", "relative_tolerance", mode="before")
    @classmethod
    def require_finite_tolerance(cls, value: object) -> object:
        """Reject values that can make every finite numeric difference pass."""
        try:
            numeric = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return value
        if not math.isfinite(numeric):
            raise ValueError("tolerance must be finite")
        return value

    @field_validator("path", "expected_path")
    @classmethod
    def require_safe_output_path(cls, value: Path) -> Path:
        """Keep both actual and expected paths inside their comparison roots."""
        return _require_safe_relative_path(value, "output path")


class BenchmarkManifest(BaseModel):
    """Provenance, execution, and output contracts for one benchmark."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    id: str
    title: str
    method_family: str
    topic: str
    public: bool
    source_url: HttpUrl
    source_version: str | None = None
    source_archive: Path | None = None
    source_sha256: str | None = None
    doi: str | None = None
    license_name: str | None = None
    license_url: HttpUrl | None = None
    commands: list[CommandSpec]
    expected_outputs: list[ExpectedOutput]

    @field_validator("id", "title", "method_family", "topic")
    @classmethod
    def require_nonempty_text(cls, value: str) -> str:
        """Ensure fields used to identify and classify a benchmark are present."""
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("source_archive")
    @classmethod
    def require_raw_source_archive(cls, value: Path | None) -> Path | None:
        """Constrain source archives to the immutable case raw directory."""
        if value is None:
            return value
        value = _require_safe_relative_path(value, "source_archive")
        if len(value.parts) < 2 or value.parts[0] != "raw":
            raise ValueError("source_archive must be located in the raw directory")
        return value

    @field_validator("source_sha256")
    @classmethod
    def require_canonical_sha256(cls, value: str | None) -> str | None:
        """Require a lowercase SHA-256 checksum whenever one is supplied."""
        if value is not None and not _SHA256_PATTERN.fullmatch(value):
            raise ValueError("must be a 64-character lowercase SHA-256")
        return value

    @field_validator(*_PUBLIC_METADATA_FIELDS)
    @classmethod
    def require_public_metadata(
        cls, value: object, info: ValidationInfo
    ) -> object:
        """Make public-source provenance failures addressable by field name."""
        if info.data.get("public") and _is_missing_metadata(value):
            raise ValueError("public benchmark requires DOI and license metadata")
        return value


def _is_missing_metadata(value: object) -> bool:
    """Treat absent and blank provenance metadata as missing."""
    return value is None or (isinstance(value, str) and not value.strip())
