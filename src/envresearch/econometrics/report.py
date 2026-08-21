"""Immutable local-analysis reports and exact references."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from envresearch.econometrics.analysis_specs import AnalysisSpec
from envresearch.econometrics.data_snapshot import LocalDataSnapshot
from envresearch.econometrics.r_evidence import RExecutionEvidence
from envresearch.econometrics.recipes import AnalysisResult

STRICT_FROZEN = ConfigDict(
    extra="forbid", frozen=True, strict=True, validate_default=True
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class OutputEvidence(BaseModel):
    """One persisted estimator output bound by exact bytes."""

    model_config = STRICT_FROZEN

    name: str
    relative_path: Path
    sha256: str
    size_bytes: int = Field(ge=0)

    @field_validator("sha256")
    @classmethod
    def require_digest(cls, value: str) -> str:
        """Require one canonical output digest."""
        if not SHA256.fullmatch(value):
            raise ValueError("output digest is invalid")
        return value

    @field_validator("relative_path")
    @classmethod
    def require_relative_path(cls, value: Path) -> Path:
        """Keep evidence references inside the artifact-store namespace."""
        return _relative_path(value)


class LocalAnalysisReport(BaseModel):
    """Complete durable state for one local analysis attempt."""

    model_config = STRICT_FROZEN

    schema_version: Literal["econometrics.local-report.v1"]
    analysis_id: str
    generation: int = Field(gt=0)
    status: Literal["passed", "exception"]
    code: str | None
    spec: AnalysisSpec
    snapshot: LocalDataSnapshot | None
    script_path: Path | None
    script_sha256: str | None
    runtime_path: Path | None
    output_root: Path | None
    outputs: tuple[OutputEvidence, ...]
    logs: tuple[OutputEvidence, ...]
    execution: RExecutionEvidence | None
    result: AnalysisResult | None
    verification_findings: tuple[str, ...]

    @model_validator(mode="after")
    def require_coherent_paths(self) -> LocalAnalysisReport:
        """Bind all report paths to this analysis's owned evidence subtree."""
        prefix = Path("analyses") / self.analysis_id / "evidence"
        paths = tuple(item.relative_path for item in (*self.outputs, *self.logs))
        optional = tuple(
            path
            for path in (self.script_path, self.runtime_path, self.output_root)
            if path is not None
        )
        if any(not path.is_relative_to(prefix) for path in (*paths, *optional)):
            raise ValueError("analysis evidence path escapes its owned subtree")
        if len({item.name for item in self.outputs}) != len(self.outputs):
            raise ValueError("analysis output names must be unique")
        if self.status == "passed" and (
            self.snapshot is None or self.code is not None or self.verification_findings
        ):
            raise ValueError("passed analysis must have coherent green evidence")
        if self.status == "exception" and (
            not self.code or not self.code.strip() or not self.verification_findings
        ):
            raise ValueError("exception analysis requires a code and findings")
        return self


class LocalAnalysisReference(BaseModel):
    """Exact content reference to one immutable analysis report."""

    model_config = STRICT_FROZEN

    analysis_id: str
    generation: int = Field(gt=0)
    relative_path: Path
    sha256: str

    @field_validator("sha256")
    @classmethod
    def require_report_digest(cls, value: str) -> str:
        """Require one canonical report digest."""
        if not SHA256.fullmatch(value):
            raise ValueError("report digest is invalid")
        return value

    @model_validator(mode="after")
    def require_exact_history_path(self) -> LocalAnalysisReference:
        """Bind a reference to its canonical immutable history filename."""
        expected = (
            Path("analyses")
            / self.analysis_id
            / "history"
            / f"generation-{self.generation}-{self.sha256}.json"
        )
        if _relative_path(self.relative_path) != expected:
            raise ValueError("local analysis reference path is not canonical")
        return self


def _relative_path(value: Path) -> Path:
    """Reject absolute, empty, current, and parent-relative paths."""
    if (
        value.is_absolute()
        or not value.parts
        or any(part in {"", ".", ".."} for part in value.parts)
    ):
        raise ValueError("artifact paths must be canonical relative paths")
    return value
