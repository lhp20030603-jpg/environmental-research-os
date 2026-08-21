"""Deterministic discovery for benchmark manifests."""

from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError
from yaml import YAMLError

from envresearch.models.benchmark import BenchmarkManifest


class BenchmarkRegistry:
    """Load benchmark manifests into a stable ID-indexed catalog."""

    @classmethod
    def discover(cls, root: Path) -> dict[str, BenchmarkManifest]:
        """Load flat catalog YAML and nested benchmark packages deterministically."""
        manifests: dict[str, BenchmarkManifest] = {}
        paths = set(root.glob("*.yaml")) | set(root.rglob("benchmark.yaml"))
        for path in sorted(paths):
            manifest = cls._read_manifest(path)
            if manifest.id in manifests:
                raise ValueError(f"duplicate benchmark id: {manifest.id}")
            manifests[manifest.id] = manifest
        return manifests

    @staticmethod
    def _read_manifest(path: Path) -> BenchmarkManifest:
        """Read one manifest and include its source path in parsing failures."""
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, YAMLError) as error:
            raise ValueError(f"invalid benchmark manifest {path}: {error}") from error
        if not isinstance(payload, dict):
            raise TypeError(
                f"invalid benchmark manifest {path}: expected a YAML mapping"
            )
        try:
            return BenchmarkManifest.model_validate(payload)
        except ValidationError as error:
            raise ValueError(f"invalid benchmark manifest {path}: {error}") from error
