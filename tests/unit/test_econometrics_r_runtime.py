"""Trusted local R execution-boundary tests."""

import hashlib
import os
from collections.abc import Mapping
from pathlib import Path

import pytest

from envresearch.econometrics.contracts import ResourceBudget
from envresearch.econometrics.r_evidence import GeneratedRScript, RCommandResult
from envresearch.econometrics.r_runtime import (
    RExecutionFailed,
    RRuntimeInvalid,
    TrustedLocalRRunner,
)


class RecordingExecutor:
    """Deterministic no-process executor for boundary tests."""

    def __init__(self) -> None:
        self.calls: list[
            tuple[tuple[str, ...], Path, dict[str, str], int, int, int, tuple[int, ...]]
        ] = []
        self.fd_bytes: tuple[bytes, ...] = ()
        self.on_analysis: object | None = None
        self.analysis_result = RCommandResult(return_code=0, stdout=b"ok\n", stderr=b"")

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
        del max_workspace_bytes
        self.calls.append(
            (
                argv,
                cwd,
                dict(env),
                timeout_seconds,
                max_output_bytes,
                executable_fd,
                pass_fds,
            )
        )
        self.fd_bytes = tuple(_read_fd(fd) for fd in pass_fds)
        if argv[-1] == "--version":
            return RCommandResult(
                return_code=0,
                stdout=b"R scripting front-end version 4.5.1\n",
                stderr=b"",
            )
        if callable(self.on_analysis):
            self.on_analysis()
        return self.analysis_result


def _read_fd(descriptor: int) -> bytes:
    """Read one inherited descriptor without changing its final offset."""
    offset = os.lseek(descriptor, 0, os.SEEK_CUR)
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while chunk := os.read(descriptor, 1024 * 1024):
        chunks.append(chunk)
    os.lseek(descriptor, offset, os.SEEK_SET)
    return b"".join(chunks)


def _budget(**updates: int) -> ResourceBudget:
    payload = {
        "inactivity_seconds": 30,
        "max_output_bytes": 1024,
        "max_workspace_bytes": 16_384,
    }
    payload.update(updates)
    return ResourceBudget.model_validate(payload)


def _executable(tmp_path: Path) -> tuple[Path, str]:
    executable = tmp_path / "bin" / "Rscript"
    executable.parent.mkdir()
    executable.write_bytes(b"reviewed-rscript-binary")
    executable.chmod(0o555)
    return executable, hashlib.sha256(executable.read_bytes()).hexdigest()


def _script(workspace: Path, body: str = "result <- 1\n") -> GeneratedRScript:
    path = workspace / "generated" / "analysis.R"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.chmod(0o644)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o444)
    return GeneratedRScript(
        template_id="did-event-study-v1",
        path=path,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _runner(
    tmp_path: Path, executor: RecordingExecutor, body: str = "result <- 1\n"
) -> tuple[TrustedLocalRRunner, Path, GeneratedRScript]:
    executable, digest = _executable(tmp_path)
    workspace = tmp_path / "workspace"
    script = _script(workspace, body)
    runner = TrustedLocalRRunner.review(
        executable=executable,
        expected_sha256=digest,
        workspace=workspace,
        executor=executor,
        budget=_budget(),
        approved_scripts={script.template_id: script.sha256},
    )
    return runner, workspace, script


@pytest.mark.parametrize("kind", ["relative", "symlink", "writable"])
def test_runtime_rejects_unreviewed_executable_identity(
    tmp_path: Path, kind: str
) -> None:
    """Only one absolute, regular, immutable executable identity is allowed."""
    executable, digest = _executable(tmp_path)
    if kind == "relative":
        executable = Path("Rscript")
    elif kind == "symlink":
        alias = tmp_path / "Rscript-alias"
        alias.symlink_to(executable)
        executable = alias
    else:
        executable.chmod(0o775)

    with pytest.raises(RRuntimeInvalid, match="reviewed regular executable"):
        TrustedLocalRRunner.review(
            executable=executable,
            expected_sha256=digest,
            workspace=tmp_path / "workspace",
            executor=RecordingExecutor(),
            budget=_budget(),
            approved_scripts={"did-event-study-v1": "a" * 64},
        )


def test_runner_uses_owned_snapshots_and_minimal_environment(tmp_path: Path) -> None:
    """Exact owned bytes and no inherited R settings reach the executor."""
    executor = RecordingExecutor()
    runner, workspace, script = _runner(tmp_path, executor)

    evidence = runner.run(script)

    argv, cwd, environment, timeout, max_output, executable_fd, pass_fds = (
        executor.calls[-1]
    )
    assert argv[0] == str(runner.identity.executable)
    assert argv[1] == "--vanilla"
    assert argv[2].startswith("/dev/fd/")
    assert len(pass_fds) == 2
    assert executable_fd == pass_fds[0]
    assert cwd == workspace
    assert set(environment) == {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "R_LIBS_USER",
        "TMPDIR",
    }
    assert environment["HOME"].startswith(str(workspace))
    assert timeout == 30
    assert max_output == 1024
    assert evidence.return_code == 0


@pytest.mark.parametrize(
    "body",
    [
        "source('author.R')\n",
        "install.packages('did')\n",
        "download.file('https://example.org/x', 'x')\n",
        "system('curl example.org')\n",
        "socketConnection(host='example.org')\n",
        "eval(parse(text='1+1'))\n",
        "get('system')('whoami')\n",
        "httr::GET('https://example.org')\n",
        "remotes::install_github('owner/repo')\n",
    ],
)
def test_runner_rejects_forbidden_script_capabilities(
    tmp_path: Path, body: str
) -> None:
    """Approved script bytes still cannot request external capabilities."""
    runner, _, script = _runner(tmp_path, RecordingExecutor(), body)

    with pytest.raises(RRuntimeInvalid, match="forbidden R capability"):
        runner.run(script)


def test_runner_executes_snapshots_after_source_replacement(tmp_path: Path) -> None:
    """Source path replacement after launch cannot alter executed bytes."""
    executor = RecordingExecutor()
    runner, _, script = _runner(tmp_path, executor)
    source_executable = runner.identity.source_executable

    def replace_sources() -> None:
        source_executable.chmod(0o755)
        source_executable.write_bytes(b"replacement")
        script.path.chmod(0o644)
        script.path.write_text("replacement <- TRUE\n", encoding="utf-8")

    executor.on_analysis = replace_sources
    evidence = runner.run(script)

    assert evidence.return_code == 0
    assert Path(evidence.argv[0]) != source_executable
    assert Path(evidence.argv[2]) != script.path
    assert hashlib.sha256(executor.fd_bytes[1]).hexdigest() == script.sha256


def test_runner_rejects_script_hash_mismatch(tmp_path: Path) -> None:
    """A generated script cannot change after its manifest is sealed."""
    runner, _, script = _runner(tmp_path, RecordingExecutor())
    script.path.chmod(0o644)
    script.path.write_text("result <- 2\n", encoding="utf-8")

    with pytest.raises(RRuntimeInvalid, match="script identity changed"):
        runner.run(script)


def test_runner_rejects_unapproved_script_hash(tmp_path: Path) -> None:
    """An arbitrary script cannot claim repository-generated provenance."""
    runner, workspace, _ = _runner(tmp_path, RecordingExecutor())
    unapproved = _script(workspace, "result <- 999\n")

    with pytest.raises(RRuntimeInvalid, match="not an approved generated script"):
        runner.run(unapproved)


@pytest.mark.parametrize("kind", ["dotdot", "ancestor-symlink"])
def test_runner_rejects_script_path_confinement_bypasses(
    tmp_path: Path, kind: str
) -> None:
    """Traversal and ancestor symlinks cannot select external script bytes."""
    runner, workspace, approved = _runner(tmp_path, RecordingExecutor())
    outside = tmp_path / "outside.R"
    outside.write_bytes(approved.path.read_bytes())
    if kind == "dotdot":
        path = workspace / ".." / "outside.R"
    else:
        link = workspace / "link"
        link.symlink_to(tmp_path, target_is_directory=True)
        path = link / "outside.R"
    forged = approved.model_copy(update={"path": path})

    with pytest.raises(RRuntimeInvalid, match="outside the owned workspace"):
        runner.run(forged)


@pytest.mark.parametrize("directory", ["runtime", "execution"])
def test_runner_rejects_snapshot_parent_symlink(tmp_path: Path, directory: str) -> None:
    """Owned execution snapshots never publish through redirected parents."""
    executable, digest = _executable(tmp_path)
    workspace = tmp_path / "workspace"
    script = _script(workspace)
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / directory).symlink_to(outside, target_is_directory=True)
    if directory == "runtime":
        with pytest.raises(RRuntimeInvalid, match="owned runtime hierarchy"):
            TrustedLocalRRunner.review(
                executable=executable,
                expected_sha256=digest,
                workspace=workspace,
                executor=RecordingExecutor(),
                budget=_budget(),
                approved_scripts={script.template_id: script.sha256},
            )
    else:
        runner = TrustedLocalRRunner.review(
            executable=executable,
            expected_sha256=digest,
            workspace=workspace,
            executor=RecordingExecutor(),
            budget=_budget(),
            approved_scripts={script.template_id: script.sha256},
        )
        with pytest.raises(RRuntimeInvalid, match="owned runtime hierarchy"):
            runner.run(script)
    assert tuple(outside.iterdir()) == ()


@pytest.mark.parametrize("directory", ["home", "library", "tmp"])
def test_runner_rejects_redirected_environment_directories(
    tmp_path: Path, directory: str
) -> None:
    """HOME, library, and temporary directories stay inside the workspace."""
    executable, digest = _executable(tmp_path)
    workspace = tmp_path / "workspace"
    script = _script(workspace)
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / directory).symlink_to(outside, target_is_directory=True)

    with pytest.raises(RRuntimeInvalid, match="owned runtime hierarchy"):
        TrustedLocalRRunner.review(
            executable=executable,
            expected_sha256=digest,
            workspace=workspace,
            executor=RecordingExecutor(),
            budget=_budget(),
            approved_scripts={script.template_id: script.sha256},
        )
    assert tuple(outside.iterdir()) == ()


def test_runner_closes_runtime_fd_when_environment_setup_fails(tmp_path: Path) -> None:
    """A rejected environment cannot leak the authenticated executable FD."""
    executable, digest = _executable(tmp_path)
    workspace = tmp_path / "workspace"
    script = _script(workspace)
    (workspace / "home").write_text("not-a-directory", encoding="utf-8")
    before = len(tuple(Path("/dev/fd").iterdir()))

    with pytest.raises(RRuntimeInvalid, match="owned runtime hierarchy"):
        TrustedLocalRRunner.review(
            executable=executable,
            expected_sha256=digest,
            workspace=workspace,
            executor=RecordingExecutor(),
            budget=_budget(),
            approved_scripts={script.template_id: script.sha256},
        )

    assert len(tuple(Path("/dev/fd").iterdir())) == before


def test_runner_closes_script_fd_when_environment_setup_fails(tmp_path: Path) -> None:
    runner, workspace, script = _runner(tmp_path, RecordingExecutor())
    (workspace / "home").rmdir()
    (workspace / "home").write_text("not-a-directory", encoding="utf-8")
    before = len(tuple(Path("/dev/fd").iterdir()))

    with pytest.raises(RRuntimeInvalid, match="owned runtime hierarchy"):
        runner.run(script)

    assert len(tuple(Path("/dev/fd").iterdir())) == before


def test_runner_bounds_logs_and_workspace_growth(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    runner, workspace, script = _runner(tmp_path, executor)
    executor.analysis_result = RCommandResult(
        return_code=0, stdout=b"x" * 1025, stderr=b""
    )
    with pytest.raises(RExecutionFailed, match="output budget"):
        runner.run(script)

    executor.analysis_result = RCommandResult(return_code=0, stdout=b"ok", stderr=b"")
    executor.on_analysis = lambda: (workspace / "large.bin").write_bytes(b"x" * 20_000)
    with pytest.raises(RExecutionFailed, match="workspace budget"):
        runner.run(script)


def test_runner_maps_timeout_and_nonzero_exit(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    runner, _, script = _runner(tmp_path, executor)

    def timeout() -> None:
        raise TimeoutError("inactive")

    executor.on_analysis = timeout
    with pytest.raises(RExecutionFailed, match="inactivity limit"):
        runner.run(script)

    executor.on_analysis = None
    executor.analysis_result = RCommandResult(return_code=3, stdout=b"", stderr=b"bad")
    with pytest.raises(RExecutionFailed, match="exit code 3"):
        runner.run(script)
