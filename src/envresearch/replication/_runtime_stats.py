"""Private strict parsing for reviewed container resource observations."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from envresearch.replication._container_models import ResourceMeasurement

if TYPE_CHECKING:
    from envresearch.replication.container import CommandExecution

_MEMORY = re.compile(r"^([0-9]+(?:\.[0-9]+)?)(B|KiB|MiB|GiB)$")
_SCALE = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}


def parse_resource_measurement(
    inspect: CommandExecution, stats: CommandExecution
) -> ResourceMeasurement:
    """Return measured values only when both exact runtime probes succeed."""
    if inspect.return_code != 0 or stats.return_code != 0:
        return ResourceMeasurement(None, None, None)
    try:
        inspected = json.loads(inspect.stdout)
        observed = json.loads(stats.stdout)
    except (json.JSONDecodeError, TypeError):
        return ResourceMeasurement(None, None, None)
    if isinstance(inspected, list) and len(inspected) == 1:
        inspected = inspected[0]
    if not isinstance(inspected, dict) or not isinstance(observed, dict):
        return ResourceMeasurement(None, None, None)
    storage = inspected.get("SizeRw")
    state = inspected.get("State")
    oom = state.get("OOMKilled") if isinstance(state, dict) else None
    usage = observed.get("MemUsage")
    memory = _memory_bytes(usage)
    if (
        type(storage) is not int
        or storage < 0
        or type(oom) is not bool
        or memory is None
    ):
        return ResourceMeasurement(None, None, None)
    return ResourceMeasurement(memory, storage, oom)


def _memory_bytes(value: object) -> int | None:
    if type(value) is not str:
        return None
    used = value.partition(" / ")[0]
    match = _MEMORY.fullmatch(used)
    if match is None:
        return None
    try:
        measured = Decimal(match.group(1)) * _SCALE[match.group(2)]
    except InvalidOperation:
        return None
    if measured != measured.to_integral_value() or measured < 0:
        return None
    return int(measured)
