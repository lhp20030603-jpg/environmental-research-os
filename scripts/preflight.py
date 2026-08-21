#!/usr/bin/env python3
"""Cross-platform, non-data-acquiring installation preflight."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal, TypedDict

Status = Literal["pass", "fail", "warn", "skip"]


class Check(TypedDict):
    """One stable preflight result."""

    name: str
    status: Status
    detail: str


REQUIRED_PATHS = (
    "pyproject.toml",
    "uv.lock",
    "src/envresearch",
    "tests",
    "configs",
    "benchmarks",
)


def result(name: str, status: Status, detail: str) -> Check:
    """Build one JSON-safe result."""
    return {"name": name, "status": status, "detail": detail}


def run_command(
    name: str,
    command: list[str],
    *,
    root: Path,
    timeout: int = 30,
) -> Check:
    """Run a bounded command without a shell or environment mutation tricks."""
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return result(name, "fail", f"could not run: {exc}")
    if completed.returncode == 0:
        return result(name, "pass", "command completed successfully")
    output = (completed.stderr or completed.stdout).strip().splitlines()
    detail = output[-1][:300] if output else f"exit code {completed.returncode}"
    return result(name, "fail", detail)


def check_python() -> Check:
    """Require the Python range declared by the distribution."""
    version = sys.version_info[:3]
    supported = (3, 11) <= version[:2] <= (3, 13)
    detail = ".".join(str(part) for part in version)
    if supported:
        return result("python-version", "pass", f"Python {detail}")
    return result(
        "python-version",
        "fail",
        f"Python {detail}; supported versions are 3.11 through 3.13",
    )


def check_layout(root: Path) -> Check:
    """Confirm this is a complete source checkout, not a partial file copy."""
    missing = [path for path in REQUIRED_PATHS if not (root / path).exists()]
    if missing:
        return result("repository-layout", "fail", f"missing: {', '.join(missing)}")
    return result("repository-layout", "pass", "required files are present")


def check_uv() -> tuple[Check, str | None]:
    """Resolve uv without assuming a platform-specific installation path."""
    executable = shutil.which("uv")
    if executable is None:
        return result("uv-command", "fail", "uv is not on PATH"), None
    return result("uv-command", "pass", executable), executable


def check_r(root: Path) -> list[Check]:
    """Inspect the optional reviewed R baseline and common packages."""
    executable = shutil.which("Rscript")
    if executable is None:
        return [result("r-runtime", "fail", "Rscript is not on PATH")]
    try:
        version = subprocess.run(
            [executable, "--version"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return [
            result(
                "r-runtime",
                "fail",
                "could not inspect Rscript: timed out after 20 seconds",
            )
        ]
    except OSError as exc:
        return [result("r-runtime", "fail", f"could not inspect Rscript: {exc}")]
    version_text = (version.stdout or version.stderr).strip().splitlines()
    rendered = version_text[0] if version_text else "unknown R version"
    version_status: Status = "pass" if "4.4.3" in rendered else "warn"
    checks = [
        result(
            "r-runtime",
            version_status,
            f"{rendered}; reviewed baseline is R 4.4.3",
        )
    ]
    expression = (
        "pkgs <- c('fixest','did'); "
        "missing <- pkgs[!vapply(pkgs, requireNamespace, logical(1), "
        "quietly=TRUE)]; "
        "if (length(missing)) { "
        "cat(paste(missing, collapse=',')); quit(status=2) }; "
        "cat(paste(pkgs, vapply(pkgs, function(x) "
        "as.character(packageVersion(x)), character(1)), sep='=', "
        "collapse=','))"
    )
    package_check = run_command(
        "r-packages", [executable, "--vanilla", "-e", expression], root=root
    )
    checks.append(package_check)
    return checks


def parse_args() -> argparse.Namespace:
    """Parse the public preflight interface."""
    parser = argparse.ArgumentParser(
        description=(
            "Check a source checkout without downloading data or running analyses."
        )
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="run 'uv sync --locked --dev' before checking CLI startup",
    )
    parser.add_argument(
        "--with-r",
        action="store_true",
        help="also inspect optional R 4.4.3, fixest, and did",
    )
    parser.add_argument(
        "--skip-runtime",
        action="store_true",
        help="skip uv lock and CLI subprocesses; useful for portable file checks",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args()
    if args.sync and args.skip_runtime:
        parser.error("--sync cannot be combined with --skip-runtime")
    return args


def main() -> int:
    """Run required checks and optional runtime checks."""
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    checks = [check_python(), check_layout(root)]
    uv_check, uv_executable = check_uv()
    checks.append(uv_check)

    if args.skip_runtime:
        checks.extend(
            [
                result("uv-runtime", "skip", "disabled by --skip-runtime"),
                result("cli-startup", "skip", "disabled by --skip-runtime"),
            ]
        )
    elif uv_executable is None:
        checks.extend(
            [
                result("uv-runtime", "fail", "uv is required"),
                result("cli-startup", "fail", "uv is required"),
            ]
        )
    else:
        if args.sync:
            checks.append(
                run_command(
                    "uv-sync",
                    [uv_executable, "sync", "--locked", "--dev"],
                    root=root,
                    timeout=180,
                )
            )
        checks.append(
            run_command(
                "uv-runtime",
                [uv_executable, "lock", "--check"],
                root=root,
            )
        )
        checks.append(
            run_command(
                "cli-startup",
                [uv_executable, "run", "--no-sync", "envresearch", "--help"],
                root=root,
            )
        )

    if args.with_r:
        checks.extend(check_r(root))

    ok = all(item["status"] not in {"fail"} for item in checks)
    if args.json:
        print(json.dumps({"ok": ok, "checks": checks}, indent=2, sort_keys=True))
    else:
        for item in checks:
            print(f"[{item['status'].upper():4}] {item['name']}: {item['detail']}")
        print("Preflight passed." if ok else "Preflight failed.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
