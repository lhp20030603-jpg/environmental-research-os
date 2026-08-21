"""Offline archive and HTTP fixtures for Tier-2 intake tests."""

from __future__ import annotations

import io
import tarfile
import urllib.request
from http.client import HTTPMessage
from pathlib import Path
from typing import Self, cast

from pydantic import HttpUrl, TypeAdapter

from envresearch.models.artifact import ArtifactRef, ResearchArtifact
from envresearch.replication.contracts import (
    AcquiredPackageInventory,
    ExternalAdmission,
    Tier2IntakeProposal,
)
from envresearch.replication.intake import Tier2IntakeService
from envresearch.storage.research_artifacts import ResearchArtifactStore

URL = TypeAdapter(HttpUrl).validate_python
FIXTURE = Path(__file__).parents[1] / "fixtures/replication/tiny-did-package.tar.gz"
SHA256 = "a" * 64


class FixtureFetcher:
    """Copy a fixture archive into the service's private staging location."""

    def __init__(self, fixture: Path) -> None:
        self.fixture = fixture

    def fetch(self, url: HttpUrl, destination: Path, max_bytes: int) -> None:
        del url, max_bytes
        destination.write_bytes(self.fixture.read_bytes())


class TarFixtureFetcher:
    """Build one deliberately unsafe archive for each inspection boundary."""

    def __init__(self, variant: str) -> None:
        self.variant = variant

    def fetch(self, url: HttpUrl, destination: Path, max_bytes: int) -> None:
        del url, max_bytes
        with tarfile.open(destination, "w:gz") as archive:
            if self.variant == "symlink":
                entry = tarfile.TarInfo("data/link.csv")
                entry.type = tarfile.SYMTYPE
                entry.linkname = "../../outside.csv"
                archive.addfile(entry)
                return
            if self.variant == "hardlink":
                entry = tarfile.TarInfo("data/link.csv")
                entry.type = tarfile.LNKTYPE
                entry.linkname = "data/analysis.csv"
                archive.addfile(entry)
                return
            if self.variant == "directory":
                entry = tarfile.TarInfo("notes")
                entry.type = tarfile.DIRTYPE
                archive.addfile(entry)
                return
            if self.variant == "other":
                entry = tarfile.TarInfo("data/pipe")
                entry.type = tarfile.FIFOTYPE
                archive.addfile(entry)
                return
            if self.variant == "traversal":
                _add_file(archive, "../outside.csv", b"outside\n")
                return
            if self.variant == "drive":
                _add_file(archive, "C:/outside.csv", b"outside\n")
                return
            if self.variant == "backslash":
                _add_file(archive, r"data\analysis.csv", b"outside\n")
                return
            if self.variant == "trailing":
                _add_file(archive, "data/analysis.csv/", b"treated,outcome\n1,2\n")
                _add_file(archive, "code/run.R", b'read.csv("data/analysis.csv")\n')
                return
            if self.variant == "duplicate":
                _add_file(archive, "data/analysis.csv", b"one\n")
                _add_file(archive, "data/analysis.csv", b"two\n")
                return
            if self.variant == "undeclared":
                _add_file(archive, "data/analysis.csv", b"treated,outcome\n1,2\n")
                _add_file(archive, "notes/readme.txt", b"not reviewed\n")
                return
            if self.variant == "oversized":
                _add_file(archive, "data/analysis.csv", b"x" * 101)
                return
            if self.variant == "missing":
                _add_file(archive, "data/analysis.csv", b"treated,outcome\n1,2\n")
                return
            raise AssertionError(f"unknown test archive variant: {self.variant}")


class FakeResponse:
    """Offline HTTP response whose reads are completely caller-controlled."""

    def __init__(self, chunks: tuple[bytes, ...], content_length: str | None) -> None:
        self._chunks = iter(chunks)
        self.headers = {"Content-Length": content_length} if content_length else {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def read(self, size: int) -> bytes:
        del size
        return next(self._chunks, b"")


class FakeOpener:
    """Offline opener that records its sole request and yields one response."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests: list[object] = []

    def open(self, request: object) -> FakeResponse:
        self.requests.append(request)
        return self.response


class RedirectingOpener:
    """Offline 302 responder that delegates redirect policy to its handler."""

    def __init__(self, handler: urllib.request.HTTPRedirectHandler) -> None:
        self.handler = handler
        self.requests: list[urllib.request.Request] = []
        self.handler.add_parent(cast(urllib.request.OpenerDirector, self))

    def open(
        self, request: urllib.request.Request, timeout: object | None = None
    ) -> object:
        del timeout
        self.requests.append(request)
        if len(self.requests) > 1:
            return FakeResponse((b"package",), "7")
        request.timeout = None
        headers = HTTPMessage()
        headers["location"] = "https://other.example/package.tar.gz"
        return self.handler.http_error_302(
            request,
            io.BytesIO(),
            302,
            "Found",
            headers,
        )


class FollowingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """The standard redirect policy, used to prove the harness sees follow-ups."""


def _add_file(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    entry = tarfile.TarInfo(name)
    entry.size = len(data)
    archive.addfile(entry, io.BytesIO(data))


def store(tmp_path: Path) -> ResearchArtifactStore:
    return ResearchArtifactStore(tmp_path)


def proposal(
    max_download_bytes: int = 1_000_000, max_storage_bytes: int = 100
) -> Tier2IntakeProposal:
    return Tier2IntakeProposal.model_validate(
        {
            "schema_version": "tier2-intake-v1",
            "package_id": "tiny-did-package",
            "canonical_url": "https://example.org/packages/tiny-did-package.tar.gz",
            "declared_version": "1.0.0",
            "doi": None,
            "license_name": "CC-BY-4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "declared_inputs": (
                {
                    "path": Path("data/analysis.csv"),
                    "purpose": "author-data",
                    "required": True,
                },
                {
                    "path": Path("code/run.R"),
                    "purpose": "author-code",
                    "required": True,
                },
            ),
            "expected_outputs": (
                {
                    "path": "output/table-1.csv",
                    "comparator": "csv_numeric",
                    "expected_path": "expected/table-1.csv",
                },
            ),
            "runtime": {
                "profile_id": "r-did-v1",
                "image_digest": "ghcr.io/envresearch/r-did@sha256:" + SHA256,
                "nonroot_uid_gid": "10001:10001",
            },
            "budget": {
                "max_download_bytes": max_download_bytes,
                "max_storage_bytes": max_storage_bytes,
                "max_memory_bytes": 512_000_000,
                "inactivity_seconds": 300,
            },
            "self_contained": True,
        }
    )


def proposal_ref(service: Tier2IntakeService) -> ArtifactRef:
    return service.record_proposal(proposal())


def approval() -> ExternalAdmission:
    return ExternalAdmission(
        approver_id="researcher-17",
        rationale="The public package has a compatible license.",
        approved_locator=URL("https://example.org/packages/tiny-did-package.tar.gz"),
    )


def approved_ref(service: Tier2IntakeService) -> ArtifactRef:
    return service.approve(proposal_ref(service), approval())


def approved_url() -> HttpUrl:
    return URL("https://example.org/packages/tiny-did-package.tar.gz")


def load_inventory(tmp_path: Path, acquired: ArtifactRef) -> AcquiredPackageInventory:
    artifact = store(tmp_path).read_structured(
        Path(f"artifacts/replication/inventories/{acquired.content_hash}.json"),
        TypeAdapter(ResearchArtifact[object]),
    )
    assert isinstance(artifact.payload, dict)
    payload = dict(artifact.payload)
    files = payload["files"]
    assert isinstance(files, list)
    restored_files: list[dict[str, object]] = []
    for item in files:
        assert isinstance(item, dict)
        restored = dict(item)
        restored["path"] = Path(str(restored["path"]))
        restored_files.append(restored)
    payload["files"] = tuple(restored_files)
    return AcquiredPackageInventory.model_validate(payload)
