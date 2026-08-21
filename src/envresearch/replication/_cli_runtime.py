"""Private reviewed runtime configuration loader for the replication CLI."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml  # type: ignore[import-untyped]

from envresearch.replication._runtime_owner import RuntimeOwnership
from envresearch.replication.container import (
    ContainerPlan,
    ContainerResult,
    RuntimeObservation,
)


class _UnavailableEngine:
    """Fail closed until an operator deployment binds Docker or Podman."""

    identity = "unavailable"
    executable_sha256 = "0" * 64
    endpoint = ""

    def preflight(self, profile: object) -> RuntimeObservation:
        del profile
        raise RuntimeError("a live Docker or Podman executor is not configured")

    def run(  # type: ignore[no-untyped-def]
        self,
        plan: ContainerPlan,
        *,
        on_progress=None,
        on_started=None,
        on_stopped=None,
    ) -> ContainerResult:
        del plan, on_progress, on_started, on_stopped
        raise RuntimeError("a live Docker or Podman executor is not configured")

    def contain(self, owner: RuntimeOwnership | None, names: tuple[str, ...]) -> None:
        del owner, names
        raise RuntimeError("a live Docker or Podman executor is not configured")


def configured_engine_configurations() -> tuple[object, ...]:
    configurations = _configured_replication().get("engines")
    if not isinstance(configurations, list):
        raise TypeError("reviewed engine configuration is invalid")
    return tuple(configurations)


def configured_max_growth_bytes() -> int:
    progress = _configured_replication().get("progress")
    if not isinstance(progress, dict):
        raise TypeError("replication progress configuration is invalid")
    value = progress.get("max_growth_bytes")
    if type(value) is not int or value < 1:
        raise ValueError("replication growth limit is invalid")
    return value


def _configured_replication() -> dict[str, object]:
    path = Path(__file__).resolve().parents[3] / "configs/replication-v03-default.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("replication runtime configuration is invalid")
    replication = value.get("replication")
    if not isinstance(replication, dict):
        raise TypeError("replication runtime configuration is invalid")
    return cast(dict[str, object], replication)
