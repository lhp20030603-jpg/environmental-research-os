"""Public-key enrollment persistence for benchmark human principals."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from envresearch.benchmarks.blind_authority import HumanKeyEnrollment
from envresearch.models.principal import PrincipalAssignment, PrincipalKind

if TYPE_CHECKING:
    from envresearch.research.principal_registry import PrincipalRegistry


class _ProtectedHumanKeys(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    participants: tuple[HumanKeyEnrollment, ...]
    mac: str


def enroll_humans(
    registry: PrincipalRegistry,
    case_id: str,
    participants: tuple[HumanKeyEnrollment, ...],
) -> None:
    expected = tuple(item for item in participants if item.case_id == case_id)
    if len(expected) != 3:
        raise ValueError("participant enrollment is incomplete for benchmark case")
    payload = _canonical([item.model_dump(mode="json") for item in expected])
    record = _ProtectedHumanKeys(
        participants=expected,
        mac=hmac.new(registry.control.key, payload, hashlib.sha256).hexdigest(),
    )
    path = registry._benchmark_path(case_id) / "human-keys.json"
    data = _canonical(record.model_dump(mode="json"))
    if not registry.control.storage.exists(path):
        registry.control.storage.write_file_noreplace(path, data, mode=0o600)
    if read_human_keys(registry, case_id) != expected:
        raise ValueError("participant enrollment cannot be replaced")


def benchmark_human(
    registry: PrincipalRegistry,
    case_id: str,
    kind: PrincipalKind,
    slot: int,
    generation: int,
) -> PrincipalAssignment:
    participant = enrolled_human_key(registry, case_id, kind, slot)
    assignment = registry._benchmark_assignment(
        case_id, kind, generation, human=True, participant=participant
    )
    return registry._persist_or_authenticate(
        assignment, registry._benchmark_path(case_id) / f"{kind.value}-{slot}.json"
    )


def enrolled_human_key(
    registry: PrincipalRegistry, case_id: str, kind: PrincipalKind, slot: int
) -> HumanKeyEnrollment:
    for participant in read_human_keys(registry, case_id):
        if participant.role is kind and participant.slot == slot:
            return participant
    raise ValueError("participant enrollment is missing")


def read_human_keys(
    registry: PrincipalRegistry, case_id: str
) -> tuple[HumanKeyEnrollment, ...]:
    path = registry._benchmark_path(case_id) / "human-keys.json"
    try:
        data = registry.control.storage.read_file(
            path, description="benchmark public-key enrollment", required_mode=0o600
        )
    except FileNotFoundError as error:
        raise ValueError("participant enrollment is missing") from error
    record = _ProtectedHumanKeys.model_validate_json(data)
    payload = _canonical([item.model_dump(mode="json") for item in record.participants])
    expected = hmac.new(registry.control.key, payload, hashlib.sha256).hexdigest()
    if data != _canonical(record.model_dump(mode="json")) or not hmac.compare_digest(
        record.mac, expected
    ):
        raise ValueError("participant enrollment authentication failed")
    return record.participants


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
