"""Private strict validation for sealed container runtime observations."""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime, timedelta

from envresearch.replication.container import ContainerEngine, RuntimeObservation

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENGINE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_FIELDS = frozenset(RuntimeObservation.__dataclass_fields__)


def engine_identity(engine: ContainerEngine) -> str:
    """Return a canonical engine identity supplied by the selected boundary."""
    identity = engine.identity
    if type(identity) is not str or not _ENGINE.fullmatch(identity):
        raise ValueError("selected runtime engine identity is invalid")
    return identity


def engine_authority(engine: ContainerEngine) -> tuple[str, str, str]:
    """Return the exact selected engine, executable digest, and local endpoint."""
    identity = engine_identity(engine)
    digest = engine.executable_sha256
    endpoint = engine.endpoint
    if type(digest) is not str or not _SHA256.fullmatch(digest):
        raise ValueError("selected runtime executable digest is invalid")
    _validate_endpoint(endpoint)
    return identity, digest, endpoint


def validate_runtime_observation(
    value: object,
    expected_engine: str,
    expected_executable_sha256: str | None = None,
    expected_endpoint: str | None = None,
) -> RuntimeObservation:
    """Require one exact observation bound to the selected engine."""
    if type(value) is not RuntimeObservation:
        raise TypeError("runtime observation type is invalid")
    observation = value
    if (
        type(expected_engine) is not str
        or not _ENGINE.fullmatch(expected_engine)
        or observation.engine != expected_engine
    ):
        raise ValueError("runtime observation engine differs from selected runtime")
    if (
        type(observation.executable_sha256) is not str
        or not _SHA256.fullmatch(observation.executable_sha256)
        or (
            expected_executable_sha256 is not None
            and observation.executable_sha256 != expected_executable_sha256
        )
    ):
        raise ValueError("runtime executable digest differs from selected runtime")
    _validate_endpoint(observation.endpoint)
    if expected_endpoint is not None and observation.endpoint != expected_endpoint:
        raise ValueError("runtime endpoint differs from selected runtime")
    if (
        type(observation.version) is not str
        or not observation.version
        or observation.version != observation.version.strip()
        or len(observation.version) > 128
        or not observation.version.isprintable()
    ):
        raise ValueError("runtime version must be canonical and nonblank")
    digests = (observation.stdout_sha256, observation.stderr_sha256)
    if any(type(item) is not str or not _SHA256.fullmatch(item) for item in digests):
        raise ValueError("runtime log digest is not canonical lowercase SHA-256")
    if (
        type(observation.stdout_truncated) is not bool
        or type(observation.stderr_truncated) is not bool
    ):
        raise ValueError("runtime truncation flags must be exact booleans")
    timestamps = (observation.started_at, observation.finished_at)
    if (
        any(
            type(item) is not datetime
            or item.utcoffset() != timedelta(0)
            or item.tzname() != "UTC"
            for item in timestamps
        )
        or observation.finished_at < observation.started_at
    ):
        raise ValueError("runtime timestamps must be ordered UTC values")
    resources = (observation.peak_memory_bytes, observation.storage_bytes)
    if observation.resource_status not in {"measured", "unknown"}:
        raise ValueError("runtime resource status is invalid")
    if observation.resource_status == "measured" and (
        any(type(item) is not int or item < 0 for item in resources)
        or type(observation.oom_killed) is not bool
    ):
        raise ValueError("measured runtime resources are invalid")
    if observation.resource_status == "unknown" and (
        resources != (None, None) or observation.oom_killed is not None
    ):
        raise ValueError("unknown runtime resources are inconsistent")
    return observation


def runtime_payload(
    value: object,
    expected_engine: str,
    expected_executable_sha256: str | None = None,
    expected_endpoint: str | None = None,
) -> dict[str, object]:
    """Validate before returning the exact serializable evidence payload."""
    return asdict(
        validate_runtime_observation(
            value,
            expected_engine,
            expected_executable_sha256,
            expected_endpoint,
        )
    )


def restore_runtime_observation(
    payload: object,
    expected_engine: str | None = None,
    expected_executable_sha256: str | None = None,
    expected_endpoint: str | None = None,
) -> RuntimeObservation:
    """Restore only the exact persisted runtime observation schema."""
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise TypeError("runtime observation payload schema is invalid")
    values = dict(payload)
    observed_engine = values.get("engine")
    if expected_engine is None:
        if type(observed_engine) is not str:
            raise TypeError("runtime observation engine is invalid")
        expected_engine = observed_engine
    for field in ("started_at", "finished_at"):
        raw = values[field]
        if type(raw) is not str:
            raise TypeError("runtime timestamp payload is invalid")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as error:
            raise ValueError("runtime timestamp payload is invalid") from error
        if parsed.utcoffset() != timedelta(0) or parsed.tzname() != "UTC":
            raise ValueError("runtime timestamp payload is not UTC")
        values[field] = parsed
    return validate_runtime_observation(
        RuntimeObservation(**values),
        expected_engine,
        expected_executable_sha256,
        expected_endpoint,
    )


def _validate_endpoint(value: object) -> None:
    if type(value) is not str or not value.startswith("unix://"):
        raise ValueError("runtime endpoint must be an explicit local unix socket")
    path = value.removeprefix("unix://")
    if not path.startswith("/") or ".." in path.split("/"):
        raise ValueError("runtime endpoint must be an explicit local unix socket")
