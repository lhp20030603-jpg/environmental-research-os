"""Public-installation preflight behavior."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_preflight(root: Path) -> ModuleType:
    path = root / "scripts" / "preflight.py"
    spec = importlib.util.spec_from_file_location("preflight_tested", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_reports_portable_repository_checks() -> None:
    """The no-runtime mode must be deterministic and require no optional tools."""
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "preflight.py"),
            "--json",
            "--skip-runtime",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    checks = {item["name"]: item for item in payload["checks"]}
    assert checks["python-version"]["status"] == "pass"
    assert checks["repository-layout"]["status"] == "pass"
    assert checks["uv-runtime"]["status"] == "skip"
    assert checks["cli-startup"]["status"] == "skip"


def test_preflight_help_explains_optional_r_and_sync() -> None:
    """Classmates should be able to discover all mutating/optional checks."""
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, str(root / "scripts" / "preflight.py"), "--help"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--sync" in completed.stdout
    assert "--with-r" in completed.stdout
    assert "--skip-runtime" in completed.stdout


def test_optional_r_version_failure_is_structured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unavailable or hung optional R runtime must not escape as a traceback."""
    root = Path(__file__).resolve().parents[2]
    module = _load_preflight(root)
    monkeypatch.setattr(module.shutil, "which", lambda _: "/reviewed/Rscript")

    def fail_version(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(args[0], timeout=20)

    monkeypatch.setattr(module.subprocess, "run", fail_version)

    checks = module.check_r(root)

    assert checks == [
        {
            "name": "r-runtime",
            "status": "fail",
            "detail": "could not inspect Rscript: timed out after 20 seconds",
        }
    ]
