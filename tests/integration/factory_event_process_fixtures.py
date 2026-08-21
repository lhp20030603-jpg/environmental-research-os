"""Spawn worker that dies during the physical factory-event byte write."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from factory_process_fixtures import ProcessConfig, open_service

from envresearch.kernel.gates import GateDecision
from envresearch.models.artifact import ArtifactRef


def crash_factory_event_append(
    config_path: str,
    operation: str,
    refs_json: str,
    decision_json: str | None,
    capability: str | None,
    attempting: Any,
    acquired: Any,
) -> None:
    """Persist partial event bytes and die before the append can complete."""
    config = ProcessConfig.model_validate_json(Path(config_path).read_bytes())
    opened = open_service(config_path)
    refs = tuple(ArtifactRef.model_validate(item) for item in json.loads(refs_json))
    target = config.factory_root / "factory-events.jsonl"
    prior_size = target.stat().st_size if target.exists() else 0
    original_fsync = os.fsync

    def descriptor_name(descriptor: int) -> str:
        if sys.platform == "darwin":
            import fcntl

            path = fcntl.fcntl(descriptor, 50, b"\0" * 1024)
            return Path(path.rstrip(b"\0").decode()).name
        return Path(os.readlink(f"/proc/self/fd/{descriptor}")).name

    def crash_during_fsync(descriptor: int) -> None:
        name = descriptor_name(descriptor)
        if "factory-events.jsonl" in name:
            written = os.fstat(descriptor).st_size
            partial = prior_size + max(1, (written - prior_size) // 2)
            os.ftruncate(descriptor, partial)
            original_fsync(descriptor)
            acquired.set()
            os._exit(95)
        original_fsync(descriptor)

    os.fsync = crash_during_fsync  # type: ignore[assignment]
    attempting.set()
    if operation == "run":
        opened.service.assemble(refs[0], refs[1])
    elif operation == "request":
        opened.service.request_promotion(refs[0], requested_by="factory-agent")
    else:
        opened.service.record_promotion(
            refs[0], GateDecision.model_validate_json(decision_json), capability
        )


__all__ = ["crash_factory_event_append"]
