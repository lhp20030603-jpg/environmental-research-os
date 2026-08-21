"""Fail-closed descriptor boundaries for immutable econometrics evidence."""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from envresearch.econometrics import _managed_r_validation as managed_validation
from envresearch.econometrics import _r_owned_files, _store_files
from envresearch.econometrics._file_evidence import read_regular
from envresearch.econometrics._managed_r_validation import (
    AuthorityValidationError,
    inspect_archive,
    require_closed_graph,
    require_description_dependencies,
    tree_digest,
    tree_entries,
)
from envresearch.econometrics._r_owned_files import (
    RRuntimeInvalid,
    open_owned_file,
    publish_owned_file,
)
from envresearch.econometrics._store_files import StoreFiles


def test_store_rejects_non_regular_evidence_and_lock_leaves(tmp_path: Path) -> None:
    files = StoreFiles(tmp_path / "store")
    (files.root / "evidence").mkdir(parents=True)
    (files.root / "locks/item.lock").mkdir(parents=True)

    with pytest.raises(OSError, match="not a regular file"):
        files.read(Path("evidence"))
    with pytest.raises(OSError):
        files.open_lock(Path("locks/item.lock"))


def test_store_failed_atomic_write_cleans_up_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = StoreFiles(tmp_path / "store")
    original = _store_files._write_descriptor

    def interrupted(_descriptor: int, _data: bytes) -> None:
        raise OSError("interrupted evidence write")

    monkeypatch.setattr(_store_files, "_write_descriptor", interrupted)
    with pytest.raises(OSError, match="interrupted evidence write"):
        files.write(Path("evidence/value.bin"), b"sealed")
    assert not tuple(files.root.rglob("*.tmp"))

    monkeypatch.setattr(_store_files, "_write_descriptor", original)
    files.write(Path("evidence/value.bin"), b"sealed")
    assert files.read(Path("evidence/value.bin")) == b"sealed"


def test_store_unlink_is_idempotent_and_paths_cannot_escape(tmp_path: Path) -> None:
    files = StoreFiles(tmp_path / "store")
    files.ensure_directory(Path("evidence"))
    files.unlink(Path("evidence/missing.bin"))

    with pytest.raises(ValueError, match="canonical and relative"):
        files.write(Path("../outside.bin"), b"escape")


def test_regular_evidence_rejects_directory_and_short_race_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(OSError, match="not a regular file"):
        read_regular(directory)

    evidence = tmp_path / "evidence.bin"
    evidence.write_bytes(b"sealed")
    original_read = os.read
    calls = 0

    def shortened(descriptor: int, size: int) -> bytes:
        nonlocal calls
        calls += 1
        return b"" if calls == 1 else original_read(descriptor, size)

    monkeypatch.setattr(os, "read", shortened)
    with pytest.raises(OSError, match="size changed"):
        read_regular(evidence)


def test_owned_runtime_publish_failure_is_clean_and_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def interrupted(*args: object, **kwargs: object) -> None:
        raise OSError("interrupted rename")

    monkeypatch.setattr(_r_owned_files.os, "replace", interrupted)
    with pytest.raises(RRuntimeInvalid, match="hierarchy is not trustworthy"):
        publish_owned_file(workspace, "owned", "script.R", b"sealed", 0o444)
    assert not tuple(workspace.rglob("*.tmp"))


def test_owned_runtime_open_rejects_missing_directory_leaf_and_digest(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(RRuntimeInvalid, match="hierarchy is not trustworthy"):
        open_owned_file(workspace, "missing", "runtime", "0" * 64, 100)

    owned = workspace / "owned"
    owned.mkdir()
    with pytest.raises(RRuntimeInvalid, match="hierarchy is not trustworthy"):
        open_owned_file(workspace, "owned", "missing", "0" * 64, 100)
    (owned / "directory").mkdir()
    with pytest.raises(RRuntimeInvalid, match="bounded regular file"):
        open_owned_file(workspace, "owned", "directory", "0" * 64, 100)

    runtime = owned / "runtime"
    runtime.write_bytes(b"reviewed")
    with pytest.raises(RRuntimeInvalid, match="identity changed"):
        open_owned_file(workspace, "owned", "runtime", "0" * 64, 100)
    with pytest.raises(RRuntimeInvalid, match="bounded regular file"):
        open_owned_file(
            workspace,
            "owned",
            "runtime",
            hashlib.sha256(b"reviewed").hexdigest(),
            1,
        )


def _archive(name: str | None = None, data: bytes = b"") -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        if name is not None:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


def test_managed_r_archive_bounds_reject_empty_and_expanding_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(AuthorityValidationError, match="member count"):
        inspect_archive(_archive())

    monkeypatch.setattr(managed_validation, "MAX_UNPACKED_BYTES", 1)
    with pytest.raises(AuthorityValidationError, match="archive expansion"):
        inspect_archive(_archive("package/data.bin", b"xx"))


def test_managed_r_tree_requires_available_nonempty_regular_hierarchy(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(AuthorityValidationError, match="unavailable"):
        tree_entries(missing)

    regular = tmp_path / "regular"
    regular.write_bytes(b"not a package tree")
    with pytest.raises(AuthorityValidationError, match="unavailable"):
        tree_entries(regular)

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(AuthorityValidationError, match="tree is empty"):
        tree_digest(empty)

    nonregular = tmp_path / "nonregular"
    nonregular.mkdir()
    os.mkfifo(nonregular / "pipe")
    with pytest.raises(AuthorityValidationError, match="nonregular"):
        tree_entries(nonregular)


def test_managed_r_description_and_dependency_authority_fail_closed(
    tmp_path: Path,
) -> None:
    with pytest.raises(AuthorityValidationError, match="DESCRIPTION is missing"):
        managed_validation.description(tmp_path / "missing-DESCRIPTION")

    required = SimpleNamespace(package="declared", version="1", base=False)
    proposal = SimpleNamespace(
        package="consumer", version="1", dependencies=(required,)
    )
    with pytest.raises(AuthorityValidationError, match="graph is not declared"):
        require_description_dependencies({"Imports": "different"}, proposal)

    invalid_base = SimpleNamespace(package="not-base", version="1", base=True)
    invalid_base_authority = SimpleNamespace(
        proposal=SimpleNamespace(
            package="consumer", version="1", dependencies=(invalid_base,)
        )
    )
    with pytest.raises(AuthorityValidationError, match="marked as base"):
        require_closed_graph((invalid_base_authority,))

    with pytest.raises(AuthorityValidationError, match="authority is missing"):
        require_closed_graph((SimpleNamespace(proposal=proposal),))
