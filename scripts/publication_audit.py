#!/usr/bin/env python3
"""Audit the exact Git index before a public repository release."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import NamedTuple, TypedDict

MAX_TRACKED_BYTES = 5_000_000
APPROVED_BINARY_FIXTURES = {
    "tests/fixtures/replication/tiny-did-package.tar.gz": (
        "2ad91705c85c5b7f3bd99664510a2ade82b08cd3c64b1565fe669518d9fa44c2"
    )
}
PRIVATE_COMPONENTS = {"runs", "artifacts", "private", "secrets"}
PRIVATE_EXACT_PATHS = {".Codex"}
PRIVATE_PREFIXES = (".claude/project-memory/",)
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
ABSOLUTE_PATH = re.compile(
    rb"(?:/" + rb"Users/[^/\s]+/|/" + rb"home/[^/\s]+/|[A-Za-z]:\\Users\\)"
)
SYNTHETIC_PATH_TOKENS = (
    b"/Users/private/",
    b"/home/private/",
    b"C:\\Users\\private\\",
)
SECRET_PATTERNS = {
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "aws-access-key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "github-token": re.compile(
        rb"(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"
    ),
    "openai-key": re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
}


class Finding(TypedDict):
    """One filename-only public-release finding."""

    path: str
    kind: str
    detail: str


class IndexEntry(NamedTuple):
    """One stage-zero Git index entry."""

    path: Path
    mode: str
    object_id: str


def index_entries(root: Path) -> list[IndexEntry]:
    """Read stage-zero paths, modes, and blob identities from the Git index."""
    completed = subprocess.run(
        ["git", "ls-files", "--stage", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    entries: list[IndexEntry] = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        metadata, encoded_path = raw.split(b"\t", 1)
        mode, object_id, stage = metadata.decode("ascii").split()
        if stage != "0":
            raise ValueError("unmerged index entries cannot be published")
        entries.append(
            IndexEntry(
                path=Path(encoded_path.decode("utf-8")),
                mode=mode,
                object_id=object_id,
            )
        )
    return entries


def index_blobs(root: Path, entries: list[IndexEntry]) -> list[bytes]:
    """Read all staged blob bytes in one bounded Git batch operation."""
    request = b"".join(entry.object_id.encode("ascii") + b"\n" for entry in entries)
    completed = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=root,
        input=request,
        check=True,
        capture_output=True,
    )
    output = completed.stdout
    position = 0
    blobs: list[bytes] = []
    for entry in entries:
        header_end = output.find(b"\n", position)
        if header_end < 0:
            raise ValueError("Git batch output ended before its blob header")
        header = output[position:header_end].split()
        if len(header) != 3 or header[0].decode("ascii") != entry.object_id:
            raise ValueError("Git batch output differs from the requested index blob")
        if header[1] != b"blob":
            raise ValueError("public index contains a non-blob entry")
        size = int(header[2])
        data_start = header_end + 1
        data_end = data_start + size
        if data_end >= len(output) or output[data_end : data_end + 1] != b"\n":
            raise ValueError("Git batch output contains a truncated blob")
        blobs.append(output[data_start:data_end])
        position = data_end + 1
    if position != len(output):
        raise ValueError("Git batch output contains unrequested trailing bytes")
    return blobs


def path_finding(path: Path) -> Finding | None:
    """Reject known private or credential-bearing path shapes."""
    rendered = path.as_posix()
    if rendered in PRIVATE_EXACT_PATHS or rendered.startswith(PRIVATE_PREFIXES):
        return {
            "path": rendered,
            "kind": "private-path",
            "detail": "machine-local project memory must not be published",
        }
    if any(part in PRIVATE_COMPONENTS for part in path.parts):
        return {
            "path": rendered,
            "kind": "private-path",
            "detail": "run, artifact, or private directory is tracked",
        }
    if path.name == ".DS_Store" or path.name == ".env":
        return {
            "path": rendered,
            "kind": "local-file",
            "detail": "local environment metadata is tracked",
        }
    if path.name.startswith(".env.") and path.name != ".env.example":
        return {
            "path": rendered,
            "kind": "local-file",
            "detail": "local environment metadata is tracked",
        }
    if path.suffix.lower() in SENSITIVE_SUFFIXES:
        return {
            "path": rendered,
            "kind": "credential-file",
            "detail": "credential-like file extension is tracked",
        }
    return None


def content_findings(path: Path, data: bytes) -> list[Finding]:
    """Return only high-confidence findings without printing matched secrets."""
    rendered = path.as_posix()
    findings: list[Finding] = []
    if len(data) > MAX_TRACKED_BYTES:
        findings.append(
            {
                "path": rendered,
                "kind": "large-file",
                "detail": f"tracked file is {len(data)} bytes",
            }
        )
    path_scan = data
    for token in SYNTHETIC_PATH_TOKENS:
        path_scan = path_scan.replace(token, b"<synthetic-private-root>/")
    if ABSOLUTE_PATH.search(path_scan):
        findings.append(
            {
                "path": rendered,
                "kind": "absolute-personal-path",
                "detail": "machine-specific user path detected",
            }
        )
    for kind, pattern in SECRET_PATTERNS.items():
        if pattern.search(data):
            findings.append(
                {
                    "path": rendered,
                    "kind": kind,
                    "detail": "high-confidence credential pattern detected",
                }
            )
    return findings


def audit(root: Path) -> tuple[list[Finding], list[str], int]:
    """Audit the exact public index and authenticate allowed binary fixtures."""
    findings: list[Finding] = []
    approved: list[str] = []
    entries = index_entries(root)
    blobs = index_blobs(root, entries)
    for entry, data in zip(entries, blobs, strict=True):
        relative = entry.path
        rendered = relative.as_posix()
        candidate = path_finding(relative)
        if candidate is not None:
            findings.append(candidate)
            continue
        if entry.mode == "120000":
            findings.append(
                {
                    "path": rendered,
                    "kind": "symlink",
                    "detail": "tracked symlink requires a portability review",
                }
            )
            continue
        if entry.mode not in {"100644", "100755"}:
            findings.append(
                {
                    "path": rendered,
                    "kind": "unsupported-index-mode",
                    "detail": f"tracked index mode is {entry.mode}",
                }
            )
            continue
        if b"\0" in data[:8192]:
            expected = APPROVED_BINARY_FIXTURES.get(rendered)
            actual = hashlib.sha256(data).hexdigest()
            if expected == actual:
                approved.append(rendered)
            else:
                findings.append(
                    {
                        "path": rendered,
                        "kind": "binary-file",
                        "detail": "binary is not an authenticated synthetic fixture",
                    }
                )
        findings.extend(content_findings(relative, data))
    findings.sort(key=lambda item: (item["path"], item["kind"]))
    return findings, sorted(approved), len(entries)


def parse_args() -> argparse.Namespace:
    """Parse the stable public audit interface."""
    parser = argparse.ArgumentParser(
        description="Check the exact Git index for public-release hazards."
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    return parser.parse_args()


def main() -> int:
    """Run the audit from the repository containing this script."""
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    findings, approved, count = audit(root)
    payload = {
        "ok": not findings,
        "tracked_files": count,
        "approved_binary_fixtures": approved,
        "findings": findings,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Tracked files: {count}")
        for item in findings:
            print(f"[FAIL] {item['path']}: {item['kind']} - {item['detail']}")
        for path in approved:
            print(f"[PASS] {path}: authenticated synthetic binary fixture")
        print(
            "Publication audit passed." if not findings else "Publication audit failed."
        )
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
