"""Public Git index audit behavior."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType


def _load_audit(root: Path) -> ModuleType:
    path = root / "scripts" / "publication_audit.py"
    spec = importlib.util.spec_from_file_location("publication_audit_tested", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_publication_audit_accepts_the_reviewed_index() -> None:
    """The current public index must exclude private and machine-local state."""
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "publication_audit.py"),
            "--json",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["tracked_files"] > 800
    assert payload["findings"] == []
    assert payload["approved_binary_fixtures"] == [
        "tests/fixtures/replication/tiny-did-package.tar.gz"
    ]


def test_publication_audit_reads_staged_blobs_not_clean_worktree(
    tmp_path: Path,
) -> None:
    """A secret staged in the index must remain visible after worktree replacement."""
    root = Path(__file__).resolve().parents[2]
    audit_module = _load_audit(root)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    candidate = tmp_path / "candidate.txt"
    aws_key = "AKIA" + "1234567890ABCDEF"
    candidate.write_text(
        aws_key + "\nsk-proj-" + "a" * 32,
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "candidate.txt"], cwd=tmp_path, check=True)
    candidate.write_text("clean working tree copy", encoding="utf-8")

    findings, _, count = audit_module.audit(tmp_path)

    assert count == 1
    assert {item["kind"] for item in findings} == {
        "aws-access-key",
        "openai-key",
    }
