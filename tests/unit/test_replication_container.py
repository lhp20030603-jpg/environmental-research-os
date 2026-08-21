"""Tests for the mandatory isolated Tier-2 container boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import envresearch.replication.container as container_module
from envresearch.replication._runtime_identity import ProcessIdentity
from envresearch.replication._runtime_owner import RuntimeOwnership
from envresearch.replication.container import (
    CommandExecution,
    ContainerPlan,
    DockerEngine,
    PodmanEngine,
    allocate_output_namespace,
)
from envresearch.replication.contracts import ContainerRuntimeProfile, ReplicationBudget

SHA256 = "a" * 64


class FakeCommandExecutor:
    """Records container commands without invoking a local runtime."""

    def __init__(self, *, return_code: int = 0) -> None:
        self.return_code = return_code
        self.commands: list[tuple[str, ...]] = []
        self.inactivity_limits: list[int] = []

    def execute(
        self,
        argv: tuple[str, ...],
        *,
        inactivity_seconds: int,
        on_progress=None,
        on_started=None,
        resource_sampler=None,
    ) -> CommandExecution:
        del on_progress, resource_sampler
        self.commands.append(argv)
        self.inactivity_limits.append(inactivity_seconds)
        if on_started is not None:
            cidfile = Path(argv[argv.index("--cidfile") + 1])
            cidfile.write_text("c" * 64, encoding="ascii")
            on_started(ProcessIdentity(4242, 4242, "b" * 64))
        now = datetime.now(UTC)
        return CommandExecution(
            argv=argv,
            return_code=self.return_code,
            stdout="container output",
            stderr="",
            started_at=now,
            finished_at=now,
            peak_memory_bytes=1024,
            storage_bytes=2048,
        )


class FakeContainerControl:
    def cleanup(self, executable: str, owner: RuntimeOwnership) -> None:
        assert executable in {"docker", "podman"} or executable.startswith("/")
        assert owner.container_name.startswith("envresearch-")


def profile(*, image: str | None = None) -> ContainerRuntimeProfile:
    return ContainerRuntimeProfile.model_construct(
        profile_id="r-did-v1",
        image_digest=image or "ghcr.io/envresearch/r-did@sha256:" + SHA256,
        nonroot_uid_gid="10001:10001",
    )


def budget() -> ReplicationBudget:
    return ReplicationBudget(
        max_download_bytes=1024,
        max_storage_bytes=2048,
        max_memory_bytes=1024,
        inactivity_seconds=15,
    )


def trusted_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    repository = tmp_path / "run-root"
    input_root = repository / "artifacts/replication/acquired/archive/approval"
    output_root = repository / "artifacts/replication/runs/approval/attempt"
    input_root.mkdir(parents=True)
    output_root.mkdir(parents=True)
    return repository, input_root, output_root


def author_plan(tmp_path: Path) -> ContainerPlan:
    _, input_root, output_root = trusted_workspace(tmp_path)
    return ContainerPlan.for_author_reproduction(
        profile(), input_root, output_root, budget()
    )


def test_r_container_plan_has_no_network_and_no_repository_mount(
    tmp_path: Path,
) -> None:
    """The author plan exposes only its derived input and output roots."""
    repository, input_root, output_root = trusted_workspace(tmp_path)
    plan = ContainerPlan.for_author_reproduction(
        profile(), input_root, output_root, budget()
    )
    argv = DockerEngine(FakeCommandExecutor()).build_argv(plan)

    assert "--network=none" in argv
    assert "--read-only" in argv
    assert "/tmp:rw,noexec,nosuid,size=512m" in argv
    assert any("dst=/input,readonly" in item for item in argv)
    assert any("dst=/output" in item and "readonly" not in item for item in argv)
    assert str(repository / "src") not in " ".join(argv)


def test_generated_files_are_materialized_under_the_read_only_input_mount(
    tmp_path: Path,
) -> None:
    """A generated command target must exist beneath the container's input mount."""
    _, input_root, output_root = trusted_workspace(tmp_path)
    plan = ContainerPlan(
        image_digest=profile().image_digest,
        user=profile().nonroot_uid_gid,
        input_root=input_root,
        output_root=output_root,
        argv=("Rscript", "/input/.generated/derived_did.R"),
        output_namespace="derived-did-event-study",
        budget=budget(),
        generated_files={"derived_did.R": "print('diagnostic')"},
    )

    argv = DockerEngine(FakeCommandExecutor()).build_argv(plan)

    assert "/input/.generated/derived_did.R" in argv
    assert (
        input_root / ".generated/derived_did.R"
    ).read_text() == "print('diagnostic')"


def test_output_namespace_allocation_rejects_untrusted_output_roots(
    tmp_path: Path,
) -> None:
    """Namespace allocation must not create directories outside the run root."""
    untrusted = tmp_path / "untrusted-output"
    untrusted.mkdir()

    with pytest.raises(ValueError, match="trusted run output base"):
        allocate_output_namespace(untrusted, "derived-did-event-study")

    assert not (untrusted / "derived-did-event-study").exists()


@pytest.mark.parametrize("engine", [DockerEngine, PodmanEngine])
def test_each_supported_engine_uses_the_same_isolation_contract(
    tmp_path: Path, engine: type[DockerEngine | PodmanEngine]
) -> None:
    """Docker and Podman must have no policy divergence."""
    argv = engine(FakeCommandExecutor()).build_argv(author_plan(tmp_path))

    assert argv[1:3] == ("run", "--cidfile")
    assert "--rm" not in argv
    assert "--name" in argv
    assert "--network=none" in argv
    assert argv[argv.index("--user") + 1] == "10001:10001"
    assert argv[argv.index("--memory") + 1] == "1024"
    assert argv[argv.index("--storage-opt") + 1] == "size=2048"
    assert sum(item.startswith("type=bind,") for item in argv) == 2


def test_container_preflight_rejects_an_engine_without_a_pinned_digest() -> None:
    """A bypassed runtime profile cannot turn a mutable tag into an execution."""
    with pytest.raises(ValueError, match="image digest"):
        DockerEngine(FakeCommandExecutor()).preflight(profile(image="rocker/r-ver:4.4"))


def test_plan_rejects_overlapping_or_symlinked_mount_roots(tmp_path: Path) -> None:
    """Roots that could confuse the read/write boundary fail before execution."""
    _, input_root, output_root = trusted_workspace(tmp_path)
    with pytest.raises(ValueError, match="trusted run output base"):
        ContainerPlan.for_author_reproduction(
            profile(), input_root, input_root, budget()
        )

    link = input_root.parent / "input-link"
    link.symlink_to(input_root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        ContainerPlan.for_author_reproduction(profile(), link, output_root, budget())
    link.unlink()


def test_preflight_and_run_use_only_the_injected_executor(tmp_path: Path) -> None:
    """Runtime checks and execution emit bounded, hashable observations."""
    executor = FakeCommandExecutor()
    engine = DockerEngine(executor, container_control=FakeContainerControl())
    runtime = engine.preflight(profile())
    result = engine.run(author_plan(tmp_path))

    assert runtime.engine == "docker"
    assert runtime.version == "container output"
    assert result.exit_status == 0
    assert len(result.stdout_sha256) == 64
    assert executor.inactivity_limits == [60, 15]
    assert executor.commands[1][0] == "docker"


def test_unavailable_engine_fails_closed_before_a_run(tmp_path: Path) -> None:
    """A failed engine probe is not replaced by a local command runner."""
    engine = DockerEngine(FakeCommandExecutor(return_code=127))
    with pytest.raises(RuntimeError, match="unavailable"):
        engine.preflight(profile())
    assert engine.build_argv(author_plan(tmp_path))[0] == "docker"


def test_workspace_validation_is_stable_across_changed_working_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changing CWD cannot authorize either the repository or a host directory."""
    repository, input_root, output_root = trusted_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    plan = ContainerPlan.for_author_reproduction(
        profile(), input_root, output_root, budget()
    )
    assert DockerEngine(FakeCommandExecutor()).build_argv(plan)[0] == "docker"

    with pytest.raises(ValueError, match="trusted acquired input base"):
        ContainerPlan.for_author_reproduction(
            profile(),
            repository,
            output_root,
            budget(),
        )
    arbitrary = tmp_path / "ordinary-host"
    arbitrary.mkdir()
    with pytest.raises(ValueError, match="trusted acquired input base"):
        ContainerPlan.for_author_reproduction(
            profile(), arbitrary, output_root, budget()
        )


def test_plan_rejects_repository_and_host_paths_after_a_caller_attempts_forgery(
    tmp_path: Path,
) -> None:
    """No public base selector can authorize source, tests, or host directories."""
    repository, _, _ = trusted_workspace(tmp_path)
    source_root = repository / "src/envresearch"
    test_root = repository / "tests/unit"
    source_root.mkdir(parents=True)
    test_root.mkdir(parents=True)

    with pytest.raises(ValueError, match="trusted acquired input base"):
        ContainerPlan.for_author_reproduction(
            profile(), source_root, test_root, budget()
        )
    host_root = tmp_path / "ordinary-host"
    host_root.mkdir()
    with pytest.raises(ValueError, match="trusted acquired input base"):
        ContainerPlan.for_author_reproduction(profile(), host_root, test_root, budget())


def test_mutating_a_module_root_global_cannot_authorize_host_mounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller-visible mutable root must not become mount authority."""
    trusted_workspace(tmp_path)
    forged_repository = tmp_path / "forged-repository"
    input_root = forged_repository / "artifacts/replication/acquired/hash/approval"
    output_root = tmp_path / "other-root/artifacts/replication/runs/approval/run-001"
    input_root.mkdir(parents=True)
    output_root.mkdir(parents=True)
    monkeypatch.setattr(
        container_module, "_REPOSITORY_ROOT", forged_repository, raising=False
    )

    with pytest.raises(ValueError, match="share one run authority"):
        ContainerPlan.for_author_reproduction(
            profile(), input_root, output_root, budget()
        )


def test_plan_rejects_mount_delimiters_and_result_truncates_logs(
    tmp_path: Path,
) -> None:
    """Mount serialization and log hashing have no attacker-controlled escape hatch."""
    _, input_root, output_root = trusted_workspace(tmp_path)
    unsafe_root = input_root.parent / "package,readonly=true"
    unsafe_root.mkdir()
    with pytest.raises(ValueError, match="mount delimiter"):
        ContainerPlan.for_author_reproduction(
            profile(), unsafe_root, output_root, budget()
        )
    unsafe_root.rmdir()

    class OversizedLogExecutor(FakeCommandExecutor):
        def execute(
            self, argv: tuple[str, ...], *, inactivity_seconds: int, **kwargs
        ) -> CommandExecution:
            result = super().execute(
                argv, inactivity_seconds=inactivity_seconds, **kwargs
            )
            return CommandExecution(
                argv=result.argv,
                return_code=result.return_code,
                stdout="a" * 9,
                stderr="b" * 9,
                started_at=result.started_at,
                finished_at=result.finished_at,
                peak_memory_bytes=result.peak_memory_bytes,
                storage_bytes=result.storage_bytes,
            )

    result = DockerEngine(
        OversizedLogExecutor(),
        container_control=FakeContainerControl(),
        max_log_bytes=8,
    ).run(
        ContainerPlan.for_author_reproduction(
            profile(), input_root, output_root, budget()
        )
    )
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True


def test_result_over_budget_fails_closed(tmp_path: Path) -> None:
    """Observed memory or writable storage beyond the plan budget is rejected."""

    class OverBudgetExecutor(FakeCommandExecutor):
        def execute(
            self, argv: tuple[str, ...], *, inactivity_seconds: int, **kwargs
        ) -> CommandExecution:
            result = super().execute(
                argv, inactivity_seconds=inactivity_seconds, **kwargs
            )
            return CommandExecution(
                argv=result.argv,
                return_code=result.return_code,
                stdout=result.stdout,
                stderr=result.stderr,
                started_at=result.started_at,
                finished_at=result.finished_at,
                peak_memory_bytes=1025,
                storage_bytes=2048,
            )

    with pytest.raises(RuntimeError, match="memory budget"):
        DockerEngine(
            OverBudgetExecutor(), container_control=FakeContainerControl()
        ).run(author_plan(tmp_path))

    _, input_root, output_root = trusted_workspace(tmp_path / "forged")
    forged_budget = ReplicationBudget.model_construct(
        max_download_bytes=1024,
        max_storage_bytes=0,
        max_memory_bytes=1024,
        inactivity_seconds=15,
    )
    with pytest.raises(ValueError, match="max_storage_bytes"):
        ContainerPlan.for_author_reproduction(
            profile(), input_root, output_root, forged_budget
        )
