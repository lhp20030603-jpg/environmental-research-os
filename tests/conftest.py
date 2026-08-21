"""Reusable deterministic benchmark fixtures for integration tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]


def _write_manifest(root: Path, payload: dict[str, object]) -> Path:
    """Write one hand-authored benchmark manifest for a test case."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "benchmark.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    return path


def _manifest_payload(*, public: bool = False) -> dict[str, object]:
    """Return a valid baseline manifest with no executable real-world inputs."""
    return {
        "id": "seeded-fixture",
        "title": "Seeded fixture",
        "method_family": "integration",
        "topic": "quality gate",
        "public": public,
        "source_url": "https://example.org/source",
        "commands": [],
        "expected_outputs": [],
    }


def _case_with_raw(root: Path) -> tuple[Path, Path]:
    """Create a synthetic case root and its immutable source file."""
    case_root = root / "case"
    source = case_root / "raw" / "source.txt"
    source.parent.mkdir(parents=True)
    source.write_text("synthetic source\n", encoding="utf-8")
    return case_root, source


@pytest.fixture
def valid_manifest_path(tmp_path: Path) -> Path:
    """Provide a minimal valid, repository-owned benchmark manifest."""
    return _write_manifest(tmp_path / "valid", _manifest_payload())


@pytest.fixture
def invalid_schema_manifest_path(tmp_path: Path) -> Path:
    """Provide a manifest missing a required schema field."""
    payload = _manifest_payload()
    del payload["title"]
    return _write_manifest(tmp_path / "invalid-schema", payload)


@pytest.fixture
def missing_doi_manifest_path(tmp_path: Path) -> Path:
    """Provide an otherwise complete public manifest that omits its DOI."""
    payload = _manifest_payload(public=True)
    payload.update(
        {
            "source_version": "v1",
            "source_archive": "raw/source.txt",
            "source_sha256": "0" * 64,
            "license_name": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
        }
    )
    return _write_manifest(tmp_path / "missing-doi", payload)


@pytest.fixture
def mismatch_benchmark(tmp_path: Path) -> tuple[Path, Path]:
    """Provide a synthetic replay whose output differs from its expected file."""
    case_root, source = _case_with_raw(tmp_path / "mismatch")
    manifest_root = tmp_path / "mismatch" / "manifest"
    (manifest_root / "expected.txt").parent.mkdir(parents=True)
    (manifest_root / "expected.txt").write_text("expected\n", encoding="utf-8")
    payload = _manifest_payload()
    payload.update(
        {
            "source_archive": "raw/source.txt",
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "commands": [
                {
                    "argv": [
                        "python",
                        "-c",
                        "from pathlib import Path; Path('result.txt').write_text('actual\\n')",
                    ]
                }
            ],
            "expected_outputs": [
                {
                    "path": "result.txt",
                    "comparator": "exact",
                    "expected_path": "expected.txt",
                }
            ],
        }
    )
    return _write_manifest(manifest_root, payload), case_root


@pytest.fixture
def command_failure_benchmark(tmp_path: Path) -> tuple[Path, Path]:
    """Provide a synthetic replay whose trusted Python command exits non-zero."""
    case_root, source = _case_with_raw(tmp_path / "command-failure")
    payload = _manifest_payload()
    payload.update(
        {
            "source_archive": "raw/source.txt",
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "commands": [{"argv": ["python", "-c", "raise SystemExit(7)"]}],
        }
    )
    return _write_manifest(tmp_path / "command-failure" / "manifest", payload), case_root


@pytest.fixture
def hash_mismatch_benchmark(tmp_path: Path) -> tuple[Path, Path]:
    """Provide a synthetic replay with an intentionally wrong raw-file digest."""
    case_root, _source = _case_with_raw(tmp_path / "hash-mismatch")
    payload = _manifest_payload()
    payload.update(
        {
            "source_archive": "raw/source.txt",
            "source_sha256": "0" * 64,
            "commands": [{"argv": ["python", "-c", "raise SystemExit(99)"]}],
        }
    )
    return _write_manifest(tmp_path / "hash-mismatch" / "manifest", payload), case_root


@pytest.fixture
def interrupted_workspace(tmp_path: Path) -> Path:
    """Provide an empty dedicated workspace for a caller to interrupt and resume."""
    workspace = tmp_path / "interrupted-workspace"
    workspace.mkdir()
    return workspace
