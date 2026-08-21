"""Private exact argv construction for one prepared container launch."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from envresearch.replication._container_identity import launch_arguments, prepare_launch
from envresearch.replication._container_support import _materialize_generated_files
from envresearch.replication._runtime_owner import RuntimeLaunchIdentity

if TYPE_CHECKING:
    from envresearch.replication.container import ContainerPlan


def prepare_run(
    executable: str,
    engine: str,
    plan: ContainerPlan,
    tmpfs: str,
    nonce_factory: Callable[[], str] | None,
) -> tuple[RuntimeLaunchIdentity, tuple[str, ...]]:
    """Prepare one launch and its exact argv from the same sealed identity."""
    keywords = {"nonce_factory": nonce_factory} if nonce_factory is not None else {}
    launch = prepare_launch(plan, engine, **keywords)
    return launch, build_run_argv(executable, plan, launch, tmpfs)


def build_run_argv(
    executable: str,
    plan: ContainerPlan,
    launch: RuntimeLaunchIdentity,
    tmpfs: str,
) -> tuple[str, ...]:
    """Construct exact isolation args from the already sealed launch."""
    _materialize_generated_files(plan)
    argv = [
        executable,
        "run",
        *launch_arguments(launch),
        "--network=none",
        "--user",
        plan.user,
        "--read-only",
        "--tmpfs",
        tmpfs,
        "--memory",
        str(plan.budget.max_memory_bytes),
        "--storage-opt",
        f"size={plan.budget.max_storage_bytes}",
        "--mount",
        f"type=bind,src={plan.input_root},dst=/input,readonly",
        "--mount",
        f"type=bind,src={plan.output_root},dst=/output",
    ]
    for name, value in sorted(plan.environment.items()):
        argv.extend(("--env", f"{name}={value}"))
    argv.extend((plan.image_digest, *plan.argv))
    return tuple(argv)
