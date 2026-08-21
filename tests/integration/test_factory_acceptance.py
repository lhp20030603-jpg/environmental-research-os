"""End-to-end acceptance for one exact governed research-factory run."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest
from test_factory_run import connected_factory

from envresearch.factory import cli as factory_cli
from envresearch.models.artifact import ArtifactRef

TreeEntry = tuple[str, bytes | None, int, int, int]


def _tree_state(root: Path) -> dict[str, TreeEntry]:
    """Snapshot path inventory, bytes, type, owner, mode, and link count."""
    state: dict[str, TreeEntry] = {}
    for path in (root, *sorted(root.rglob("*"))):
        metadata = path.lstat()
        if stat.S_ISREG(metadata.st_mode):
            kind, content = "file", path.read_bytes()
        elif stat.S_ISDIR(metadata.st_mode):
            kind, content = "directory", None
        elif stat.S_ISLNK(metadata.st_mode):
            kind, content = "symlink", os.readlink(path).encode()
        else:
            kind, content = "other", None
        relative = "." if path == root else path.relative_to(root).as_posix()
        state[relative] = (
            kind,
            content,
            metadata.st_uid,
            stat.S_IMODE(metadata.st_mode),
            metadata.st_nlink,
        )
    return state


def test_synthetic_release_assembles_exact_factory_run(tmp_path: Path) -> None:
    """Catch the connected handoff losing either exact accepted input reference."""
    fixture = connected_factory(tmp_path)
    try:
        reference = fixture.service.assemble(fixture.design_ref, fixture.release_ref)
        reopened = fixture.service.status(reference)

        assert reopened.state == "promotion-required"
        assert reopened.run.release_ref == fixture.release_ref
        assert reopened.run.design_ref == fixture.design_ref
    finally:
        fixture.close()


@pytest.mark.skipif(
    "ENVRESEARCH_V04_ACCEPTANCE_ROOT" not in os.environ,
    reason="formal sealed V0.4 root is operator supplied",
)
def test_sealed_release_assembles_exact_factory_run(tmp_path: Path) -> None:
    """Catch a reviewed exact release/design pair failing factory composition."""
    reviewed = Path(os.environ["ENVRESEARCH_V04_ACCEPTANCE_ROOT"]).resolve(strict=True)
    originals = {
        name: (reviewed / name).resolve(strict=True)
        for name in ("research", "v031", "paper", "factory")
    }
    original_state = {name: _tree_state(root) for name, root in originals.items()}
    copies = {name: tmp_path / name for name in originals}
    for name, source in originals.items():
        shutil.copytree(source, copies[name])
    design_ref = ArtifactRef.model_validate_json(
        (reviewed / "design-reference.json").read_bytes()
    )
    release_ref = ArtifactRef.model_validate_json(
        (reviewed / "release-reference.json").read_bytes()
    )
    try:
        factory = factory_cli.service_for_roots(
            copies["research"],
            copies["v031"],
            copies["paper"],
            copies["factory"],
            create=True,
        )
        try:
            reference = factory.assemble(design_ref, release_ref)
            reopened = factory.status(reference)

            assert reopened.state == "promotion-required"
            assert reopened.run.release_ref == release_ref
            assert reopened.run.design_ref == design_ref
        finally:
            factory_cli._close(factory)
    finally:
        assert {
            name: _tree_state(root) for name, root in originals.items()
        } == original_state
