"""Secure discovery and deterministic filtering of methodology profiles."""

from __future__ import annotations

from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError
from yaml import YAMLError
from yaml.constructor import ConstructorError  # type: ignore[import-untyped]
from yaml.nodes import MappingNode  # type: ignore[import-untyped]
from yaml.resolver import BaseResolver  # type: ignore[import-untyped]

from envresearch.methods.models import MethodProfile
from envresearch.packs.manifest import PackManifest
from envresearch.packs.registry import PackRegistry


class _UniqueKeySafeLoader(yaml.SafeLoader):  # type: ignore[misc]
    """Safe YAML loader that rejects duplicate keys at every mapping depth."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Hashable, Any]:
    """Construct one mapping only when every effective key is unique."""
    loader.flatten_mapping(node)
    mapping: dict[Hashable, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, Hashable):
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            )
        if key in mapping:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class MethodProfileRegistry:
    """An immutable ID index of validated planning-only method profiles."""

    profiles: Mapping[str, MethodProfile]

    @classmethod
    def discover(cls, root: Path) -> MethodProfileRegistry:
        """Load method manifests through PackRegistry, then validate their profiles."""
        if not isinstance(root, Path):
            raise TypeError("root must be a pathlib.Path")
        if root.is_symlink():
            raise ValueError(f"method pack root must not be a symlink: {root}")

        pack_registry = PackRegistry.discover(root)
        try:
            resolved_root = root.resolve(strict=True)
        except OSError as error:
            raise ValueError(f"invalid method pack root {root}: {error}") from error
        if not resolved_root.is_dir():
            raise ValueError(f"method pack root must be a directory: {root}")

        manifest_paths = sorted(root.rglob("pack.yaml"))
        manifests = tuple(pack_registry.manifests.values())
        if len(manifest_paths) != len(manifests):
            raise ValueError("method manifest discovery changed during validation")

        profiles: dict[str, MethodProfile] = {}
        for manifest_path, manifest in zip(manifest_paths, manifests, strict=True):
            cls._validate_manifest_path(resolved_root, manifest_path)
            cls._read_unique_yaml_mapping(manifest_path, "method manifest")
            profile = cls._load_profile(resolved_root, manifest_path, manifest)
            if profile.profile_id in profiles:
                raise ValueError(f"duplicate method profile id: {profile.profile_id}")
            profiles[profile.profile_id] = profile

        known_ids = frozenset(profiles)
        for profile in profiles.values():
            unknown = tuple(
                fallback
                for fallback in profile.fallback_profiles
                if fallback not in known_ids
            )
            if unknown:
                raise ValueError(
                    f"profile {profile.profile_id} has unknown fallback profiles: "
                    + ", ".join(unknown)
                )
        return cls(profiles=MappingProxyType(profiles))

    @classmethod
    def _load_profile(
        cls,
        resolved_root: Path,
        manifest_path: Path,
        manifest: PackManifest,
    ) -> MethodProfile:
        """Resolve one safe YAML entrypoint and enforce manifest consistency."""
        pack_dir = manifest_path.parent
        if manifest.kind != "method":
            raise ValueError(f"method registry requires kind=method: {manifest.id}")
        if manifest.id != pack_dir.name:
            raise ValueError(
                f"pack id {manifest.id} must match directory {pack_dir.name}"
            )
        if manifest.entrypoint != "profile.yaml":
            raise ValueError(
                f"invalid method profile entrypoint for {manifest.id}: "
                f"{manifest.entrypoint}"
            )

        entrypoint = pack_dir / manifest.entrypoint
        cls._validate_entrypoint(resolved_root, pack_dir, entrypoint)
        payload = cls._read_unique_yaml_mapping(entrypoint, "method profile")
        try:
            profile = MethodProfile.model_validate(payload)
        except ValidationError as error:
            raise ValueError(f"invalid method profile {entrypoint}: {error}") from error
        if profile.profile_id != manifest.id:
            raise ValueError(
                f"profile_id {profile.profile_id} must match manifest id {manifest.id}"
            )
        if profile.version != manifest.version:
            raise ValueError(
                f"profile version {profile.version} must match manifest version "
                f"{manifest.version}"
            )
        return profile

    @staticmethod
    def _read_unique_yaml_mapping(path: Path, label: str) -> Mapping[Any, Any]:
        """Load safe YAML with path-qualified duplicate and syntax diagnostics."""
        try:
            payload = yaml.load(
                path.read_text(encoding="utf-8"),
                Loader=_UniqueKeySafeLoader,
            )
        except (OSError, YAMLError) as error:
            raise ValueError(f"invalid {label} {path}: {error}") from error
        if not isinstance(payload, dict):
            raise TypeError(f"invalid {label} {path}: expected a YAML mapping")
        return payload

    @staticmethod
    def _validate_manifest_path(resolved_root: Path, manifest_path: Path) -> None:
        """Reject manifest symlinks and paths outside the requested root."""
        if manifest_path.is_symlink() or manifest_path.parent.is_symlink():
            raise ValueError(f"method manifest must not use a symlink: {manifest_path}")
        try:
            resolved_manifest = manifest_path.resolve(strict=True)
        except OSError as error:
            raise ValueError(f"invalid method manifest {manifest_path}: {error}") from error
        if not resolved_manifest.is_relative_to(resolved_root):
            raise ValueError(f"method manifest escapes discovery root: {manifest_path}")

    @staticmethod
    def _validate_entrypoint(
        resolved_root: Path,
        pack_dir: Path,
        entrypoint: Path,
    ) -> None:
        """Require a regular in-pack file, never a symlink or escaped target."""
        if entrypoint.is_symlink():
            raise ValueError(f"method profile entrypoint must not be a symlink: {entrypoint}")
        try:
            resolved_entrypoint = entrypoint.resolve(strict=True)
            resolved_pack_dir = pack_dir.resolve(strict=True)
        except OSError as error:
            raise ValueError(f"invalid method profile entrypoint {entrypoint}: {error}") from error
        if (
            not resolved_entrypoint.is_file()
            or not resolved_entrypoint.is_relative_to(resolved_pack_dir)
            or not resolved_entrypoint.is_relative_to(resolved_root)
        ):
            raise ValueError(f"method profile entrypoint escapes its pack: {entrypoint}")

    def compatible(
        self,
        estimand_type: str,
        data_structure: str,
        features: frozenset[str],
    ) -> tuple[MethodProfile, ...]:
        """Return compatible profiles in deterministic discovery/profile-ID order."""
        MethodProfile._validate_compatibility_inputs(
            estimand_type=estimand_type,
            data_structure=data_structure,
            features=features,
        )
        return tuple(
            profile
            for profile in self.profiles.values()
            if profile.is_compatible(estimand_type, data_structure, features)
        )
