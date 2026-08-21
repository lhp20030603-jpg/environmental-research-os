"""Freeze a closed reviewed local R package projection below one run root."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path

from envresearch.econometrics._managed_r_validation import (
    BASE_R_PACKAGES,
    description,
    freeze_tree,
    sha256,
    tree_digest,
    tree_entries,
)
from envresearch.econometrics._store_files import StoreFiles
from envresearch.econometrics.installed_package_authority import (
    InstalledPackageAuthority,
    observed_now,
)
from envresearch.econometrics.method_authority import PackageRequirement


class FrozenRLibrary:
    """Copy and reauthenticate a closed set of preinstalled package trees."""

    def __init__(self, store_root: Path) -> None:
        if not store_root.is_absolute() or store_root.is_symlink():
            raise ValueError("frozen R store root must be absolute and non-symlink")
        self.store_root = store_root.resolve()
        self.generation = self.store_root / "authorities/frozen-r-pack"
        self.root = self.generation / "library"
        self.files = StoreFiles(self.store_root)

    def freeze(
        self,
        source_libraries: tuple[Path, ...],
        *,
        required_packages: tuple[str, ...],
        r_version: str,
    ) -> tuple[InstalledPackageAuthority, ...]:
        """Publish a closed package projection without running install hooks."""
        sources = _source_roots(source_libraries)
        selected, fields = _closed_packages(sources, required_packages, r_version)
        identities = tuple(
            (name, fields[name]["Version"], tree_digest(selected[name]))
            for name in sorted(selected)
        )
        pack_hash = _pack_hash(r_version, identities)
        if self.root.exists():
            authorities = self._load_records(tuple(sorted(selected)))
            if any(
                item.pack_hash != pack_hash or item.r_version != r_version
                for item in authorities
            ):
                raise ValueError(
                    "existing frozen R pack conflicts with requested input"
                )
            return self.verify(authorities)
        shadow = self.store_root / "authorities/.frozen-shadow" / uuid.uuid4().hex
        library = shadow / "library"
        library.mkdir(parents=True)
        try:
            for name, source in selected.items():
                tree_entries(source)
                shutil.copytree(source, library / name, symlinks=False)
                freeze_tree(library / name)
            authorities = tuple(
                self._authority(name, fields[name], pack_hash, r_version, library)
                for name in sorted(selected)
            )
            shadow_files = StoreFiles(shadow)
            for authority in authorities:
                shadow_files.persist_exact(
                    Path("records") / f"{authority.package}.json",
                    authority.model_dump_json().encode(),
                )
            self.generation.parent.mkdir(parents=True, exist_ok=True)
            os.replace(shadow, self.generation)
        finally:
            shutil.rmtree(shadow, ignore_errors=True)
        return self.verify(authorities)

    def verify(
        self, authorities: tuple[InstalledPackageAuthority, ...]
    ) -> tuple[InstalledPackageAuthority, ...]:
        """Reopen records, exact package trees, and the closed dependency graph."""
        if not authorities:
            raise ValueError("frozen R library authority is empty")
        expected = {item.package for item in authorities}
        if len(expected) != len(authorities):
            raise ValueError("frozen R package authorities are duplicated")
        entries = tuple(self.root.iterdir())
        if self.root.is_symlink() or any(
            path.is_symlink() or not path.is_dir() for path in entries
        ):
            raise ValueError("frozen R library projection contains an invalid entry")
        observed = {path.name for path in entries}
        if observed != expected:
            raise ValueError("frozen R library projection is not closed")
        runtime_versions = {item.r_version for item in authorities}
        pack_hashes = {item.pack_hash for item in authorities}
        if len(runtime_versions) != 1 or len(pack_hashes) != 1:
            raise ValueError("frozen R package records disagree on pack identity")
        identities = tuple(
            (
                item.package,
                item.version,
                item.installed_tree_sha256,
            )
            for item in sorted(authorities, key=lambda value: value.package)
        )
        if _pack_hash(next(iter(runtime_versions)), identities) not in pack_hashes:
            raise ValueError("frozen R package pack identity changed")
        for authority in authorities:
            record = InstalledPackageAuthority.model_validate_json(
                self.files.read(_record_path(authority.package)), strict=True
            )
            if record != authority:
                raise ValueError("frozen R package record changed")
            package = self.root / authority.package
            if tree_digest(package) != authority.installed_tree_sha256:
                raise ValueError("frozen R package tree identity changed")
            if sha256((package / "DESCRIPTION").read_bytes()) != (
                authority.description_sha256
            ):
                raise ValueError("frozen R package DESCRIPTION changed")
            fields = description(package / "DESCRIPTION")
            rebuilt = _requirements(fields, self.root, authority.r_version)
            if (
                fields.get("Package") != authority.package
                or fields.get("Version") != authority.version
                or fields.get("License") != authority.observed_license
                or rebuilt != authority.dependencies
            ):
                raise ValueError("frozen R package record semantics changed")
            for dependency in authority.dependencies:
                if not dependency.base and dependency.package not in expected:
                    raise ValueError("frozen R package dependency is missing")
        return authorities

    def load(self, expected_pack_hash: str) -> tuple[InstalledPackageAuthority, ...]:
        """Load one existing projection only under an external exact pack hash."""
        if re.fullmatch(r"[0-9a-f]{64}", expected_pack_hash) is None:
            raise ValueError("expected frozen R pack hash is invalid")
        if not self.root.is_dir() or self.root.is_symlink():
            raise ValueError("frozen R library projection is missing")
        packages = tuple(sorted(path.name for path in self.root.iterdir()))
        authorities = self._load_records(packages)
        if any(item.pack_hash != expected_pack_hash for item in authorities):
            raise ValueError("frozen R pack hash does not match reviewed authority")
        return self.verify(authorities)

    def _authority(
        self,
        name: str,
        fields: dict[str, str],
        pack_hash: str,
        r_version: str,
        library: Path,
    ) -> InstalledPackageAuthority:
        package = library / name
        return InstalledPackageAuthority(
            schema_version="econometrics.frozen-r-package.v1",
            authority_kind="frozen-local-tree",
            package=name,
            version=fields["Version"],
            observed_license=fields["License"],
            description_sha256=sha256((package / "DESCRIPTION").read_bytes()),
            installed_tree_sha256=tree_digest(package),
            package_relative_path=Path("authorities/frozen-r-pack/library") / name,
            dependencies=_requirements(fields, library, r_version),
            r_version=r_version,
            pack_hash=pack_hash,
            observed_at=observed_now(),
        )

    def _load_records(
        self, packages: tuple[str, ...]
    ) -> tuple[InstalledPackageAuthority, ...]:
        return tuple(
            InstalledPackageAuthority.model_validate_json(
                self.files.read(_record_path(name)), strict=True
            )
            for name in packages
        )


def _source_roots(values: tuple[Path, ...]) -> tuple[Path, ...]:
    if not values:
        raise ValueError("frozen R source libraries are required")
    roots: list[Path] = []
    for value in values:
        if not value.is_absolute() or value.is_symlink() or not value.is_dir():
            raise ValueError("frozen R source library is invalid")
        roots.append(value.resolve(strict=True))
    return tuple(roots)


def _closed_packages(
    roots: tuple[Path, ...], required: tuple[str, ...], r_version: str
) -> tuple[dict[str, Path], dict[str, dict[str, str]]]:
    selected: dict[str, Path] = {}
    fields: dict[str, dict[str, str]] = {}
    pending = list(required)
    while pending:
        name = pending.pop()
        if name in BASE_R_PACKAGES or name == "R" or name in selected:
            continue
        candidates = tuple(root / name for root in roots if (root / name).is_dir())
        if not candidates:
            raise ValueError(f"frozen R dependency is missing: {name}")
        identities = {
            (description(path / "DESCRIPTION").get("Version"), tree_digest(path))
            for path in candidates
        }
        if len(identities) != 1:
            raise ValueError(f"frozen R package is ambiguous: {name}")
        selected[name] = candidates[0]
        metadata = description(candidates[0] / "DESCRIPTION")
        if not all(metadata.get(key) for key in ("Package", "Version", "License")):
            raise ValueError(f"frozen R package metadata is incomplete: {name}")
        if metadata["Package"] != name:
            raise ValueError("frozen R package identity conflicts with its directory")
        fields[name] = metadata
        pending.extend(_dependency_names(metadata))
    return selected, fields


def _dependency_names(fields: dict[str, str]) -> tuple[str, ...]:
    names: list[str] = []
    for field in ("Depends", "Imports", "LinkingTo"):
        for item in fields.get(field, "").split(","):
            match = re.fullmatch(
                r"\s*([A-Za-z][A-Za-z0-9.]*)\s*(?:\([^)]*\))?\s*", item
            )
            if item.strip() and match is None:
                raise ValueError("frozen R dependency syntax is invalid")
            if match is not None:
                names.append(match.group(1))
    return tuple(names)


def _requirements(
    fields: dict[str, str], library: Path, r_version: str
) -> tuple[PackageRequirement, ...]:
    requirements: list[PackageRequirement] = []
    for name in sorted(set(_dependency_names(fields))):
        if name == "R" or name in BASE_R_PACKAGES:
            requirements.append(
                PackageRequirement(package=name, version=r_version, base=True)
            )
        else:
            version = description(library / name / "DESCRIPTION")["Version"]
            requirements.append(
                PackageRequirement(package=name, version=version, base=False)
            )
    return tuple(requirements)


def _record_path(package: str) -> Path:
    return Path("authorities/frozen-r-pack/records") / f"{package}.json"


def _pack_hash(r_version: str, identities: tuple[tuple[str, str, str], ...]) -> str:
    return hashlib.sha256(
        json.dumps((r_version, identities), separators=(",", ":")).encode()
    ).hexdigest()
