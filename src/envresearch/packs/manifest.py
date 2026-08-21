"""Pydantic contract for a versioned capability pack manifest."""

import re
from typing import Literal

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from pydantic import BaseModel, ConfigDict, Field, field_validator

_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


class PackManifest(BaseModel):
    """Stable metadata that declares one independently versioned capability."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    id: str
    kind: Literal["paper", "method", "domain", "connector", "verification"]
    version: str
    kernel: str
    schema_version: str = Field(alias="schema")
    entrypoint: str

    @field_validator("id", "entrypoint")
    @classmethod
    def require_nonempty_value(cls, value: str) -> str:
        """Reject fields that cannot identify or load a capability."""
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("version")
    @classmethod
    def require_semver(cls, value: str) -> str:
        """Require the full SemVer form for independently evolving packs."""
        if not _SEMVER_PATTERN.fullmatch(value):
            raise ValueError("must be a valid SemVer version")
        return value

    @field_validator("kernel", "schema_version")
    @classmethod
    def require_valid_specifier(cls, value: str) -> str:
        """Ensure compatibility declarations are valid packaging specifiers."""
        try:
            SpecifierSet(value)
        except InvalidSpecifier as error:
            raise ValueError("must be a valid version specifier") from error
        return value
