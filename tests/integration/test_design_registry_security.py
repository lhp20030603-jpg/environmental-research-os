"""Strict wire-schema and descriptor-confined design benchmark tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import get_type_hints

import pytest
import yaml  # type: ignore[import-untyped]

from envresearch.benchmarks.design_registry import (
    DesignBenchmarkRegistry,
    replay_design_fixture,
)
from envresearch.benchmarks.design_scoring import (
    RESEARCH_QUALITY_DIMENSIONS,
    RESEARCH_QUALITY_RUBRIC_VERSION,
)


def _payload(*, tier: object = 0, executes: object = False) -> dict[str, object]:
    return {
        "id": "strict-boundary",
        "version": "1.0",
        "tier": tier,
        "source": "repository-owned synthetic fixture",
        "license": "CC0-1.0",
        "input_fixture": "replay.yaml",
        "replay_fixture": "replay.yaml",
        "expected_phase": "waiting_for_agent",
        "expected_artifacts": ["artifacts/research-brief.yaml"],
        "rubric_version": RESEARCH_QUALITY_RUBRIC_VERSION,
        "rubric_thresholds": {
            dimension: 3 for dimension in RESEARCH_QUALITY_DIMENSIONS
        },
        "executes_replication_package": executes,
    }


def _write(root: Path, payload: dict[str, object] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "benchmark.yaml"
    manifest.write_text(
        yaml.safe_dump(payload or _payload(), sort_keys=True), encoding="utf-8"
    )
    return manifest


@pytest.mark.parametrize("executes", (True, 1, "true"))
def test_execution_flag_rejects_every_true_like_wire_value(
    tmp_path: Path, executes: object
) -> None:
    """Wire coercion cannot turn a forbidden execution request into acceptance."""
    _write(tmp_path, _payload(executes=executes))

    with pytest.raises(ValueError, match="replication package execution"):
        DesignBenchmarkRegistry.discover(tmp_path)


@pytest.mark.parametrize("tier", (True, False, "0", 0.0, 2, 3))
def test_tier_requires_an_exact_supported_integer_wire_type(
    tmp_path: Path, tier: object
) -> None:
    """Boolean, string, float, and unsupported tiers are rejected before parsing."""
    _write(tmp_path, _payload(tier=tier))

    with pytest.raises(ValueError, match="Tier|tier"):
        DesignBenchmarkRegistry.discover(tmp_path)


def test_registry_rejects_symlinked_manifest(tmp_path: Path) -> None:
    """Discovery must never follow a manifest alias outside the catalog root."""
    outside = _write(tmp_path / "outside")
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    (catalog / "benchmark.yaml").symlink_to(outside)

    with pytest.raises(ValueError, match="symlink|regular"):
        DesignBenchmarkRegistry.discover(catalog)


def test_replay_rejects_symlinked_input(tmp_path: Path) -> None:
    """Fixture input must be a regular inode beneath its pinned fixture root."""
    fixture = tmp_path / "fixture"
    _write(fixture)
    outside = tmp_path / "outside.yaml"
    outside.write_text("pending_work_order_nodes: [frame-charters]\n", encoding="utf-8")
    (fixture / "replay.yaml").symlink_to(outside)

    with pytest.raises(ValueError, match="symlink|regular"):
        replay_design_fixture(fixture)


def test_replay_rejects_hard_linked_input(tmp_path: Path) -> None:
    """A multi-link fixture file cannot hide an externally mutable alias."""
    fixture = tmp_path / "fixture"
    _write(fixture)
    outside = tmp_path / "outside.yaml"
    outside.write_text("scenario: broad-topic\n", encoding="utf-8")
    os.link(outside, fixture / "replay.yaml")

    with pytest.raises(ValueError, match="link count"):
        replay_design_fixture(fixture)


def test_replay_rejects_fifo_without_attempting_to_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Special files are rejected by metadata before any potentially blocking read."""
    fixture = tmp_path / "fixture"
    _write(fixture)
    fifo = fixture / "replay.yaml"
    os.mkfifo(fifo)
    original = Path.read_text

    def guarded(path: Path, *args: object, **kwargs: object) -> str:
        if path == fifo:
            pytest.fail("unsafe FIFO reached Path.read_text")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    with pytest.raises(ValueError, match="regular"):
        replay_design_fixture(fixture)


def test_registry_rejects_symlinked_root(tmp_path: Path) -> None:
    """Catalog roots themselves must be pinned without following aliases."""
    actual = tmp_path / "actual"
    _write(actual)
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink|root"):
        DesignBenchmarkRegistry.discover(alias)


def test_registry_reexports_runtime_replay_result_annotation() -> None:
    """The public replay result remains importable and introspectable at runtime."""
    from envresearch.benchmarks.design_registry import DesignFixtureReplay

    hints = get_type_hints(replay_design_fixture)

    assert hints["return"] is DesignFixtureReplay
