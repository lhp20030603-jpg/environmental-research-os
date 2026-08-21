"""Content-addressed installation boundary for approved external R packages."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from envresearch.econometrics._managed_r_validation import (
    AuthorityValidationError,
    require_source_identity,
)
from envresearch.econometrics._managed_r_validation import (
    description as _description,
)
from envresearch.econometrics._managed_r_validation import (
    freeze_tree as _freeze_tree,
)
from envresearch.econometrics._managed_r_validation import (
    inspect_archive as _inspect_archive,
)
from envresearch.econometrics._managed_r_validation import (
    license_matches as _license_matches,
)
from envresearch.econometrics._managed_r_validation import (
    reject_alternate_version as _reject_alternate_version,
)
from envresearch.econometrics._managed_r_validation import (
    require_closed_graph as _require_closed_graph,
)
from envresearch.econometrics._managed_r_validation import (
    require_description_dependencies as _require_description_dependencies,
)
from envresearch.econometrics._managed_r_validation import (
    sha256 as _sha256,
)
from envresearch.econometrics._managed_r_validation import (
    thread_lock as _thread_lock,
)
from envresearch.econometrics._managed_r_validation import (
    tree_digest as _tree_digest,
)
from envresearch.econometrics._store_files import StoreFiles
from envresearch.econometrics.method_authority import (
    MethodAuthority,
    MethodAuthorityProposal,
)
from envresearch.econometrics.r_evidence import RCommandResult

MAX_SOURCE_BYTES = 128 * 1024 * 1024


class MethodAuthorityInvalid(RuntimeError):
    """An external package could not satisfy its declared authority."""


class FetchedSource(BaseModel):
    """Bounded source bytes and the final URL observed by the fetcher."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    data: bytes
    final_url: str


class SourceFetcher(Protocol):
    """Injected HTTPS byte-acquisition boundary."""

    def fetch(self, url: str, *, max_bytes: int) -> FetchedSource: ...


class PackageInstaller(Protocol):
    """Injected no-shell R package installer."""

    def execute(
        self, argv: tuple[str, ...], *, cwd: Path, env: Mapping[str, str]
    ) -> RCommandResult: ...


class HttpsSourceFetcher:
    """Fetch bounded HTTPS bytes while exposing the final redirect URL."""

    def fetch(self, url: str, *, max_bytes: int) -> FetchedSource:
        host = urlsplit(url).hostname
        if host is None:
            raise MethodAuthorityInvalid("package source URL has no official host")
        opener = urllib.request.build_opener(_SameHostRedirect(host))
        try:
            with opener.open(url, timeout=30) as response:
                final_url = response.geturl()
                data = response.read(max_bytes + 1)
        except OSError as error:
            raise MethodAuthorityInvalid(
                "package source could not be fetched"
            ) from error
        if len(data) > max_bytes:
            raise MethodAuthorityInvalid("package source exceeds its byte limit")
        return FetchedSource(data=data, final_url=final_url)


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Reject an unauthorized redirect before any redirected request is sent."""

    def __init__(self, official_host: str) -> None:
        self.official_host = official_host
        super().__init__()

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        observed = urlsplit(newurl)
        if observed.scheme != "https" or observed.hostname != self.official_host:
            raise MethodAuthorityInvalid(
                "package source redirected outside official host"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ManagedRLibrary:
    """Install and reauthenticate exact R package trees below one run store."""

    def __init__(
        self,
        store_root: Path,
        *,
        fetcher: SourceFetcher | None = None,
        installer: PackageInstaller | None = None,
        r_executable: Path = Path("/usr/bin/R"),
    ) -> None:
        if not store_root.is_absolute():
            store_root = store_root.resolve()
        if store_root.exists() and store_root.is_symlink():
            raise MethodAuthorityInvalid("managed store root cannot be a symlink")
        if not r_executable.is_absolute():
            raise MethodAuthorityInvalid("R installer executable must be absolute")
        self.store_root = store_root.resolve()
        self.root = (self.store_root / "authorities/r-library").resolve()
        if not self.root.is_relative_to(self.store_root):
            raise MethodAuthorityInvalid("managed library escaped its store root")
        self.fetcher = fetcher or HttpsSourceFetcher()
        self.installer = installer
        self.r_executable = r_executable
        self.files = StoreFiles(self.store_root)

    def acquire(
        self, proposals: Sequence[MethodAuthorityProposal]
    ) -> tuple[MethodAuthority, ...]:
        """Acquire proposals serially and return exact reusable authorities."""
        if self.installer is None:
            raise MethodAuthorityInvalid(
                "a reviewed sandboxed package installer is required"
            )
        if len({item.package for item in proposals}) != len(proposals):
            raise MethodAuthorityInvalid("package proposals must be unique")
        authorities = tuple(self._acquire_one(proposal) for proposal in proposals)
        return self.verify(authorities)

    def verify(
        self, authorities: Sequence[MethodAuthority]
    ) -> tuple[MethodAuthority, ...]:
        """Reopen source, record, DESCRIPTION, and installed bytes exactly."""
        verified: list[MethodAuthority] = []
        for authority in authorities:
            self._verify_authority(authority)
            verified.append(authority)
        try:
            _require_closed_graph(tuple(verified))
        except AuthorityValidationError as error:
            raise MethodAuthorityInvalid(str(error)) from error
        try:
            entries = tuple(os.scandir(self.root))
        except OSError as error:
            raise MethodAuthorityInvalid(
                "managed package projection is missing"
            ) from error
        expected = {item.proposal.package for item in verified}
        observed = {
            entry.name for entry in entries if entry.is_dir(follow_symlinks=False)
        }
        if observed != expected or any(
            entry.is_symlink() or not entry.is_dir(follow_symlinks=False)
            for entry in entries
        ):
            raise MethodAuthorityInvalid("managed package projection is not closed")
        return tuple(verified)

    def _acquire_one(self, proposal: MethodAuthorityProposal) -> MethodAuthority:
        with _thread_lock(self.store_root, proposal.package):
            lock = self.files.open_lock(
                Path("authorities/locks") / f"{proposal.package}.lock"
            )
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                existing = self._try_record(proposal)
                if existing is not None:
                    self._verify_authority(existing)
                    return existing
                _reject_alternate_version(self.root, proposal)
                fetched = self.fetcher.fetch(
                    proposal.source_url, max_bytes=MAX_SOURCE_BYTES
                )
                require_source_identity(proposal, fetched.data, fetched.final_url)
                _inspect_archive(fetched.data)
                source_rel = (
                    Path("authorities/sources")
                    / proposal.source_sha256
                    / Path(urlsplit(proposal.source_url).path).name
                )
                self.files.persist_exact(source_rel, fetched.data)
                return self._install(proposal, source_rel)
            except MethodAuthorityInvalid:
                raise
            except AuthorityValidationError as error:
                raise MethodAuthorityInvalid(str(error)) from error
            except (
                OSError,
                ValueError,
                tarfile.TarError,
                subprocess.TimeoutExpired,
            ) as error:
                raise MethodAuthorityInvalid(
                    "package authority transaction failed"
                ) from error
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
                os.close(lock)

    def _install(
        self, proposal: MethodAuthorityProposal, source_rel: Path
    ) -> MethodAuthority:
        shadow_parent = self.store_root / "authorities/.shadow"
        shadow_parent.mkdir(parents=True, exist_ok=True)
        shadow = Path(
            tempfile.mkdtemp(prefix=f"{proposal.package}-", dir=shadow_parent)
        )
        library = shadow / "library"
        library.mkdir()
        source = self.store_root / source_rel
        try:
            assert self.installer is not None
            result = self.installer.execute(
                (
                    str(self.r_executable),
                    "CMD",
                    "INSTALL",
                    f"--library={library}",
                    str(source),
                ),
                cwd=shadow,
                env={
                    "HOME": str(shadow),
                    "LANG": "C",
                    "LC_ALL": "C",
                    "R_LIBS_USER": str(self.root),
                },
            )
            if result.return_code != 0:
                raise MethodAuthorityInvalid("R package installation failed")
            package = library / proposal.package
            fields = _description(package / "DESCRIPTION")
            if (
                fields.get("Package") != proposal.package
                or fields.get("Version") != proposal.version
                or fields.get("License") != proposal.description_license
            ):
                raise MethodAuthorityInvalid(
                    "installed DESCRIPTION does not match proposal"
                )
            if not _license_matches(proposal.license, fields["License"]):
                raise MethodAuthorityInvalid("installed license is not SPDX-bound")
            _require_description_dependencies(fields, proposal)
            description_hash = _sha256((package / "DESCRIPTION").read_bytes())
            tree_hash = _tree_digest(package)
            self.root.mkdir(parents=True, exist_ok=True)
            destination = self.root / proposal.package
            if destination.exists() or destination.is_symlink():
                if _tree_digest(destination) != tree_hash:
                    raise MethodAuthorityInvalid(
                        "unrecorded package tree conflicts with reinstall"
                    )
            else:
                os.replace(package, destination)
            _freeze_tree(destination)
            if _tree_digest(destination) != tree_hash:
                raise MethodAuthorityInvalid("published package tree identity changed")
            authority = MethodAuthority(
                proposal=proposal,
                installed_tree_sha256=tree_hash,
                source_relative_path=source_rel,
                package_relative_path=Path("authorities/r-library") / proposal.package,
                description_sha256=description_hash,
                observed_license=fields["License"],
                observed_at=datetime.now(UTC),
            )
            self.files.persist_exact(
                _record_path(proposal), _authority_bytes(authority)
            )
            return authority
        finally:
            shutil.rmtree(shadow, ignore_errors=True)

    def _try_record(self, proposal: MethodAuthorityProposal) -> MethodAuthority | None:
        try:
            return self._load_record(proposal)
        except FileNotFoundError:
            return None

    def _load_record(self, proposal: MethodAuthorityProposal) -> MethodAuthority:
        authority = MethodAuthority.model_validate_json(
            self.files.read(_record_path(proposal)), strict=True
        )
        if authority.proposal != proposal:
            raise MethodAuthorityInvalid(
                "package proposal conflicts with stored authority"
            )
        return authority

    def _verify_authority(self, authority: MethodAuthority) -> None:
        record = self._load_record(authority.proposal)
        if record != authority or record.content_hash() != authority.content_hash():
            raise MethodAuthorityInvalid("package authority record changed")
        source = self.files.read(authority.source_relative_path)
        if _sha256(source) != authority.proposal.source_sha256:
            raise MethodAuthorityInvalid("package source identity changed")
        package = self.store_root / authority.package_relative_path
        description = package / "DESCRIPTION"
        if _sha256(description.read_bytes()) != authority.description_sha256:
            raise MethodAuthorityInvalid("package DESCRIPTION identity changed")
        try:
            observed_tree = _tree_digest(package)
        except AuthorityValidationError as error:
            raise MethodAuthorityInvalid(str(error)) from error
        if observed_tree != authority.installed_tree_sha256:
            raise MethodAuthorityInvalid("installed package tree identity changed")


def _record_path(proposal: MethodAuthorityProposal) -> Path:
    return Path("authorities/records") / f"{proposal.package}-{proposal.version}.json"


def _authority_bytes(authority: MethodAuthority) -> bytes:
    return (
        json.dumps(
            authority.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode()
