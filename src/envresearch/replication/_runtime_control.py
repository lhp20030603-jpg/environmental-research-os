"""Private reviewed executable and local daemon authority validation."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from envresearch.storage.hashing import sha256_file

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = {"identity", "executable", "executable_sha256", "endpoint"}


@dataclass(frozen=True, slots=True)
class ReviewedEngineConfiguration:
    identity: str
    executable: Path
    executable_sha256: str
    endpoint: str


@dataclass(frozen=True, slots=True)
class EngineBinding:
    configuration: ReviewedEngineConfiguration
    device: int
    inode: int
    owner_uid: int
    mode: int

    @property
    def environment(self) -> Mapping[str, str]:
        endpoint_key = (
            "DOCKER_HOST"
            if self.configuration.identity == "docker"
            else "CONTAINER_HOST"
        )
        return MappingProxyType(
            {"LANG": "C", "LC_ALL": "C", endpoint_key: self.configuration.endpoint}
        )

    def require_current(self) -> None:
        observed = bind_engine(self.configuration)
        if observed != self:
            raise ValueError("reviewed container executable identity changed")


def restore_engine_configuration(value: object) -> ReviewedEngineConfiguration:
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise TypeError("reviewed engine configuration schema is invalid")
    identity = value.get("identity")
    executable = value.get("executable")
    digest = value.get("executable_sha256")
    endpoint = value.get("endpoint")
    if type(identity) is not str or identity not in {"docker", "podman"}:
        raise ValueError("reviewed engine identity is invalid")
    if type(executable) is not str or not executable:
        raise TypeError("reviewed executable path is invalid")
    path = Path(executable)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("reviewed executable must be an absolute canonical path")
    if type(digest) is not str or not _SHA256.fullmatch(digest):
        raise ValueError("reviewed executable digest is invalid")
    if type(endpoint) is not str:
        raise TypeError("reviewed local endpoint is invalid")
    _require_local_endpoint(endpoint)
    return ReviewedEngineConfiguration(identity, path, digest, endpoint)


def bind_engine(configuration: ReviewedEngineConfiguration) -> EngineBinding:
    path = configuration.executable
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("reviewed executable must be a nonsymlink regular file")
    mode = stat.S_IMODE(metadata.st_mode)
    if metadata.st_uid not in {0, os.geteuid()}:
        raise ValueError("reviewed executable owner is not trusted")
    if mode & 0o022 or not mode & 0o111 or not os.access(path, os.X_OK):
        raise ValueError("reviewed executable mode is not trusted")
    if sha256_file(path) != configuration.executable_sha256:
        raise ValueError("reviewed executable digest differs from configuration")
    return EngineBinding(
        configuration,
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        mode,
    )


def _require_local_endpoint(endpoint: str) -> None:
    if not endpoint.startswith("unix://"):
        raise ValueError("container endpoint must be an explicit local unix socket")
    path = endpoint.removeprefix("unix://")
    socket = Path(path)
    if (
        not path
        or not socket.is_absolute()
        or ".." in socket.parts
        or any(character in endpoint for character in ("\x00", "\n", "\r"))
    ):
        raise ValueError("container endpoint must be an explicit local unix socket")
