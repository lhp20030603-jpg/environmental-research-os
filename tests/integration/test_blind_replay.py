"""Opt-in blind descriptor integration for design fixture replay."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from test_blind_registry_security import write_case

from envresearch.benchmarks.blind_registry import BlindBenchmarkRegistry
from envresearch.benchmarks.design_files import PinnedFixtureRoot
from envresearch.benchmarks.design_registry import replay_design_fixture

FIXTURE_ROOT = Path("benchmarks/design/fixtures")


def _opted_in_fixture(tmp_path: Path) -> Path:
    fixture = tmp_path / "fixture"
    shutil.copytree(FIXTURE_ROOT / "broad-topic", fixture)
    write_case(fixture / "blind")
    manifest = fixture / "benchmark.yaml"
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    payload.update(
        blind_manifest="blind/benchmark.yaml",
        blind_rubric_version="blind-method-v1",
    )
    manifest.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return fixture


def test_design_replay_reaches_explicit_blind_descriptor(tmp_path: Path) -> None:
    result = replay_design_fixture(_opted_in_fixture(tmp_path))
    assert result.overall_pass is True
    assert result.blind_case_evaluation is None


@pytest.mark.parametrize("attack", ("malformed", "symlink"))
def test_design_replay_rejects_invalid_explicit_blind_descriptor(
    tmp_path: Path, attack: str
) -> None:
    fixture = _opted_in_fixture(tmp_path)
    if attack == "malformed":
        manifest = fixture / "blind/benchmark.yaml"
        payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        payload["tier"] = 0
        manifest.write_text(yaml.safe_dump(payload), encoding="utf-8")
    else:
        source = fixture / "blind/curator-source-sheet.yaml"
        outside = tmp_path / "outside.yaml"
        outside.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(outside)
    with pytest.raises(ValueError, match="Tier 1|symlink"):
        replay_design_fixture(fixture)


def test_blind_case_directory_replacement_cannot_change_pinned_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _opted_in_fixture(tmp_path)
    original = BlindBenchmarkRegistry._read_manifest
    swapped = False

    def replace_after_manifest(pinned: PinnedFixtureRoot, path: Path) -> object:
        nonlocal swapped
        manifest = original(pinned, path)
        if pinned.root.name == "blind" and not swapped:
            swapped = True
            backup = fixture / "original-blind"
            pinned.root.rename(backup)
            shutil.copytree(backup, pinned.root)
            (pinned.root / "curator-source-sheet.yaml").write_text("forged")
        return manifest

    monkeypatch.setattr(
        BlindBenchmarkRegistry, "_read_manifest", staticmethod(replace_after_manifest)
    )
    assert replay_design_fixture(fixture).overall_pass is True
