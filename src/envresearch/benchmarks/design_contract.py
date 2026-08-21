"""Strict repository-owned scenario contract for one design replay."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator


class DesignReplayScenario(StrEnum):
    """Supported offline paths through the public research workflow."""

    HAPPY_PATH = "happy_path"
    CONNECTOR_DEGRADATION = "connector_degradation"
    CONDITIONAL_DATA_APPROVAL = "conditional_data_approval"
    BLOCKING_REVIEW_REVISION = "blocking_review_revision"
    INTERRUPTED_RECOVERY = "interrupted_recovery"


class DesignReplaySpec(BaseModel):
    """Versioned behavior input consumed by the actual replay driver."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: str
    scenario: DesignReplayScenario

    @field_validator("schema_version")
    @classmethod
    def require_supported_schema(cls, value: str) -> str:
        if value != "1.0":
            raise ValueError("unsupported design replay schema version")
        return value

    @field_validator("scenario", mode="before")
    @classmethod
    def parse_scenario(cls, value: object) -> object:
        if isinstance(value, str):
            return DesignReplayScenario(value)
        return value
