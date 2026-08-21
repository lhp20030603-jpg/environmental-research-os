"""Managed package authority integration with the trusted R runner."""

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from envresearch.econometrics._managed_r_validation import tree_digest
from envresearch.econometrics.contracts import ResourceBudget
from envresearch.econometrics.installed_package_authority import (
    InstalledPackageAuthority,
)
from envresearch.econometrics.managed_r_library import MethodAuthorityInvalid
from envresearch.econometrics.method_authority import (
    MethodAuthority,
    MethodAuthorityProposal,
    PackageRequirement,
)
from envresearch.econometrics.r_evidence import GeneratedRScript, RCommandResult
from envresearch.econometrics.r_runtime import (
    RPackageAuthorityInvalid,
    TrustedLocalRRunner,
)


class RecordingExecutor:
    """No-process executor recording the environment used for analysis."""

    def __init__(self, version: str = "4.4.3") -> None:
        self.environments: list[dict[str, str]] = []
        self.version = version

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
        self.environments.append(dict(env))
        output = (
            f"R version {self.version}\n".encode()
            if argv[-1] == "--version"
            else b"ok\n"
        )
        return RCommandResult(return_code=0, stdout=output, stderr=b"")


class RecordingManagedLibrary:
    """Reauthentication seam for execution-boundary tests."""

    def __init__(self, root: Path, *, fail_on: int | None = None) -> None:
        self.root = root
        self.root.mkdir(parents=True)
        self.calls = 0
        self.fail_on = fail_on

    def verify(self, authorities: Sequence[object]) -> tuple[object, ...]:
        self.calls += 1
        if self.calls == self.fail_on:
            raise MethodAuthorityInvalid("installed package tree identity changed")
        return tuple(authorities)


def _authority() -> MethodAuthority:
    proposal = MethodAuthorityProposal(
        package="did",
        version="2.3.0",
        source_url="https://cran.r-project.org/src/contrib/did_2.3.0.tar.gz",
        source_sha256="a" * 64,
        license="GPL-3.0-only",
        description_license="GPL-3",
        dependencies=(PackageRequirement(package="R", version="4.4.3", base=True),),
    )
    return MethodAuthority(
        proposal=proposal,
        installed_tree_sha256="b" * 64,
        source_relative_path=Path("authorities/sources/" + "a" * 64 + "/did.tar.gz"),
        package_relative_path=Path("authorities/r-library/did"),
        description_sha256="c" * 64,
        observed_license="GPL-3",
        observed_at=datetime(2026, 8, 12, tzinfo=UTC),
    )


def _installed_authority() -> InstalledPackageAuthority:
    return InstalledPackageAuthority(
        schema_version="econometrics.frozen-r-package.v1",
        authority_kind="frozen-local-tree",
        package="did",
        version="2.3.0",
        observed_license="GPL-2",
        description_sha256="d" * 64,
        installed_tree_sha256="e" * 64,
        package_relative_path=Path("authorities/frozen-r-pack/library/did"),
        dependencies=(PackageRequirement(package="R", version="4.4.3", base=True),),
        r_version="4.4.3",
        pack_hash="f" * 64,
        observed_at=datetime(2026, 8, 12, tzinfo=UTC),
    )


def _runner(
    tmp_path: Path,
    library: RecordingManagedLibrary,
    *,
    runtime_version: str = "4.4.3",
    authority: MethodAuthority | InstalledPackageAuthority | None = None,
) -> tuple[TrustedLocalRRunner, GeneratedRScript, RecordingExecutor]:
    package = library.root / "did"
    (package / "R").mkdir(parents=True)
    (package / "DESCRIPTION").write_text(
        "Package: did\nVersion: 2.3.0\nLicense: GPL-3\n", encoding="utf-8"
    )
    (package / "R/did.rdb").write_bytes(b"reviewed")
    digest = tree_digest(package)
    selected = authority or _authority()
    selected = selected.model_copy(update={"installed_tree_sha256": digest})
    executable = tmp_path / "bin/Rscript"
    executable.parent.mkdir()
    executable.write_bytes(b"reviewed-rscript")
    executable.chmod(0o555)
    workspace = tmp_path / "workspace"
    script_path = workspace / "generated/analysis.R"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("result <- 1\n", encoding="utf-8")
    script_path.chmod(0o444)
    script = GeneratedRScript(
        template_id="did-event-study-v1",
        path=script_path,
        sha256=hashlib.sha256(script_path.read_bytes()).hexdigest(),
    )
    executor = RecordingExecutor(runtime_version)
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
        managed_library=library,
        package_authorities=(selected,),
    )
    return runner, script, executor


def test_runner_binds_managed_package_authority_before_and_after_run(
    tmp_path: Path,
) -> None:
    """Only the exact managed library is exposed and sealed into evidence."""
    library = RecordingManagedLibrary(tmp_path / "store/authorities/r-library")
    runner, script, executor = _runner(tmp_path, library)

    evidence = runner.run(script)

    execution_library = Path(executor.environments[-1]["R_LIBS_USER"])
    assert execution_library.parent.name == ".r-library-snapshots"
    assert executor.environments[-1]["R_LIBS_SITE"] == str(execution_library)
    assert not execution_library.exists()
    assert evidence.package_authorities == runner.package_authorities
    assert library.calls == 2


def test_runner_rejects_package_tree_change_after_execution(tmp_path: Path) -> None:
    """A package mutation during R execution cannot produce accepted evidence."""
    library = RecordingManagedLibrary(
        tmp_path / "store/authorities/r-library", fail_on=2
    )
    runner, script, _ = _runner(tmp_path, library)

    with pytest.raises(RPackageAuthorityInvalid, match="package authority") as error:
        runner.run(script)
    assert error.value.code == "R_PACKAGE_AUTHORITY_INVALID"


def test_runner_requires_exact_r_base_dependency_version(tmp_path: Path) -> None:
    """A near-prefix runtime version cannot satisfy a base dependency."""
    library = RecordingManagedLibrary(tmp_path / "store/authorities/r-library")
    runner, script, _ = _runner(tmp_path, library, runtime_version="4.4.30")

    with pytest.raises(RPackageAuthorityInvalid, match="base dependency"):
        runner.run(script)


def test_runner_records_and_reauthenticates_frozen_tree_authority(
    tmp_path: Path,
) -> None:
    library = RecordingManagedLibrary(tmp_path / "store/authorities/frozen-r-library")
    authority = _installed_authority()
    runner, script, _ = _runner(tmp_path, library, authority=authority)

    evidence = runner.run(script)

    assert evidence.package_authorities == runner.package_authorities
    assert evidence.package_authorities[0].ref().artifact_id == (
        "r-package-authority-did-2.3.0"
    )
    assert library.calls == 2
