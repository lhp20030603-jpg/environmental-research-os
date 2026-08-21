"""Exact input, system, and cross-root inventory boundaries."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation import snapshots as snapshots_module
from envresearch.personal_validation.contracts import (
    PERSONAL_ATTEMPT_ROOTS_V1,
    AttemptRootInventory,
    InputSnapshot,
    SystemSnapshot,
)
from envresearch.personal_validation.errors import PersonalValidationIntegrityInvalid
from envresearch.personal_validation.snapshots import (
    require_correct_stop_inventory,
    snapshot_inputs,
    snapshot_roots,
    snapshot_system,
)


def _roots(tmp_path: Path) -> dict[str, Path]:
    roots = {name: tmp_path / name for name in PERSONAL_ATTEMPT_ROOTS_V1}
    for root in roots.values():
        root.mkdir(parents=True)
    return roots


def _write(root: Path, relative: str, data: bytes = b"result") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _protocol_ref() -> ArtifactRef:
    return ArtifactRef(
        artifact_id="personal-validation-protocol-v1",
        artifact_version=1,
        content_hash="a" * 64,
    )


def test_input_snapshot_covers_modes_symlinks_untracked_and_effective_config(
    tmp_path: Path,
) -> None:
    """Skipping an untracked path, link, directory, or mode breaks the snapshot."""
    root = tmp_path / "inputs"
    directory = root / "fixtures"
    directory.mkdir(parents=True)
    effective = root / "effective-config.json"
    effective.write_bytes(b'{"method":"hedonic"}')
    untracked = root / "untracked.csv"
    untracked.write_bytes(b"x,y\n1,2\n")
    os.chmod(untracked, 0o640)
    (root / "config-link").symlink_to("effective-config.json")

    snapshot = snapshot_inputs(root)

    entries = {entry.logical_name: entry for entry in snapshot.entries}
    assert {entry.kind for entry in snapshot.entries} >= {
        "file",
        "directory",
        "symlink",
    }
    assert "effective-config.json" in entries
    assert entries["untracked.csv"].mode == 0o640
    assert (
        entries["untracked.csv"].sha256
        == hashlib.sha256(untracked.read_bytes()).hexdigest()
    )
    assert entries["config-link"].symlink_target == "effective-config.json"
    assert entries["."].mode == stat.S_IMODE(root.lstat().st_mode)

    forged = snapshot.model_dump(mode="python")
    forged["entries"] = tuple(forged["entries"][:-1])
    with pytest.raises(ValidationError, match="identity"):
        InputSnapshot.model_validate(forged)


def test_system_snapshot_binds_commit_tree_manifests_runtime_and_cleanliness(
    tmp_path: Path,
) -> None:
    """Changing execution bytes or a bound field must change system identity."""
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "uv.lock").write_bytes(b"lock-v1")
    (repository / "capabilities.json").write_bytes(b'{"hedonic":"1"}')
    (repository / "methods.json").write_bytes(b'{"primary":"hedonic"}')
    source = repository / "runner.py"
    source.write_bytes(b"print('v1')\n")
    for command in (
        ("git", "init", "-q"),
        ("git", "config", "user.email", "test@example.invalid"),
        ("git", "config", "user.name", "Snapshot Test"),
        ("git", "add", "."),
        ("git", "commit", "-qm", "fixture"),
    ):
        subprocess.run(command, cwd=repository, check=True, capture_output=True)

    first = snapshot_system(
        repository,
        _protocol_ref(),
        capability_manifest=Path("capabilities.json"),
        method_profile=Path("methods.json"),
        runtime_versions=(("python", "3.13-test"),),
    )

    assert first.clean_worktree is True
    assert first.uv_lock_sha256 == hashlib.sha256(b"lock-v1").hexdigest()
    assert (
        first.capability_manifest_sha256
        == hashlib.sha256(b'{"hedonic":"1"}').hexdigest()
    )
    assert (
        first.method_profile_sha256
        == hashlib.sha256(b'{"primary":"hedonic"}').hexdigest()
    )
    assert first.protocol_ref == _protocol_ref()
    assert first.runtime_versions == (("python", "3.13-test"),)

    source.write_bytes(b"print('v2')\n")
    second = snapshot_system(
        repository,
        _protocol_ref(),
        capability_manifest=Path("capabilities.json"),
        method_profile=Path("methods.json"),
        runtime_versions=(("python", "3.13-test"),),
    )
    assert second.clean_worktree is False
    assert second.execution_tree_sha256 != first.execution_tree_sha256
    assert second.snapshot_id != first.snapshot_id

    forged = first.model_dump(mode="python")
    forged["git_commit"] = "f" * 40
    with pytest.raises(ValidationError, match="identity"):
        SystemSnapshot.model_validate(forged)


def test_root_snapshot_records_exact_identity_bytes_and_metadata(
    tmp_path: Path,
) -> None:
    """Dropping file bytes, root inode, link metadata, or root mode is a bug."""
    roots = _roots(tmp_path)
    target = _write(roots["research-citation"], "sources/source.json", b"citation")
    os.chmod(target, 0o640)
    (roots["research-citation"] / "empty").mkdir()
    (roots["research-citation"] / "source-link").symlink_to("sources/source.json")

    inventory = snapshot_roots(roots)

    identities = {item.logical_root: item for item in inventory.root_identities}
    opened = roots["research-citation"].stat()
    assert (
        identities["research-citation"].device,
        identities["research-citation"].inode,
    ) == (opened.st_dev, opened.st_ino)
    entries = {
        (item.logical_root, item.relative_path): item for item in inventory.entries
    }
    source = entries[("research-citation", "sources/source.json")]
    assert source.sha256 == hashlib.sha256(b"citation").hexdigest()
    assert source.size_bytes == len(b"citation")
    assert source.owner == target.lstat().st_uid
    assert source.mode == 0o640
    assert source.link_count == target.lstat().st_nlink
    assert entries[("research-citation", "source-link")].symlink_target == (
        "sources/source.json"
    )
    assert ("research-citation", ".") in entries
    require_correct_stop_inventory(inventory)

    forged = inventory.model_dump(mode="python")
    forged["entries"] = tuple(forged["entries"][:-1])
    with pytest.raises(ValidationError, match="identity"):
        AttemptRootInventory.model_validate(forged)


@pytest.mark.parametrize("omitted", PERSONAL_ATTEMPT_ROOTS_V1)
def test_root_snapshot_rejects_each_omitted_root(tmp_path: Path, omitted: str) -> None:
    """Every versioned logical root is mandatory, not caller-selected."""
    roots = _roots(tmp_path)
    roots.pop(omitted)

    with pytest.raises(
        PersonalValidationIntegrityInvalid, match="exact logical root set"
    ) as captured:
        snapshot_roots(roots)

    assert captured.value.finding_kind == "attempt-root-inventory-incomplete"


def test_root_replacement_changes_inventory_identity(tmp_path: Path) -> None:
    """Replacing a logical root inode cannot preserve the inventory identity."""
    roots = _roots(tmp_path)
    before = snapshot_roots(roots)
    factory = roots["factory"]
    factory.rename(tmp_path / "factory-replaced")
    factory.mkdir()

    after = snapshot_roots(roots)

    before_identity = next(
        item for item in before.root_identities if item.logical_root == "factory"
    )
    after_identity = next(
        item for item in after.root_identities if item.logical_root == "factory"
    )
    assert after_identity.inode != before_identity.inode
    assert after.inventory_id != before.inventory_id


def test_root_snapshot_rejects_two_roles_with_the_same_physical_root(
    tmp_path: Path,
) -> None:
    """Two logical roles sharing one inode are not nine governed roots."""
    roots = _roots(tmp_path)
    roots["research-citation"] = roots["research-design"]

    with pytest.raises(PersonalValidationIntegrityInvalid) as captured:
        snapshot_roots(roots)

    assert captured.value.finding_kind == "attempt-root-authority-overlap"


def test_root_snapshot_rejects_nested_governed_roots(tmp_path: Path) -> None:
    """A child authority nested in another governed root overlaps it."""
    roots = _roots(tmp_path)
    roots["research-citation"].rmdir()
    nested = roots["research-design"] / "citation"
    nested.mkdir()
    roots["research-citation"] = nested

    with pytest.raises(PersonalValidationIntegrityInvalid) as captured:
        snapshot_roots(roots)

    assert captured.value.finding_kind == "attempt-root-authority-overlap"


def test_root_snapshot_rejects_attribute_object_with_extra_root(tmp_path: Path) -> None:
    """Attribute selection must not silently discard an extra authority."""
    roots = _roots(tmp_path)
    supplied = SimpleNamespace(
        **{name.replace("-", "_"): path for name, path in roots.items()},
        extra_root=tmp_path / "hidden",
    )

    with pytest.raises(
        PersonalValidationIntegrityInvalid, match="exact logical root set"
    ) as captured:
        snapshot_roots(supplied)

    assert captured.value.finding_kind == "attempt-root-inventory-incomplete"


def test_root_snapshot_rejects_root_entry_inserted_during_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A root-level addition after listdir must not be omitted."""
    roots = _roots(tmp_path)
    _write(roots["citation-control"], "seed.txt", b"seed")
    real_read = snapshots_module._read_file

    def insert_after_read(parent_fd: int, name: str, before: os.stat_result) -> bytes:
        data = real_read(parent_fd, name, before)
        if name == "seed.txt":
            _write(roots["citation-control"], "inserted.txt", b"late")
        return data

    monkeypatch.setattr(snapshots_module, "_read_file", insert_after_read)
    with pytest.raises(PersonalValidationIntegrityInvalid):
        snapshot_roots(roots)


def test_root_snapshot_rejects_first_root_changed_during_later_root_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed early root must stay exact while later roots are read."""
    roots = _roots(tmp_path)
    _write(roots["factory"], "seed.txt", b"seed")
    real_read = snapshots_module._read_file

    def mutate_first_root(parent_fd: int, name: str, before: os.stat_result) -> bytes:
        data = real_read(parent_fd, name, before)
        if name == "seed.txt":
            _write(roots["citation-control"], "empirical-result.csv", b"late")
        return data

    descriptors = len(os.listdir("/dev/fd"))
    monkeypatch.setattr(snapshots_module, "_read_file", mutate_first_root)
    with pytest.raises(PersonalValidationIntegrityInvalid):
        snapshot_roots(roots)
    assert len(os.listdir("/dev/fd")) == descriptors


# fmt: off
@pytest.mark.parametrize(
    ("logical_root", "relative", "finding_kind"),
    (
        (
            "factory",
            "exit/current/research-factory-run-prepared.json",
            "factory-result-present",
        ),
        (
            "factory",
            "exit/objects/factory-run-deadbeef/v1-deadbeef.json",
            "factory-result-present",
        ),
        ("paper", "exit/current/paper-release-pending.json", "paper-result-present"),
        ("paper", "exit/objects/paper-draft-deadbeef/v1-deadbeef.json", "paper-result-present"),
        ("paper", "exit/current/paper-claim-ledger.json", "paper-result-present"),
        ("paper", "exit/objects/paper-argument-map/v1-deadbeef.json", "paper-result-present"),
        ("paper", "exit/current/paper-revision-deadbeef.json", "paper-result-present"),
        ("paper", "exit/objects/paper-revision-deadbeef/v1-deadbeef.json", "paper-result-present"),
        (
            "local-analysis",
            "analyses/hedonic/current.json",
            "local-analysis-result-present",
        ),
        (
            "local-analysis",
            "analyses/hedonic/evidence/generation-1/outputs/estimate.csv",
            "local-analysis-result-present",
        ),
        (
            "v03",
            "runner/exit/current/analysis-case.json",
            "v03-result-present",
        ),
        (
            "v03",
            "analysis/analyses/case/evidence/generation-1/outputs/estimate.csv",
            "v03-result-present",
        ),
        (
            "v031",
            "evaluator/exit/current/valuation-transition-v031.json",
            "v031-result-present",
        ),
        (
            "v031",
            "analysis/analyses/case/evidence/generation-1/outputs/estimate.csv",
            "v031-result-present",
        ),
        (
            "valuation-control",
            "exports/empirical-result-table.csv",
            "empirical-result-present",
        ),
        (
            "research-design",
            "artifacts/empirical-result-table.csv",
            "empirical-result-present",
        ),
    ),
)
def test_correct_stop_inventory_rejects_each_forbidden_result_namespace(
    tmp_path: Path, logical_root: str, relative: str, finding_kind: str
) -> None:
    """Each forbidden namespace independently invalidates a claimed stop."""
    roots = _roots(tmp_path)
    _write(roots[logical_root], relative)

    inventory = snapshot_roots(roots)
    with pytest.raises(
        PersonalValidationIntegrityInvalid, match="result artifact"
    ) as captured:
        require_correct_stop_inventory(inventory)

    assert captured.value.finding_kind == finding_kind


def test_correct_stop_inventory_rejects_empty_output_namespace(tmp_path: Path) -> None:
    """An empty downstream output namespace is already evidence of overrun."""
    roots = _roots(tmp_path)
    (roots["v031"] / "analysis/analyses/case/outputs").mkdir(parents=True)

    with pytest.raises(PersonalValidationIntegrityInvalid) as captured:
        require_correct_stop_inventory(snapshot_roots(roots))

    assert captured.value.finding_kind == "v031-result-present"
