"""Public Personal Pilot metadata and documentation contracts."""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml


def test_public_release_files_have_consistent_identity() -> None:
    """The repository must carry a complete, honest public-release identity."""
    root = Path(__file__).resolve().parents[2]
    repository_url = "https://github.com/lhp20030603-jpg/environmental-research-os"
    required = (
        "README.md",
        "LICENSE",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "CITATION.cff",
        "CHANGELOG.md",
        "docs/releases/v1-personal-pilot.md",
        "docs/github-release-checklist.md",
    )
    assert all((root / path).is_file() for path in required)

    citation = yaml.safe_load((root / "CITATION.cff").read_text(encoding="utf-8"))
    assert citation["title"] == "Environmental Research OS"
    assert citation["version"] == "0.2.0"
    assert citation["license"] == "MIT"
    assert citation["repository-code"] == repository_url

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    assert project["license"] == "MIT"
    assert project["requires-python"] == ">=3.11,<3.14"
    assert "Development Status :: 3 - Alpha" in project["classifiers"]
    assert project["urls"]["Repository"] == repository_url
    assert project["urls"]["Issues"] == f"{repository_url}/issues"

    readme = (root / "README.md").read_text(encoding="utf-8")
    release = (root / required[-2]).read_text(encoding="utf-8")
    for text in (readme, release):
        assert "Personal Pilot / Research Prototype" in text
        assert "scientific_release_pending" in text
        assert f"git clone {repository_url}.git" in text
    assert "not a system lock" not in readme
    assert "不是系统锁" in readme

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    checklist = (root / "docs/github-release-checklist.md").read_text(encoding="utf-8")
    assert repository_url in changelog
    assert repository_url in checklist
    assert "will be added after the public remote" not in changelog
    assert "After the repository owner supplies" not in checklist


def test_public_release_documents_do_not_claim_personal_cli() -> None:
    """Deferred Tasks 7-10 must not be advertised as stable user commands."""
    root = Path(__file__).resolve().parents[2]
    readme = (root / "README.md").read_text(encoding="utf-8")
    release = (root / "docs/releases/v1-personal-pilot.md").read_text(encoding="utf-8")

    assert "尚无稳定 CLI" in readme
    assert "Tasks 7–10 are intentionally outside this preview" in release
    assert "envresearch personal-validation" not in readme
