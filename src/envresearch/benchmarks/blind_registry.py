"""Descriptor-pinned, execution-free registry for Tier-1 blind cases."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal, TypeVar

import yaml  # type: ignore[import-untyped]
from pydantic import (
    BaseModel,
    ConfigDict,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)
from yaml import YAMLError

from envresearch.benchmarks.design_files import PinnedFixtureRoot
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_blinding import BlindedBrief
from envresearch.models.benchmark_claims import (
    ClaimFactMap,
    ClaimVerificationStatus,
    CuratorSourceSheet,
)

BLIND_RUBRIC_VERSION = "blind-method-v1"
Model = TypeVar("Model", bound=BaseModel)

__all__ = [
    "BLIND_RUBRIC_VERSION",
    "BlindBenchmarkManifest",
    "BlindBenchmarkRegistry",
    "LoadedBlindCase",
]


class BlindBenchmarkManifest(BaseModel):
    """One inert Tier-1 case and the confined paths to its blind artifacts."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str
    version: str
    tier: Literal[1]
    method_family: str
    license: str
    source_sheet: Path
    claim_fact_map: Path
    blinded_brief: Path
    rubric_version: Literal["blind-method-v1"]
    executes_replication_package: Literal[False]

    _catalog_root: Path = PrivateAttr()
    _case_path: Path = PrivateAttr()

    @field_validator("id", "version", "method_family", "license")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        """Keep persisted descriptor metadata canonical and reviewable."""
        if not value.strip() or value != value.strip():
            raise ValueError(
                "blind benchmark text fields must be canonical and nonblank"
            )
        return value

    @field_validator("source_sheet", "claim_fact_map", "blinded_brief")
    @classmethod
    def require_relative_artifact_path(cls, value: Path) -> Path:
        """Forbid a descriptor from escaping the directory containing its manifest."""
        if value.is_absolute() or ".." in value.parts or value == Path("."):
            raise ValueError("blind case files must be confined relative paths")
        return value

    @model_validator(mode="after")
    def require_distinct_case_files(self) -> BlindBenchmarkManifest:
        """Prevent one opaque file from being reinterpreted as several artifacts."""
        paths = (self.source_sheet, self.claim_fact_map, self.blinded_brief)
        if len(paths) != len(set(paths)):
            raise ValueError("blind case files must have distinct paths")
        return self


class LoadedBlindCase(BaseModel):
    """Validated blind-case artifacts plus the references consumers must reuse."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_sheet: CuratorSourceSheet
    source_ref: ArtifactRef
    claim_fact_map: ClaimFactMap
    claim_fact_map_ref: ArtifactRef
    blinded_brief: BlindedBrief
    brief_ref: ArtifactRef


class BlindBenchmarkRegistry:
    """Discover and load inert cases without creating an executable replay path."""

    @classmethod
    def discover(cls, root: Path) -> dict[str, BlindBenchmarkManifest]:
        """Return uniquely identified manifests rooted at one pinned catalog."""
        with PinnedFixtureRoot(root) as pinned:
            manifests: dict[str, BlindBenchmarkManifest] = {}
            for manifest_path in pinned.discover_manifests():
                manifest, _ = cls.load_pinned_case(pinned, manifest_path)
                if manifest.id in manifests:
                    raise ValueError(f"duplicate blind benchmark id: {manifest.id}")
                manifests[manifest.id] = manifest
            return dict(sorted(manifests.items()))

    @classmethod
    def load_pinned_case(
        cls, pinned: PinnedFixtureRoot, manifest_path: Path
    ) -> tuple[BlindBenchmarkManifest, LoadedBlindCase]:
        """Load one explicitly referenced case from one pinned directory inode."""
        with pinned.pin_directory(manifest_path.parent) as case:
            manifest = cls._read_manifest(case, Path(manifest_path.name))
            return manifest, cls._load_pinned(case, manifest)

    @classmethod
    def load_case(cls, manifest: BlindBenchmarkManifest) -> LoadedBlindCase:
        """Load all referenced artifacts through a fresh pinned case directory."""
        try:
            case_root = manifest._catalog_root / manifest._case_path
        except AttributeError as error:
            raise ValueError(
                "blind benchmark manifest was not discovered by registry"
            ) from error
        with PinnedFixtureRoot(case_root) as pinned:
            return cls._load_pinned(pinned, manifest)

    @classmethod
    def _load_pinned(
        cls, pinned: PinnedFixtureRoot, manifest: BlindBenchmarkManifest
    ) -> LoadedBlindCase:
        source_bytes = pinned.read(
            manifest.source_sheet, description="curator source sheet"
        )
        brief_bytes = pinned.read(manifest.blinded_brief, description="blinded brief")
        map_bytes = pinned.read(manifest.claim_fact_map, description="claim fact map")
        source_ref = _content_ref("curator-source-sheet", source_bytes)
        brief_ref = _content_ref("blinded-brief", brief_bytes)
        map_ref = _content_ref("claim-fact-map", map_bytes)
        source_sheet = _parse_artifact(
            source_bytes, CuratorSourceSheet, "curator source sheet"
        )
        blinded_brief = _parse_artifact(brief_bytes, BlindedBrief, "blinded brief")
        claim_fact_map = _parse_artifact(map_bytes, ClaimFactMap, "claim fact map")
        cls._validate_case(
            manifest,
            source_sheet,
            source_ref,
            blinded_brief,
            brief_ref,
            claim_fact_map,
        )
        return LoadedBlindCase(
            source_sheet=source_sheet,
            source_ref=source_ref,
            claim_fact_map=claim_fact_map,
            claim_fact_map_ref=map_ref,
            blinded_brief=blinded_brief,
            brief_ref=brief_ref,
        )

    @staticmethod
    def _read_manifest(pinned: PinnedFixtureRoot, path: Path) -> BlindBenchmarkManifest:
        """Parse one manifest and retain only its confined, descriptor root."""
        try:
            payload = yaml.safe_load(pinned.read(path, description="blind manifest"))
            manifest = BlindBenchmarkManifest.model_validate(
                _normalize_manifest(payload)
            )
        except (OSError, UnicodeError, YAMLError, ValidationError, TypeError) as error:
            raise ValueError(f"invalid blind benchmark {path}: {error}") from error
        object.__setattr__(manifest, "_catalog_root", pinned.root)
        object.__setattr__(manifest, "_case_path", path.parent)
        return manifest

    @staticmethod
    def _validate_case(
        manifest: BlindBenchmarkManifest,
        source_sheet: CuratorSourceSheet,
        source_ref: ArtifactRef,
        blinded_brief: BlindedBrief,
        brief_ref: ArtifactRef,
        claim_fact_map: ClaimFactMap,
    ) -> None:
        """Require one exact case identity and provenance chain across all files."""
        if source_sheet.case_id != manifest.id:
            raise ValueError("source sheet case_id does not match blind manifest")
        if source_sheet.method_family != manifest.method_family:
            raise ValueError("source sheet method_family does not match blind manifest")
        if blinded_brief.case_id != manifest.id:
            raise ValueError("blinded brief case_id does not match blind manifest")
        if blinded_brief.source_sheet_ref != source_ref:
            raise ValueError(
                "blinded brief source_sheet_ref does not match loaded source"
            )
        if claim_fact_map.case_id != manifest.id:
            raise ValueError("claim fact map case_id does not match blind manifest")
        if claim_fact_map.source_sheet_ref != source_ref:
            raise ValueError(
                "claim fact map source_sheet_ref does not match loaded source"
            )
        if claim_fact_map.blinded_brief_ref != brief_ref:
            raise ValueError(
                "claim fact map blinded_brief_ref does not match loaded brief"
            )


def _normalize_manifest(value: object) -> dict[str, object]:
    """Reject coercible YAML scalars before strict Pydantic model validation."""
    if not isinstance(value, dict):
        raise TypeError("blind benchmark manifest must contain one YAML mapping")
    normalized = dict(value)
    if type(value.get("tier")) is not int or value.get("tier") != 1:
        raise ValueError("blind benchmarks require an exact Tier 1 wire value")
    if value.get("rubric_version") != BLIND_RUBRIC_VERSION:
        raise ValueError("unsupported blind-method rubric version")
    if value.get("executes_replication_package") is not False:
        raise ValueError("replication package execution is not allowed in v0.2")
    for field in ("id", "version", "method_family", "license"):
        if type(value.get(field)) is not str:
            raise ValueError(f"{field} must be an exact string wire value")
    for field in ("source_sheet", "claim_fact_map", "blinded_brief"):
        raw = value.get(field)
        if type(raw) is not str:
            raise ValueError(f"{field} must be an exact path string wire value")
        normalized[field] = Path(raw)
    return normalized


def _parse_artifact(content: bytes, model: type[Model], description: str) -> Model:
    """Parse one regular, pinned YAML artifact through its strict model."""
    try:
        payload = yaml.safe_load(content)
        if not isinstance(payload, dict):
            raise TypeError(f"{description} must contain one YAML mapping")
        normalized = _tuple_lists(payload)
        if model is CuratorSourceSheet:
            normalized = _source_sheet_wire_payload(normalized)
        return model.model_validate(normalized)
    except (UnicodeError, YAMLError, ValidationError, TypeError) as error:
        raise ValueError(f"invalid {description}: {error}") from error


def _content_ref(artifact_id: str, content: bytes) -> ArtifactRef:
    """Create the one canonical reference for the exact persisted file bytes."""
    return ArtifactRef(
        artifact_id=artifact_id,
        artifact_version=1,
        content_hash=hashlib.sha256(content).hexdigest(),
    )


def _tuple_lists(value: object) -> object:
    """Normalize YAML arrays for the immutable tuple-based persisted contracts."""
    if isinstance(value, list):
        return tuple(_tuple_lists(item) for item in value)
    if isinstance(value, dict):
        return {key: _tuple_lists(item) for key, item in value.items()}
    return value


def _source_sheet_wire_payload(value: object) -> object:
    """Convert the one persisted enum field without relaxing strict validation."""
    if not isinstance(value, dict):
        return value
    claims = value.get("claims")
    if not isinstance(claims, tuple):
        return value
    normalized_claims: list[object] = []
    for claim in claims:
        if not isinstance(claim, dict) or type(claim.get("status")) is not str:
            normalized_claims.append(claim)
            continue
        normalized_claim = dict(claim)
        normalized_claim["status"] = ClaimVerificationStatus(claim["status"])
        normalized_claims.append(normalized_claim)
    normalized = dict(value)
    normalized["claims"] = tuple(normalized_claims)
    return normalized
