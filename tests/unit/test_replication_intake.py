"""Tests for approved Tier-2 archive acquisition and inventory."""

from __future__ import annotations

import hashlib
import os
import urllib.request
from pathlib import Path

import pytest
from replication_intake_fixtures import (
    FIXTURE,
    URL,
    FakeOpener,
    FakeResponse,
    FixtureFetcher,
    FollowingRedirectHandler,
    RedirectingOpener,
    TarFixtureFetcher,
    approval,
    approved_ref,
    approved_url,
    load_inventory,
    proposal,
    store,
)

from envresearch.replication.intake import (
    HttpArchiveFetcher,
    Tier2IntakeService,
    _NoRedirectHandler,
)


def test_acquire_requires_the_exact_approved_locator(tmp_path: Path) -> None:
    """A caller cannot replace the independently approved external locator."""
    service = Tier2IntakeService(store(tmp_path), fetcher=FixtureFetcher(FIXTURE))
    approved = approved_ref(service)

    with pytest.raises(PermissionError, match="approved locator"):
        service.acquire(approved, URL("https://other.example/package.tar.gz"))


def test_acquire_writes_deterministic_raw_archive_and_inventory(tmp_path: Path) -> None:
    """A reviewed self-contained package is archived without extraction."""
    service = Tier2IntakeService(store(tmp_path), fetcher=FixtureFetcher(FIXTURE))
    acquired = service.acquire(approved_ref(service), approved_url())

    inventory = load_inventory(tmp_path, acquired)
    assert inventory.archive_sha256 == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert [
        (item.path.as_posix(), item.bytes, item.sha256) for item in inventory.files
    ] == [
        (
            "code/run.R",
            30,
            "10a507edf4af416877d629fb7892e1db3b07882dcfadf6d0e9177ad0938c3e02",
        ),
        (
            "data/analysis.csv",
            20,
            "51c6a72451b5502721d1e091e2499bc15034a7de8ecdf9b4a8d5d03a5eea77b9",
        ),
    ]
    assert list((tmp_path / "artifacts/replication/raw").glob("*.tar.gz"))


@pytest.mark.parametrize(
    ("variant", "message"),
    [
        ("symlink", "symlink"),
        ("hardlink", "symlink"),
        ("directory", "non-regular"),
        ("other", "non-regular"),
        ("traversal", "safe relative path"),
        ("drive", "safe relative path"),
        ("backslash", "safe relative path"),
        ("trailing", "safe relative path"),
        ("duplicate", "duplicate"),
        ("undeclared", "undeclared"),
        ("oversized", "storage budget"),
        ("missing", "every declared input"),
    ],
)
def test_unsafe_archive_leaves_no_acquired_artifact(
    tmp_path: Path, variant: str, message: str
) -> None:
    """Rejected archives never become visible in the immutable artifact store."""
    service = Tier2IntakeService(store(tmp_path), fetcher=TarFixtureFetcher(variant))

    with pytest.raises(ValueError, match=message):
        service.acquire(approved_ref(service), approved_url())

    assert not (tmp_path / "artifacts/replication/raw").exists()
    assert not (tmp_path / "artifacts/replication/inventories").exists()


def test_acquire_rejects_a_download_larger_than_its_approved_budget(
    tmp_path: Path,
) -> None:
    """A fetcher cannot bypass the post-download byte-budget check."""
    service = Tier2IntakeService(store(tmp_path), fetcher=FixtureFetcher(FIXTURE))
    proposal_reference = service.record_proposal(proposal(max_download_bytes=100))
    approved = service.approve(proposal_reference, approval())

    with pytest.raises(ValueError, match="download exceeds approved byte budget"):
        service.acquire(approved, approved_url())

    assert not (tmp_path / "artifacts/replication/raw").exists()
    assert not (tmp_path / "artifacts/replication/inventories").exists()


def test_publication_failure_rolls_back_current_archive_and_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed second publication leaves no current raw or inventory artifact."""
    service = Tier2IntakeService(store(tmp_path), fetcher=FixtureFetcher(FIXTURE))
    original_replace = os.replace
    calls = 0

    def fail_second_publication(source: Path | str, destination: Path | str) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("simulated inventory publication failure")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_second_publication)

    with pytest.raises(OSError, match="simulated inventory publication failure"):
        service.acquire(approved_ref(service), approved_url())

    assert not list((tmp_path / "artifacts/replication/raw").glob("*.tar.gz"))
    assert not list((tmp_path / "artifacts/replication/inventories").glob("*.json"))


def test_http_fetcher_rejects_an_offline_redirect_without_a_follow_up_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 302 reaches the redirect handler but cannot cause a second request."""
    openers: list[RedirectingOpener] = []

    def build_opener(handler: _NoRedirectHandler) -> RedirectingOpener:
        opener = RedirectingOpener(handler)
        openers.append(opener)
        return opener

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)

    with pytest.raises(ValueError, match="archive fetch failed"):
        HttpArchiveFetcher().fetch(approved_url(), tmp_path / "download.tar.gz", 7)

    assert len(openers) == 1
    assert len(openers[0].requests) == 1


def test_recording_redirect_opener_observes_a_following_handler_second_request() -> (
    None
):
    """The offline parent opener must expose a redirect follow-up request."""
    handler = FollowingRedirectHandler()
    opener = RedirectingOpener(handler)

    opener.open(urllib.request.Request(str(approved_url()), method="GET"))

    assert len(opener.requests) == 2


@pytest.mark.parametrize(
    ("content_length", "chunks", "message"),
    [
        ("not-a-number", (b"package",), "invalid Content-Length"),
        ("8", (b"package",), "download exceeds approved byte budget"),
        (None, (b"four", b"more"), "download exceeds approved byte budget"),
    ],
)
def test_http_fetcher_rejects_bad_or_oversized_offline_responses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content_length: str | None,
    chunks: tuple[bytes, ...],
    message: str,
) -> None:
    """Header and stream bounds fail before any archive inspection occurs."""
    opener = FakeOpener(FakeResponse(chunks, content_length))
    monkeypatch.setattr(urllib.request, "build_opener", lambda _: opener)

    with pytest.raises(ValueError, match=message):
        HttpArchiveFetcher().fetch(approved_url(), tmp_path / "download.tar.gz", 7)

    assert len(opener.requests) == 1
