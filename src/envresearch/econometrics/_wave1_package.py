"""Shared package-configuration evidence for Wave-1 recipes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from envresearch.econometrics.contracts import STRICT_FROZEN
from envresearch.models.artifact import ArtifactRef

WaveMethod = Literal[
    "rct-itt", "synthetic-control", "environmental-measurement", "meta-analysis"
]


class WavePackageConfiguration(BaseModel):
    model_config = STRICT_FROZEN
    method_id: WaveMethod
    r_version: str
    confidence_level: float | None = Field(default=None, gt=0.0, lt=1.0)
    package_authorities: tuple[ArtifactRef, ...] = ()

    @field_validator("r_version")
    @classmethod
    def version(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("R version must be canonical")
        return value

    @field_validator("package_authorities")
    @classmethod
    def unique_authorities(
        cls, value: tuple[ArtifactRef, ...]
    ) -> tuple[ArtifactRef, ...]:
        identities = tuple(item.artifact_id for item in value)
        if len(identities) != len(set(identities)):
            raise ValueError("package authorities must be unique")
        return value

    @model_validator(mode="after")
    def require_external_method_authority(self) -> WavePackageConfiguration:
        if (
            self.method_id in {"rct-itt", "meta-analysis"}
            and self.confidence_level is None
        ):
            raise ValueError("inferential confidence level is required")
        if (
            self.method_id == "environmental-measurement"
            and self.confidence_level is not None
        ):
            raise ValueError("descriptive measurement has no confidence level")
        if (
            self.method_id != "environmental-measurement"
            and not self.package_authorities
        ):
            raise ValueError("external method package authority is required")
        required = {
            "rct-itt": "fixest",
            "synthetic-control": "synthdid",
            "meta-analysis": "metafor",
        }.get(self.method_id)
        if required is not None and not any(
            item.artifact_id.startswith(f"r-package-authority-{required}-")
            for item in self.package_authorities
        ):
            raise ValueError(f"{required} package authority is required")
        return self
