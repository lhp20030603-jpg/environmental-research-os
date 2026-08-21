"""Fail-closed frozen R pack authority boundary coverage."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from envresearch.econometrics._managed_r_validation import tree_digest
from envresearch.econometrics.frozen_r_library import (
    FrozenRLibrary,
    _closed_packages,
    _dependency_names,
    _pack_hash,
    _record_path,
    _source_roots,
)
from envresearch.econometrics.installed_package_authority import observed_now


def _source(tmp_path: Path, name: str = "fixture", extra: str = "") -> Path:
    root = (tmp_path / "source").resolve()
    package = root / name
    package.mkdir(parents=True)
    (package / "DESCRIPTION").write_text(
        f"Package: {name}\nVersion: 1.0.0\nLicense: GPL-3\n{extra}",
        encoding="utf-8",
    )
    (package / "R").mkdir()
    (package / "R/code").write_text("fixture <- TRUE\n", encoding="utf-8")
    return root


def _pack(tmp_path: Path):
    source = _source(tmp_path)
    frozen = FrozenRLibrary((tmp_path / "pack").resolve())
    authorities = frozen.freeze(
        (source,), required_packages=("fixture",), r_version="4.4.3"
    )
    return frozen, authorities


def test_frozen_library_requires_absolute_non_symlink_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute and non-symlink"):
        FrozenRLibrary(Path("relative"))
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="absolute and non-symlink"):
        FrozenRLibrary(link.absolute())


def test_frozen_library_rejects_empty_duplicate_and_disagreeing_records(
    tmp_path: Path,
) -> None:
    frozen, authorities = _pack(tmp_path)
    with pytest.raises(ValueError, match="authority is empty"):
        frozen.verify(())
    with pytest.raises(ValueError, match="duplicated"):
        frozen.verify((authorities[0], authorities[0]))
    changed = authorities[0].model_copy(update={"pack_hash": "0" * 64})
    with pytest.raises(ValueError, match="pack identity changed"):
        frozen.verify((changed,))


def test_frozen_library_rejects_invalid_entry_and_open_projection(
    tmp_path: Path,
) -> None:
    frozen, authorities = _pack(tmp_path)
    os.chmod(frozen.root, 0o700)
    invalid = frozen.root / "invalid"
    invalid.write_text("not a package", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid entry"):
        frozen.verify(authorities)
    invalid.unlink()
    extra = frozen.root / "extra"
    extra.mkdir()
    with pytest.raises(ValueError, match="not closed"):
        frozen.verify(authorities)


def test_frozen_library_rejects_changed_record(tmp_path: Path) -> None:
    frozen, authorities = _pack(tmp_path)
    changed = authorities[0].model_copy(update={"observed_at": observed_now()})
    with pytest.raises(ValueError, match="record changed"):
        frozen.verify((changed,))


def test_frozen_library_rejects_invalid_load_hash_and_missing_projection(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="pack hash is invalid"):
        FrozenRLibrary((tmp_path / "missing").resolve()).load("bad")
    with pytest.raises(ValueError, match="projection is missing"):
        FrozenRLibrary((tmp_path / "missing").resolve()).load("0" * 64)


def test_frozen_source_roots_reject_empty_relative_and_symlink(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="are required"):
        _source_roots(())
    with pytest.raises(ValueError, match="source library is invalid"):
        _source_roots((Path("relative"),))
    source = _source(tmp_path)
    link = tmp_path / "source-link"
    link.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match="source library is invalid"):
        _source_roots((link.absolute(),))


@pytest.mark.parametrize(
    ("description", "message"),
    (
        ("Package: fixture\nVersion: 1.0.0\n", "metadata is incomplete"),
        (
            "Package: another\nVersion: 1.0.0\nLicense: GPL-3\n",
            "identity conflicts",
        ),
    ),
)
def test_frozen_package_metadata_must_be_complete_and_match_directory(
    description: str, message: str, tmp_path: Path
) -> None:
    source = _source(tmp_path)
    (source / "fixture/DESCRIPTION").write_text(description, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        _closed_packages((source,), ("fixture",), "4.4.3")


def test_frozen_dependency_syntax_is_closed() -> None:
    with pytest.raises(ValueError, match="dependency syntax is invalid"):
        _dependency_names({"Imports": "valid, !!!"})


def test_frozen_library_rejects_changed_description_under_resealed_tree(
    tmp_path: Path,
) -> None:
    frozen, authorities = _pack(tmp_path)
    authority = authorities[0]
    package = frozen.root / authority.package
    description = package / "DESCRIPTION"
    description.chmod(0o600)
    description.write_text(
        "Package: fixture\nVersion: 1.0.0\nLicense: MIT\n", encoding="utf-8"
    )
    new_tree = tree_digest(package)
    new_pack = _pack_hash("4.4.3", (("fixture", "1.0.0", new_tree),))
    resealed = authority.model_copy(
        update={"installed_tree_sha256": new_tree, "pack_hash": new_pack}
    )
    record = frozen.store_root / _record_path("fixture")
    record.chmod(0o600)
    record.write_text(resealed.model_dump_json(), encoding="utf-8")

    with pytest.raises(ValueError, match="DESCRIPTION changed"):
        frozen.verify((resealed,))
