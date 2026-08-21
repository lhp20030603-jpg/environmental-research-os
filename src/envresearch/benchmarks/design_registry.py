"""Strict, execution-free registry for V0.2 research-design benchmarks."""

from __future__ import annotations

from pathlib import Path
from typing import Self

import yaml  # type: ignore[import-untyped]
from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)
from yaml import YAMLError

from envresearch.benchmarks.design_files import PinnedFixtureRoot
from envresearch.benchmarks.design_result import DesignFixtureReplay
from envresearch.benchmarks.design_scoring import (
    RESEARCH_QUALITY_DIMENSIONS,
    RESEARCH_QUALITY_RUBRIC_VERSION,
)
from envresearch.research.workflow import ResearchRunPhase

__all__ = [
    "DesignBenchmarkManifest",
    "DesignBenchmarkRegistry",
    "DesignFixtureReplay",
    "replay_design_fixture",
]


class DesignBenchmarkManifest(BaseModel):
    """One inert Tier 0/1 design benchmark and its auditable expectations."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str
    version: str
    tier: int
    source: str
    license: str
    input_fixture: Path
    replay_fixture: Path
    expected_phase: ResearchRunPhase
    expected_artifacts: tuple[Path, ...]
    rubric_version: str
    rubric_thresholds: dict[str, int]
    executes_replication_package: bool
    blind_manifest: Path | None = None
    blind_rubric_version: str | None = None

    @field_validator("id", "version", "source", "license")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("benchmark text fields must be canonical and nonblank")
        return value

    @field_validator("tier")
    @classmethod
    def require_supported_tier(cls, value: int) -> int:
        if value == 2:
            raise ValueError("Tier 2 is not allowed in v0.2")
        if value not in (0, 1):
            raise ValueError("only Tier 0 and Tier 1 are allowed in v0.2")
        return value

    @field_validator("input_fixture", "replay_fixture")
    @classmethod
    def require_relative_fixture(cls, value: Path, info: object) -> Path:
        return _relative_path(value, getattr(info, "field_name", "fixture"))

    @field_validator("blind_manifest")
    @classmethod
    def require_relative_blind_manifest(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return _relative_path(value, "blind_manifest")

    @field_validator("expected_artifacts")
    @classmethod
    def require_expected_artifacts(cls, value: tuple[Path, ...]) -> tuple[Path, ...]:
        normalized = tuple(_relative_path(path, "expected artifact") for path in value)
        if not normalized or len(set(normalized)) != len(normalized):
            raise ValueError("expected_artifacts must be nonempty and unique")
        return normalized

    @field_validator("rubric_thresholds")
    @classmethod
    def require_rubric_thresholds(cls, value: dict[str, int]) -> dict[str, int]:
        if set(value) != set(RESEARCH_QUALITY_DIMENSIONS) or len(value) != len(
            RESEARCH_QUALITY_DIMENSIONS
        ):
            raise ValueError(
                "rubric thresholds must contain exactly the six research-quality "
                "dimensions"
            )
        if any(score < 1 or score > 5 for score in value.values()):
            raise ValueError("rubric thresholds must use scores 1-5")
        return {
            dimension: value[dimension] for dimension in RESEARCH_QUALITY_DIMENSIONS
        }

    @field_validator("rubric_version")
    @classmethod
    def require_rubric_version(cls, value: str) -> str:
        if value != RESEARCH_QUALITY_RUBRIC_VERSION:
            raise ValueError("unsupported research-quality rubric version")
        return value

    @model_validator(mode="after")
    def enforce_v02_boundary(self) -> Self:
        if self.executes_replication_package:
            raise ValueError("replication package execution is not allowed in v0.2")
        if self.tier == 1 and not self.source.startswith(("https://", "http://")):
            raise ValueError("Tier 1 requires open-paper source metadata")
        if (self.blind_manifest is None) != (self.blind_rubric_version is None):
            raise ValueError(
                "blind_manifest and blind_rubric_version must be supplied together"
            )
        if (
            self.blind_rubric_version is not None
            and self.blind_rubric_version != "blind-method-v1"
        ):
            raise ValueError("unsupported blind-method rubric version")
        return self


class DesignBenchmarkRegistry:
    """Discover inert design benchmark manifests through a pinned local root."""

    @classmethod
    def discover(cls, root: Path) -> dict[str, DesignBenchmarkManifest]:
        with PinnedFixtureRoot(root) as pinned:
            return cls.discover_pinned(pinned)

    @classmethod
    def discover_pinned(
        cls, pinned: PinnedFixtureRoot
    ) -> dict[str, DesignBenchmarkManifest]:
        manifests: dict[str, DesignBenchmarkManifest] = {}
        for path in pinned.discover_manifests():
            manifest = cls._read(pinned, path)
            if manifest.id in manifests:
                raise ValueError(f"duplicate design benchmark id: {manifest.id}")
            manifests[manifest.id] = manifest
        return dict(sorted(manifests.items()))

    @classmethod
    def load_pinned(
        cls, pinned: PinnedFixtureRoot, path: Path
    ) -> DesignBenchmarkManifest:
        """Load one explicitly selected design manifest from a pinned fixture."""
        return cls._read(pinned, path)

    @staticmethod
    def _read(pinned: PinnedFixtureRoot, path: Path) -> DesignBenchmarkManifest:
        try:
            payload = yaml.safe_load(
                pinned.read(path, description="benchmark manifest")
            )
            normalized = _normalize_wire_payload(payload)
            return DesignBenchmarkManifest.model_validate(normalized)
        except (OSError, UnicodeError, YAMLError, ValidationError) as error:
            raise ValueError(f"invalid design benchmark {path}: {error}") from error


def replay_design_fixture(root: Path) -> DesignFixtureReplay:
    """Replay a repository-owned fixture using the actual orchestrator."""
    from envresearch.benchmarks.design_replay import replay_design_fixture as replay

    return replay(root)


def _normalize_wire_payload(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("benchmark manifest must contain one YAML mapping")
    tier = value.get("tier")
    if type(tier) is not int:
        raise ValueError("tier must be an exact integer wire value")
    if tier == 2:
        raise ValueError("Tier 2 is not allowed in v0.2")
    if tier not in (0, 1):
        raise ValueError("only Tier 0 and Tier 1 are allowed in v0.2")
    executes = value.get("executes_replication_package")
    if type(executes) is not bool:
        raise ValueError("replication package execution flag must be an exact boolean")
    if executes:
        raise ValueError("replication package execution is not allowed in v0.2")
    expected = value.get("expected_artifacts")
    rubric = value.get("rubric_thresholds")
    if not isinstance(expected, list) or any(
        type(item) is not str for item in expected
    ):
        raise ValueError("expected_artifacts must be a list of path strings")
    if not isinstance(rubric, dict) or any(
        type(key) is not str or type(score) is not int for key, score in rubric.items()
    ):
        raise ValueError("rubric_thresholds must use string keys and integer scores")
    normalized = dict(value)
    normalized["input_fixture"] = _wire_path(
        value.get("input_fixture"), "input_fixture"
    )
    normalized["replay_fixture"] = _wire_path(
        value.get("replay_fixture"), "replay_fixture"
    )
    normalized["expected_phase"] = ResearchRunPhase(
        _wire_string(value.get("expected_phase"), "expected_phase")
    )
    normalized["expected_artifacts"] = tuple(Path(item) for item in expected)
    blind_manifest = value.get("blind_manifest")
    blind_rubric_version = value.get("blind_rubric_version")
    if blind_manifest is not None:
        normalized["blind_manifest"] = _wire_path(blind_manifest, "blind_manifest")
    if blind_rubric_version is not None:
        normalized["blind_rubric_version"] = _wire_string(
            blind_rubric_version, "blind_rubric_version"
        )
    return normalized


def _wire_string(value: object, field: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field} must be an exact string wire value")
    return value


def _wire_path(value: object, field: str) -> Path:
    return Path(_wire_string(value, field))


def _relative_path(value: Path, field: str) -> Path:
    if value.is_absolute() or ".." in value.parts or value == Path("."):
        raise ValueError(f"{field} must be a confined relative path")
    return value
