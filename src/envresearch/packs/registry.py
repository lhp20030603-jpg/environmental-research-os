"""Deterministic discovery and compatibility resolution for capability packs."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import ValidationError
from yaml import YAMLError

from envresearch.packs.manifest import PackManifest


@dataclass(frozen=True)
class PackRegistry:
    """An ID-indexed collection of discovered pack manifests."""

    manifests: Mapping[str, PackManifest]

    @classmethod
    def discover(cls, root: Path) -> "PackRegistry":
        """Recursively read ``pack.yaml`` manifests below ``root`` in path order."""
        manifests: dict[str, PackManifest] = {}
        for path in sorted(root.rglob("pack.yaml")):
            manifest = cls._read_manifest(path)
            if manifest.id in manifests:
                raise ValueError(f"duplicate pack id: {manifest.id}")
            manifests[manifest.id] = manifest
        return cls(manifests=manifests)

    @staticmethod
    def _read_manifest(path: Path) -> PackManifest:
        """Load one YAML mapping and attach its path to user-facing validation errors."""
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, YAMLError) as error:
            raise ValueError(f"invalid pack manifest {path}: {error}") from error
        if not isinstance(payload, dict):
            raise TypeError(f"invalid pack manifest {path}: expected a YAML mapping")
        try:
            return PackManifest.model_validate(payload)
        except ValidationError as error:
            raise ValueError(f"invalid pack manifest {path}: {error}") from error

    def require_compatible(
        self,
        pack_id: str,
        kernel_version: str,
        schema_version: str,
    ) -> PackManifest:
        """Return a pack only when its kernel and schema ranges contain both versions."""
        manifest = self.manifests.get(pack_id)
        if manifest is None:
            raise ValueError(f"pack not found: {pack_id}")
        kernel = self._parse_version("kernel", kernel_version)
        schema = self._parse_version("schema", schema_version)
        if kernel not in SpecifierSet(manifest.kernel):
            raise ValueError(
                f"pack {pack_id} is incompatible with kernel {kernel_version}"
            )
        if schema not in SpecifierSet(manifest.schema_version):
            raise ValueError(
                f"pack {pack_id} is incompatible with schema {schema_version}"
            )
        return manifest

    @staticmethod
    def _parse_version(label: str, value: str) -> Version:
        """Normalize caller-provided versions before compatibility checks."""
        try:
            return Version(value)
        except InvalidVersion as error:
            raise ValueError(f"invalid {label} version: {value}") from error
