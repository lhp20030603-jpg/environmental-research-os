"""Private immutable evidence values re-exported by the container boundary."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class ResourceMeasurement:
    """One explicit measured or unknown runtime resource sample."""

    memory_bytes: int | None
    storage_bytes: int | None
    oom_killed: bool | None

    def __post_init__(self) -> None:
        for value in (self.memory_bytes, self.storage_bytes):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("resource measurement must be nonnegative or unknown")
        if self.oom_killed is not None and type(self.oom_killed) is not bool:
            raise ValueError("OOM measurement must be an exact boolean or unknown")

    @property
    def status(self) -> Literal["measured", "unknown"]:
        if None in (self.memory_bytes, self.storage_bytes, self.oom_killed):
            return "unknown"
        return "measured"


@dataclass(frozen=True, slots=True)
class CommandExecution:
    """Raw output from the injected container-runtime executor."""

    argv: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str
    started_at: datetime
    finished_at: datetime
    peak_memory_bytes: int | None
    storage_bytes: int | None
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    resource_status: Literal["measured", "unknown"] = "measured"
    oom_killed: bool | None = False


@dataclass(frozen=True, slots=True)
class RuntimeObservation:
    """Hash-bound evidence that a specific engine version was available."""

    engine: str
    executable_sha256: str
    endpoint: str
    version: str
    started_at: datetime
    finished_at: datetime
    stdout_sha256: str
    stderr_sha256: str
    stdout_truncated: bool
    stderr_truncated: bool
    peak_memory_bytes: int | None
    storage_bytes: int | None
    resource_status: Literal["measured", "unknown"] = "measured"
    oom_killed: bool | None = False


@dataclass(frozen=True, slots=True)
class ContainerResult:
    """Bounded, hash-bound execution outcome consumed by the ledger."""

    engine: str
    image_digest: str
    exit_status: int
    started_at: datetime
    finished_at: datetime
    stdout_sha256: str
    stderr_sha256: str
    stdout_truncated: bool
    stderr_truncated: bool
    peak_memory_bytes: int | None
    storage_bytes: int | None
    resource_status: Literal["measured", "unknown"] = "measured"
    oom_killed: bool | None = False
