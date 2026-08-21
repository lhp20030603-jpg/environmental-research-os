"""Durable read boundary for authority-signed blind enrollments."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from envresearch.benchmarks.blind_authority import (
    SignedBlindEnrollment,
    VerifiedBlindEnrollment,
    canonical_json,
    verify_enrollment_signature,
)
from envresearch.benchmarks.blind_trust_store import read_authority_anchor
from envresearch.research.principal_registry import PrincipalRegistry


class StoredBlindEnrollment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    signed: SignedBlindEnrollment


def read_verified_enrollment(
    registry: PrincipalRegistry,
    case_id: str,
) -> VerifiedBlindEnrollment:
    path = Path("principals/benchmark") / case_id / "signed-enrollment.json"
    try:
        data = registry.control.storage.read_file(
            path, description="signed benchmark enrollment", required_mode=0o600
        )
    except FileNotFoundError as error:
        raise ValueError("verified authority enrollment is required") from error
    record = StoredBlindEnrollment.model_validate_json(data)
    if data != canonical_json(record.model_dump(mode="json")):
        raise ValueError("signed benchmark enrollment is not canonical")
    payload, digest = verify_enrollment_signature(
        record.signed, read_authority_anchor(registry)
    )
    return VerifiedBlindEnrollment(payload, digest)


def store_signed_enrollment(
    registry: PrincipalRegistry, case_id: str, signed: SignedBlindEnrollment
) -> None:
    path = Path("principals/benchmark") / case_id / "signed-enrollment.json"
    data = canonical_json(StoredBlindEnrollment(signed=signed).model_dump(mode="json"))
    storage = registry.control.storage
    if not storage.exists(path):
        storage.write_file_noreplace(path, data, mode=0o600)
    if storage.read_file(
        path, description="signed benchmark enrollment", required_mode=0o600
    ) != data:
        raise ValueError("participant enrollment cannot be replaced")
