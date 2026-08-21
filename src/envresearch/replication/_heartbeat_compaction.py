"""Bounded authenticated heartbeat evidence helpers."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from envresearch.replication._ledger_models import (
        ReplicationRun,
        ResourceObservation,
    )

_RETAINED_OBSERVATIONS = 8
_EMPTY_CHAIN = "0" * 64


def append_observation(
    run: ReplicationRun, observation: ResourceObservation
) -> dict[str, object]:
    """Append one observation while retaining a constant-size authenticated tail."""
    previous = run.observation_chain_sha256 or _EMPTY_CHAIN
    payload = json.dumps(
        observation.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    chain = hashlib.sha256(bytes.fromhex(previous) + payload).hexdigest()
    retained = (*run.observations, observation)[-_RETAINED_OBSERVATIONS:]
    return {
        "observations": retained,
        "observation_count": run.observation_count + 1,
        "observation_chain_sha256": chain,
    }
