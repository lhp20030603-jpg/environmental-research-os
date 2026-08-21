"""Transactional managed R-library tests without network or real R."""

import hashlib
import io
import subprocess
import tarfile
import urllib.request
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from envresearch.econometrics import managed_r_library as managed_module
from envresearch.econometrics._managed_r_validation import license_matches
from envresearch.econometrics.managed_r_library import (
    FetchedSource,
    ManagedRLibrary,
    MethodAuthorityInvalid,
    _SameHostRedirect,
)
from envresearch.econometrics.method_authority import MethodAuthorityProposal
from envresearch.econometrics.r_evidence import RCommandResult


def test_dual_description_license_matches_spdx_or_expression() -> None:
    assert license_matches(
        "GPL-2.0-or-later OR BSD-3-Clause",
        "GPL (>= 2) | BSD_3_clause + file LICENSE",
    )
    assert not license_matches(
        "GPL-3.0-only OR GPL-3.0-only",
        "GPL-3",
    )
    assert not license_matches("GPL-3.0-only", "GPL-3 | GPL-3")


def _archive(
    *, package: str = "rdrobust", version: str = "3.0.0", license_id: str = "GPL-3"
) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        description = (
            f"Package: {package}\nVersion: {version}\nLicense: {license_id}\n"
            "Depends: R (>= 4.0.0)\n"
        ).encode()
        info = tarfile.TarInfo(f"{package}/DESCRIPTION")
        info.size = len(description)
        archive.addfile(info, io.BytesIO(description))
        body = b'exportPattern("^[[:alpha:]]+")\n'
        info = tarfile.TarInfo(f"{package}/NAMESPACE")
        info.size = len(body)
        archive.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


class BytesFetcher:
    """Return fixed official bytes without network access."""

    def __init__(self, data: bytes, final_url: str | None = None) -> None:
        self.data = data
        self.final_url = final_url
        self.calls = 0

    def fetch(self, url: str, *, max_bytes: int) -> FetchedSource:
        self.calls += 1
        assert len(self.data) <= max_bytes
        return FetchedSource(data=self.data, final_url=self.final_url or url)


class ArchiveInstaller:
    """Materialize a package tree while recording the exact install command."""

    def __init__(self, *, return_code: int = 0) -> None:
        self.return_code = return_code
        self.calls: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []

    def execute(
        self, argv: tuple[str, ...], *, cwd: Path, env: Mapping[str, str]
    ) -> RCommandResult:
        self.calls.append((argv, cwd, dict(env)))
        if self.return_code:
            return RCommandResult(
                return_code=self.return_code, stdout=b"", stderr=b"install failed"
            )
        target = Path(argv[3].split("=", 1)[1])
        with tarfile.open(argv[4], mode="r:gz") as archive:
            archive.extractall(cwd / "unpacked", filter="data")
        source = cwd / "unpacked" / "rdrobust"
        package = target / "rdrobust"
        package.mkdir(parents=True)
        for source_file in source.iterdir():
            (package / source_file.name).write_bytes(source_file.read_bytes())
        return RCommandResult(return_code=0, stdout=b"installed\n", stderr=b"")


def _proposal(data: bytes, **updates: object) -> MethodAuthorityProposal:
    payload: dict[str, object] = {
        "package": "rdrobust",
        "version": "3.0.0",
        "source_url": "https://cran.r-project.org/src/contrib/rdrobust_3.0.0.tar.gz",
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "license": "GPL-3.0-only",
        "description_license": "GPL-3",
        "dependencies": ({"package": "R", "version": "4.4.3", "base": True},),
    }
    payload.update(updates)
    return MethodAuthorityProposal.model_validate(payload)


def test_acquire_publishes_and_reopens_exact_authority(tmp_path: Path) -> None:
    """One source transaction yields an immutable, re-verifiable package tree."""
    data = _archive()
    fetcher = BytesFetcher(data)
    installer = ArchiveInstaller()
    library = ManagedRLibrary(
        tmp_path / "store",
        fetcher=fetcher,
        installer=installer,
        r_executable=Path("/reviewed/R"),
    )

    (authority,) = library.acquire((_proposal(data),))

    assert authority.proposal.package == "rdrobust"
    assert (library.root / "rdrobust/DESCRIPTION").is_file()
    assert library.verify((authority,)) == (authority,)
    assert installer.calls[0][0][:3] == ("/reviewed/R", "CMD", "INSTALL")
    assert installer.calls[0][0][3].startswith("--library=")
    assert library.acquire((_proposal(data),)) == (authority,)
    assert fetcher.calls == 1


@pytest.mark.parametrize("kind", ["digest", "redirect", "license", "install"])
def test_acquire_fails_closed_without_partial_publication(
    tmp_path: Path, kind: str
) -> None:
    """External identity or installation failures never publish a package."""
    data = _archive()
    proposal = _proposal(data)
    fetcher = BytesFetcher(data)
    installer = ArchiveInstaller()
    if kind == "digest":
        proposal = _proposal(data, source_sha256="0" * 64)
    elif kind == "redirect":
        fetcher.final_url = "https://evil.example/rdrobust.tar.gz"
    elif kind == "license":
        proposal = _proposal(data, license="MIT")
    else:
        installer.return_code = 1
    library = ManagedRLibrary(tmp_path / "store", fetcher=fetcher, installer=installer)

    with pytest.raises(MethodAuthorityInvalid):
        library.acquire((proposal,))

    assert not (library.root / "rdrobust").exists()


def test_https_fetcher_rejects_cross_host_redirect_before_following() -> None:
    """Redirect authority is checked before requesting bytes from another host."""
    handler = _SameHostRedirect("cran.r-project.org")
    request = urllib.request.Request("https://cran.r-project.org/source.tar.gz")

    with pytest.raises(MethodAuthorityInvalid, match="outside official host"):
        handler.redirect_request(
            request,
            None,  # type: ignore[arg-type]
            302,
            "redirect",
            {},  # type: ignore[arg-type]
            "https://evil.example/source.tar.gz",
        )


def test_acquire_rejects_unsafe_archive_member(tmp_path: Path) -> None:
    """Traversal, links, and nonregular members are rejected before installation."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("../escape")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    data = buffer.getvalue()
    library = ManagedRLibrary(
        tmp_path / "store", fetcher=BytesFetcher(data), installer=ArchiveInstaller()
    )

    with pytest.raises(MethodAuthorityInvalid, match="unsafe archive"):
        library.acquire((_proposal(data),))

    assert not (tmp_path / "escape").exists()


def test_verify_detects_installed_tree_tampering(tmp_path: Path) -> None:
    """Execution cannot consume a package tree changed after admission."""
    data = _archive()
    library = ManagedRLibrary(
        tmp_path / "store", fetcher=BytesFetcher(data), installer=ArchiveInstaller()
    )
    (authority,) = library.acquire((_proposal(data),))
    namespace = library.root / "rdrobust/NAMESPACE"
    namespace.chmod(0o644)
    namespace.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(MethodAuthorityInvalid, match="tree identity"):
        library.verify((authority,))


def test_acquire_recovers_package_published_before_authority_record(
    tmp_path: Path,
) -> None:
    """A record-write interruption is retryable without adopting unknown bytes."""
    data = _archive()
    installer = ArchiveInstaller()
    library = ManagedRLibrary(
        tmp_path / "store", fetcher=BytesFetcher(data), installer=installer
    )
    persist = library.files.persist_exact
    failed = False

    def fail_record(path: Path, content: bytes) -> None:
        nonlocal failed
        if path.parts[:2] == ("authorities", "records") and not failed:
            failed = True
            raise OSError("injected record publication failure")
        persist(path, content)

    library.files.persist_exact = fail_record  # type: ignore[method-assign]
    with pytest.raises(MethodAuthorityInvalid):
        library.acquire((_proposal(data),))
    library.files.persist_exact = persist  # type: ignore[method-assign]

    (authority,) = library.acquire((_proposal(data),))

    assert library.verify((authority,)) == (authority,)
    assert len(installer.calls) == 2


def test_concurrent_acquire_has_one_published_install(tmp_path: Path) -> None:
    """Two callers serialize on one package/version authority."""
    data = _archive()
    fetcher = BytesFetcher(data)
    installer = ArchiveInstaller()
    library = ManagedRLibrary(tmp_path / "store", fetcher=fetcher, installer=installer)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = tuple(
            pool.map(lambda _: library.acquire((_proposal(data),))[0], range(2))
        )

    assert first == second
    assert fetcher.calls == 1
    assert len(installer.calls) == 1


def test_cross_instance_acquire_serializes_on_package_identity(tmp_path: Path) -> None:
    """Separate service objects cannot race one package publication."""
    data = _archive()
    fetcher = BytesFetcher(data)
    installer = ArchiveInstaller()
    libraries = tuple(
        ManagedRLibrary(tmp_path / "store", fetcher=fetcher, installer=installer)
        for _ in range(2)
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = tuple(
            pool.map(lambda item: item.acquire((_proposal(data),))[0], libraries)
        )

    assert first == second
    assert len(installer.calls) == 1


def test_alternate_package_version_is_rejected_before_fetch(tmp_path: Path) -> None:
    """One package-only projection cannot race or silently replace its version."""
    first = _archive()
    fetcher = BytesFetcher(first)
    installer = ArchiveInstaller()
    library = ManagedRLibrary(tmp_path / "store", fetcher=fetcher, installer=installer)
    library.acquire((_proposal(first),))
    second = _archive(version="3.1.0")

    with pytest.raises(MethodAuthorityInvalid, match="another package version"):
        library.acquire((_proposal(second, version="3.1.0"),))

    assert fetcher.calls == 1


def test_install_timeout_is_a_typed_transaction_failure(tmp_path: Path) -> None:
    """A bounded sandbox timeout cannot escape as a raw subprocess exception."""
    data = _archive()

    class TimeoutInstaller(ArchiveInstaller):
        def execute(
            self, argv: tuple[str, ...], *, cwd: Path, env: Mapping[str, str]
        ) -> RCommandResult:
            raise subprocess.TimeoutExpired(argv, 1)

    library = ManagedRLibrary(
        tmp_path / "store", fetcher=BytesFetcher(data), installer=TimeoutInstaller()
    )

    with pytest.raises(MethodAuthorityInvalid, match="transaction failed"):
        library.acquire((_proposal(data),))


def test_installer_symlinked_directory_is_rejected_without_external_chmod(
    tmp_path: Path,
) -> None:
    """Installed tree validation lstat-checks directories before hashing/freezing."""
    data = _archive()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker"
    marker.write_text("outside", encoding="utf-8")

    class SymlinkInstaller(ArchiveInstaller):
        def execute(
            self, argv: tuple[str, ...], *, cwd: Path, env: Mapping[str, str]
        ) -> RCommandResult:
            result = super().execute(argv, cwd=cwd, env=env)
            target = Path(argv[3].split("=", 1)[1]) / "rdrobust/linked"
            target.symlink_to(outside, target_is_directory=True)
            return result

    library = ManagedRLibrary(
        tmp_path / "store", fetcher=BytesFetcher(data), installer=SymlinkInstaller()
    )

    with pytest.raises(MethodAuthorityInvalid, match="symlink"):
        library.acquire((_proposal(data),))

    assert marker.stat().st_mode & 0o777 == 0o644


def test_retry_freezes_tree_after_post_replace_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery freezes and rehashes a tree published before process death."""
    data = _archive()
    library = ManagedRLibrary(
        tmp_path / "store", fetcher=BytesFetcher(data), installer=ArchiveInstaller()
    )
    freeze = managed_module._freeze_tree
    failed = False

    def interrupt(root: Path) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("injected pre-freeze crash")
        freeze(root)

    monkeypatch.setattr(managed_module, "_freeze_tree", interrupt)
    with pytest.raises(MethodAuthorityInvalid):
        library.acquire((_proposal(data),))

    (authority,) = library.acquire((_proposal(data),))

    assert library.verify((authority,)) == (authority,)
    assert (library.root / "rdrobust/DESCRIPTION").stat().st_mode & 0o222 == 0


def test_acquisition_requires_explicit_sandboxed_installer(tmp_path: Path) -> None:
    """Production acquisition has no unsafe host-subprocess fallback."""
    data = _archive()
    fetcher = BytesFetcher(data)
    library = ManagedRLibrary(tmp_path / "store", fetcher=fetcher)

    with pytest.raises(MethodAuthorityInvalid, match="sandboxed"):
        library.acquire((_proposal(data),))
    assert fetcher.calls == 0
    assert not (tmp_path / "store/authorities/sources").exists()


def test_verify_rejects_an_unrecorded_package_in_execution_projection(
    tmp_path: Path,
) -> None:
    """R cannot fall through to an extra unauthenticated managed package."""
    data = _archive()
    library = ManagedRLibrary(
        tmp_path / "store", fetcher=BytesFetcher(data), installer=ArchiveInstaller()
    )
    (authority,) = library.acquire((_proposal(data),))
    extra = library.root / "unrecorded"
    extra.mkdir()
    (extra / "DESCRIPTION").write_text(
        "Package: unrecorded\nVersion: 1.0\n", encoding="utf-8"
    )

    with pytest.raises(MethodAuthorityInvalid, match="projection is not closed"):
        library.verify((authority,))
