"""Tests for discovering and resolving versioned capability packs."""

from json import dumps
from pathlib import Path

import pytest
from pydantic import ValidationError

from envresearch.packs.manifest import PackManifest
from envresearch.packs.registry import PackRegistry


def write_pack(path: Path, content: str) -> None:
    """Create a pack fixture with a valid default manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        content.strip(),
        encoding="utf-8",
    )


def valid_pack(**overrides: str) -> str:
    """Build a hand-readable manifest fixture with selected replacements."""
    fields = {
        "id": "method.example",
        "kind": "method",
        "version": "1.0.0",
        "kernel": ">=0.1,<1.0",
        "schema": ">=1.0,<2.0",
        "entrypoint": "example:run",
    }
    fields.update(overrides)
    return "\n".join(f"{key}: {dumps(value)}" for key, value in fields.items())


def test_registry_rejects_incompatible_kernel(tmp_path: Path) -> None:
    """A kernel outside the pack range cannot resolve that pack."""
    write_pack(
        tmp_path / "pack.yaml",
        valid_pack(kernel=">=0.2,<1.0"),
    )
    registry = PackRegistry.discover(tmp_path)

    with pytest.raises(ValueError, match="kernel 0.1.0"):
        registry.require_compatible("method.example", "0.1.0", "1.0")


def test_registry_rejects_incompatible_schema(tmp_path: Path) -> None:
    """A schema outside the pack range cannot resolve that pack."""
    write_pack(
        tmp_path / "pack.yaml",
        valid_pack(schema=">=2.0,<3.0"),
    )
    registry = PackRegistry.discover(tmp_path)

    with pytest.raises(ValueError, match="schema 1.0"):
        registry.require_compatible("method.example", "0.1.0", "1.0")


def test_registry_returns_compatible_manifest(tmp_path: Path) -> None:
    """A matching kernel and schema resolve the indexed manifest itself."""
    write_pack(tmp_path / "pack.yaml", valid_pack())
    registry = PackRegistry.discover(tmp_path)

    resolved = registry.require_compatible("method.example", "0.1.0", "1.0")

    assert resolved is registry.manifests["method.example"]
    assert resolved.model_dump(by_alias=True) == {
        "id": "method.example",
        "kind": "method",
        "version": "1.0.0",
        "kernel": ">=0.1,<1.0",
        "schema": ">=1.0,<2.0",
        "entrypoint": "example:run",
    }


def test_registry_rejects_duplicate_ids(tmp_path: Path) -> None:
    """Two independently discovered packs cannot share one stable ID."""
    write_pack(tmp_path / "a" / "pack.yaml", valid_pack())
    write_pack(tmp_path / "b" / "pack.yaml", valid_pack())

    with pytest.raises(ValueError, match="duplicate pack id: method.example"):
        PackRegistry.discover(tmp_path)


def test_manifest_rejects_unknown_kind() -> None:
    """Only the five supported capability categories can be declared."""
    with pytest.raises(ValidationError, match="kind"):
        PackManifest.model_validate({
            "id": "unknown.example",
            "kind": "unknown",
            "version": "1.0.0",
            "kernel": ">=0.1",
            "schema": ">=1.0",
            "entrypoint": "example:run",
        })


def test_manifest_rejects_non_semver_version() -> None:
    """Pack versions must include SemVer major, minor, and patch components."""
    with pytest.raises(ValidationError, match="SemVer"):
        PackManifest.model_validate({
            "id": "method.example",
            "kind": "method",
            "version": "1.0",
            "kernel": ">=0.1",
            "schema": ">=1.0",
            "entrypoint": "example:run",
        })


@pytest.mark.parametrize(
    ("field", "value"),
    [("kernel", ">=invalid"), ("schema", "not a specifier")],
)
def test_manifest_rejects_invalid_version_specifiers(field: str, value: str) -> None:
    """Compatibility range fields must parse as packaging specifier sets."""
    payload = {
        "id": "method.example",
        "kind": "method",
        "version": "1.0.0",
        "kernel": ">=0.1",
        "schema": ">=1.0",
        "entrypoint": "example:run",
    }
    payload[field] = value

    with pytest.raises(ValidationError, match="specifier"):
        PackManifest.model_validate(payload)


def test_registry_rejects_invalid_yaml(tmp_path: Path) -> None:
    """Malformed YAML is reported with the manifest path for repair."""
    manifest_path = tmp_path / "pack.yaml"
    write_pack(manifest_path, "id: [unterminated")

    with pytest.raises(ValueError, match="pack.yaml"):
        PackRegistry.discover(tmp_path)


def test_discovery_is_recursive_and_deterministic(tmp_path: Path) -> None:
    """Nested manifests are indexed in lexical path order regardless of creation order."""
    write_pack(
        tmp_path / "z-last" / "pack.yaml",
        valid_pack(id="method.z-last", entrypoint="z:run"),
    )
    write_pack(
        tmp_path / "a-first" / "nested" / "pack.yaml",
        valid_pack(id="paper.a-first", kind="paper", entrypoint="a:run"),
    )

    registry = PackRegistry.discover(tmp_path)

    assert list(registry.manifests) == ["paper.a-first", "method.z-last"]


def test_registry_reports_missing_pack_id(tmp_path: Path) -> None:
    """Missing compatible-pack requests name the unknown ID."""
    registry = PackRegistry.discover(tmp_path)

    with pytest.raises(ValueError, match="pack not found: missing.pack"):
        registry.require_compatible("missing.pack", "0.1.0", "1.0")
