"""Private fail-closed container and process-group cleanup mechanics."""

from typing import Protocol

from envresearch.replication._runtime_owner import RuntimeOwnership


class ContainerCleanupError(RuntimeError):
    """The runtime boundary could not prove all tracked execution was removed."""


class _ContainerControl(Protocol):
    def authenticate(self, executable: str, owner: RuntimeOwnership) -> bool: ...

    def cleanup(self, executable: str, owner: RuntimeOwnership) -> None: ...


class _ProcessGroupControl(Protocol):
    def authenticate(self, owner: RuntimeOwnership) -> bool: ...

    def cleanup(self, owner: RuntimeOwnership) -> None: ...


def cleanup_container(
    control: _ContainerControl | None,
    executable: str,
    owner: RuntimeOwnership,
    *,
    cause: BaseException | None = None,
) -> None:
    if control is None:
        error = ContainerCleanupError("container cleanup control is unavailable")
        if cause is not None:
            raise error from cause
        raise error
    try:
        control.cleanup(executable, owner)
    except Exception as error:
        raise ContainerCleanupError(
            "container containment cleanup verification failed"
        ) from error


def contain_runtime(
    process_control: _ProcessGroupControl | None,
    container_control: _ContainerControl | None,
    executable: str,
    engine: str,
    owner: RuntimeOwnership | None,
    names: tuple[str, ...],
) -> None:
    del names
    if (
        owner is None
        or owner.engine != engine
        or process_control is None
        or container_control is None
    ):
        raise ContainerCleanupError("runtime owner cannot be contained")
    try:
        process_control.authenticate(owner)
        container_control.authenticate(executable, owner)
    except Exception as error:
        raise ContainerCleanupError(
            "runtime containment identity verification failed"
        ) from error
    cleanup_container(container_control, executable, owner)
    try:
        process_control.cleanup(owner)
    except Exception as error:
        raise ContainerCleanupError(
            "runtime process containment verification failed"
        ) from error
