"""Authenticated first-transition marker for blind benchmark enrollment."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from envresearch.benchmarks.blind_authority import (
    EnrolledBlindCase,
    VerifiedBlindEnrollment,
    canonical_json,
)
from envresearch.benchmarks.blind_enrollment_store import read_verified_enrollment
from envresearch.research.principal_registry import PrincipalRegistry


class _EnrollmentMarker(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    case_id: str
    signed_sha256: str
    authority_key_id: str
    source_generation: int
    mac: str


def freeze_enrollment(
    registry: PrincipalRegistry,
    case_id: str,
    verified: VerifiedBlindEnrollment,
) -> None:
    case = _enrolled_case(verified, case_id)
    identity = {
        "case_id": case_id,
        "signed_sha256": verified.signed_sha256,
        "authority_key_id": verified.payload.authority_key_id,
        "source_generation": case.source_generation,
    }
    marker = _EnrollmentMarker(
        case_id=case_id,
        signed_sha256=verified.signed_sha256,
        authority_key_id=verified.payload.authority_key_id,
        source_generation=case.source_generation,
        mac=hmac.new(
            registry.control.key, canonical_json(identity), hashlib.sha256
        ).hexdigest(),
    )
    path = _marker_path(case_id)
    data = canonical_json(marker.model_dump(mode="json"))
    storage = registry.control.storage
    if not storage.exists(path):
        storage.write_file_noreplace(path, data, mode=0o600)
    if _read_marker(registry, case_id) != marker:
        raise ValueError("blind enrollment marker cannot be replaced")


def require_frozen_enrollment(
    registry: PrincipalRegistry, case_id: str
) -> VerifiedBlindEnrollment:
    try:
        marker = _read_marker(registry, case_id)
    except FileNotFoundError as error:
        raise ValueError("frozen blind enrollment is required") from error
    verified = read_verified_enrollment(registry, case_id)
    case = _enrolled_case(verified, case_id)
    if (
        marker.signed_sha256 != verified.signed_sha256
        or marker.authority_key_id != verified.payload.authority_key_id
        or marker.source_generation != case.source_generation
    ):
        raise ValueError("blind enrollment marker authentication mismatch")
    participants = tuple(
        item for item in verified.payload.participants if item.case_id == case_id
    )
    for participant in participants:
        if registry.enrolled_human_key(
            case_id, participant.role, participant.slot
        ) != participant:
            raise ValueError("frozen blind participant enrollment mismatch")
    return verified


def enrollment_is_frozen(registry: PrincipalRegistry, case_id: str) -> bool:
    return registry.control.storage.exists(_marker_path(case_id))


def _read_marker(registry: PrincipalRegistry, case_id: str) -> _EnrollmentMarker:
    data = registry.control.storage.read_file(
        _marker_path(case_id),
        description="blind enrollment marker",
        required_mode=0o600,
    )
    marker = _EnrollmentMarker.model_validate_json(data)
    identity = marker.model_dump(mode="json", exclude={"mac"})
    expected = hmac.new(
        registry.control.key, canonical_json(identity), hashlib.sha256
    ).hexdigest()
    if (
        data != canonical_json(marker.model_dump(mode="json"))
        or marker.case_id != case_id
        or not hmac.compare_digest(marker.mac, expected)
    ):
        raise ValueError("blind enrollment marker authentication failed")
    return marker


def _enrolled_case(
    verified: VerifiedBlindEnrollment, case_id: str
) -> EnrolledBlindCase:
    cases = tuple(case for case in verified.payload.cases if case.case_id == case_id)
    if len(cases) != 1:
        raise ValueError("controller case is missing from sealed enrollment")
    return cases[0]


def _marker_path(case_id: str) -> Path:
    return Path("principals/benchmark") / case_id / "enrollment-frozen.json"
