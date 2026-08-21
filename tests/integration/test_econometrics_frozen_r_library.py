"""Content-addressed admission of one reviewed local R package projection."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

import pytest

from envresearch.econometrics._store_files import StoreFiles
from envresearch.econometrics.cli import _service_for
from envresearch.econometrics.contracts import ResourceBudget
from envresearch.econometrics.frozen_r_library import FrozenRLibrary
from envresearch.econometrics.installed_package_authority import (
    InstalledPackageAuthority,
)
from envresearch.econometrics.r_evidence import GeneratedRScript, RCommandResult
from envresearch.econometrics.r_runtime import TrustedLocalRRunner


def _package(
    library: Path,
    package: str,
    version: str,
    *,
    imports: str = "",
    license_name: str = "MIT",
) -> Path:
    root = library / package
    (root / "R").mkdir(parents=True)
    fields = [
        f"Package: {package}",
        f"Version: {version}",
        f"License: {license_name}",
    ]
    if imports:
        fields.append(f"Imports: {imports}")
    (root / "DESCRIPTION").write_text("\n".join(fields) + "\n", encoding="utf-8")
    (root / "R" / f"{package}.rdb").write_bytes(package.encode())
    return root


def test_freeze_copies_exact_closed_dependency_projection(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _package(source, "methodpkg", "1.2.0", imports="helper (>= 1.0), stats")
    _package(source, "helper", "1.1.0")
    frozen = FrozenRLibrary(tmp_path / "store")

    authorities = frozen.freeze(
        (source.resolve(),), required_packages=("methodpkg",), r_version="4.4.3"
    )

    assert tuple(item.package for item in authorities) == ("helper", "methodpkg")
    assert all(isinstance(item, InstalledPackageAuthority) for item in authorities)
    assert all(item.authority_kind == "frozen-local-tree" for item in authorities)
    assert frozen.verify(authorities) == authorities
    assert {path.name for path in frozen.root.iterdir()} == {"helper", "methodpkg"}
    source.joinpath("methodpkg/R/methodpkg.rdb").write_bytes(b"changed source")
    assert frozen.verify(authorities) == authorities

    reopened = FrozenRLibrary(tmp_path / "store").load(authorities[0].pack_hash)
    assert reopened == authorities
    with pytest.raises(ValueError, match="pack hash"):
        FrozenRLibrary(tmp_path / "store").load("0" * 64)


def test_freeze_accepts_compact_r_dependency_version_syntax(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _package(source, "methodpkg", "1.2.0", imports="R(>= 4.4.0), stats")

    authorities = FrozenRLibrary(tmp_path / "store").freeze(
        (source.resolve(),), required_packages=("methodpkg",), r_version="4.4.3"
    )

    dependency = authorities[0].dependencies[0]
    assert (dependency.package, dependency.version, dependency.base) == (
        "R",
        "4.4.3",
        True,
    )


def test_freeze_rejects_missing_dependency_and_tree_tamper(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    _package(missing, "methodpkg", "1.2.0", imports="absent")
    with pytest.raises(ValueError, match="dependency.*absent"):
        FrozenRLibrary(tmp_path / "missing-store").freeze(
            (missing.resolve(),),
            required_packages=("methodpkg",),
            r_version="4.4.3",
        )

    source = tmp_path / "source"
    _package(source, "methodpkg", "1.2.0")
    frozen = FrozenRLibrary(tmp_path / "store")
    authorities = frozen.freeze(
        (source.resolve(),), required_packages=("methodpkg",), r_version="4.4.3"
    )
    (frozen.root / "methodpkg/R/methodpkg.rdb").chmod(0o644)
    (frozen.root / "methodpkg/R/methodpkg.rdb").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="tree identity"):
        frozen.verify(authorities)


def test_load_rebuilds_record_semantics_from_description(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _package(source, "methodpkg", "1.2.0", imports="helper")
    _package(source, "helper", "1.0.0")
    frozen = FrozenRLibrary(tmp_path / "store")
    authorities = frozen.freeze(
        (source.resolve(),), required_packages=("methodpkg",), r_version="4.4.3"
    )
    record = frozen.store_root / ("authorities/frozen-r-pack/records/methodpkg.json")
    payload = InstalledPackageAuthority.model_validate_json(record.read_bytes())
    record.chmod(0o600)
    record.write_text(
        payload.model_copy(update={"dependencies": ()}).model_dump_json(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="record semantics"):
        frozen.load(authorities[0].pack_hash)


def test_freeze_rejects_ambiguous_package_versions(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    _package(first, "methodpkg", "1.2.0")
    _package(second, "methodpkg", "9.0.0")

    with pytest.raises(ValueError, match="ambiguous"):
        FrozenRLibrary(tmp_path / "store").freeze(
            (first.resolve(), second.resolve()),
            required_packages=("methodpkg",),
            r_version="4.4.3",
        )


def test_production_service_reopens_only_the_reviewed_pack_hash(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _package(source, "methodpkg", "1.2.0")
    pack_root = (tmp_path / "pack").resolve()
    authorities = FrozenRLibrary(pack_root).freeze(
        (source.resolve(),), required_packages=("methodpkg",), r_version="4.4.3"
    )

    service = _service_for(
        (tmp_path / "run").resolve(),
        r_executable=(tmp_path / "Rscript").resolve(),
        r_sha256="a" * 64,
        frozen_pack_root=pack_root,
        frozen_pack_hash=authorities[0].pack_hash,
    )

    assert service.backend.package_authorities == authorities  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="pack hash"):
        _service_for(
            (tmp_path / "other-run").resolve(),
            r_executable=(tmp_path / "Rscript").resolve(),
            r_sha256="a" * 64,
            frozen_pack_root=pack_root,
            frozen_pack_hash="0" * 64,
        )


def test_freeze_retry_requires_the_same_runtime_and_source_identity(
    tmp_path: Path,
) -> None:
    first, changed = tmp_path / "first", tmp_path / "changed"
    _package(first, "methodpkg", "1.2.0")
    changed_package = _package(changed, "methodpkg", "1.2.0")
    changed_package.joinpath("R/methodpkg.rdb").write_bytes(b"different tree")
    frozen = FrozenRLibrary(tmp_path / "store")
    frozen.freeze(
        (first.resolve(),), required_packages=("methodpkg",), r_version="4.4.3"
    )

    with pytest.raises(ValueError, match="existing frozen R pack conflicts"):
        frozen.freeze(
            (changed.resolve(),),
            required_packages=("methodpkg",),
            r_version="4.4.3",
        )
    with pytest.raises(ValueError, match="existing frozen R pack conflicts"):
        frozen.freeze(
            (first.resolve(),),
            required_packages=("methodpkg",),
            r_version="4.5.0",
        )


def test_interrupted_first_publication_recovers_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    _package(source, "methodpkg", "1.2.0")
    frozen = FrozenRLibrary(tmp_path / "store")
    original = StoreFiles.persist_exact
    calls = 0

    def fail_first(files: StoreFiles, path: Path, data: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected record publication crash")
        original(files, path, data)

    monkeypatch.setattr(StoreFiles, "persist_exact", fail_first)
    with pytest.raises(OSError, match="publication crash"):
        frozen.freeze(
            (source.resolve(),),
            required_packages=("methodpkg",),
            r_version="4.4.3",
        )
    monkeypatch.setattr(StoreFiles, "persist_exact", original)

    authorities = frozen.freeze(
        (source.resolve(),), required_packages=("methodpkg",), r_version="4.4.3"
    )
    assert frozen.verify(authorities) == authorities


class _MutatingExecutor:
    def __init__(self, source_leaf: Path) -> None:
        self.source_leaf = source_leaf
        self.consumed = b""

    def execute(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
        max_output_bytes: int,
        max_workspace_bytes: int,
        executable_fd: int,
        pass_fds: tuple[int, ...],
    ) -> RCommandResult:
        del cwd, timeout_seconds, max_output_bytes, max_workspace_bytes
        del executable_fd, pass_fds
        if argv[-1] == "--version":
            return RCommandResult(
                return_code=0, stdout=b"R version 4.4.3\n", stderr=b""
            )
        original = self.source_leaf.read_bytes()
        self.source_leaf.chmod(0o644)
        self.source_leaf.write_bytes(b"UNREVIEWED")
        try:
            self.consumed = (
                Path(env["R_LIBS_USER"])
                .joinpath("methodpkg/R/methodpkg.rdb")
                .read_bytes()
            )
        finally:
            self.source_leaf.write_bytes(original)
            self.source_leaf.chmod(0o444)
        return RCommandResult(return_code=0, stdout=b"ok\n", stderr=b"")


def test_execution_consumes_an_owned_package_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _package(source, "methodpkg", "1.2.0")
    frozen = FrozenRLibrary(tmp_path / "store")
    authorities = frozen.freeze(
        (source.resolve(),), required_packages=("methodpkg",), r_version="4.4.3"
    )
    executable = tmp_path / "Rscript"
    executable.write_bytes(b"reviewed-rscript")
    executable.chmod(0o555)
    workspace = tmp_path / "workspace"
    script_path = workspace / "generated/analysis.R"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("result <- 1\n", encoding="utf-8")
    script_path.chmod(0o444)
    script = GeneratedRScript(
        template_id="owned-snapshot-v1",
        path=script_path,
        sha256=hashlib.sha256(script_path.read_bytes()).hexdigest(),
    )
    executor = _MutatingExecutor(frozen.root / "methodpkg/R/methodpkg.rdb")
    runner = TrustedLocalRRunner.review(
        executable=executable,
        expected_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        workspace=workspace,
        executor=executor,
        budget=ResourceBudget(
            inactivity_seconds=30,
            max_output_bytes=1024,
            max_workspace_bytes=16_384,
        ),
        approved_scripts={script.template_id: script.sha256},
        managed_library=frozen,
        package_authorities=authorities,
    )

    runner.run(script)

    assert executor.consumed == b"methodpkg"


def test_execution_rejects_symlinked_snapshot_staging(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _package(source, "methodpkg", "1.2.0")
    frozen = FrozenRLibrary(tmp_path / "store")
    authorities = frozen.freeze(
        (source.resolve(),), required_packages=("methodpkg",), r_version="4.4.3"
    )
    executable = tmp_path / "Rscript"
    executable.write_bytes(b"reviewed-rscript")
    executable.chmod(0o555)
    workspace = tmp_path / "work/run"
    script_path = workspace / "generated/analysis.R"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("result <- 1\n", encoding="utf-8")
    script_path.chmod(0o444)
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace.parent / ".r-library-snapshots").symlink_to(outside)
    executor = _MutatingExecutor(frozen.root / "methodpkg/R/methodpkg.rdb")
    runner = TrustedLocalRRunner.review(
        executable=executable,
        expected_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        workspace=workspace,
        executor=executor,
        budget=ResourceBudget(
            inactivity_seconds=30,
            max_output_bytes=1024,
            max_workspace_bytes=16_384,
        ),
        approved_scripts={
            "owned-snapshot-v1": hashlib.sha256(script_path.read_bytes()).hexdigest()
        },
        managed_library=frozen,
        package_authorities=authorities,
    )
    script = GeneratedRScript(
        template_id="owned-snapshot-v1",
        path=script_path,
        sha256=hashlib.sha256(script_path.read_bytes()).hexdigest(),
    )

    with pytest.raises(ValueError, match="staging directory"):
        runner.run(script)
    assert not tuple(outside.iterdir())
