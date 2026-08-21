"""External Ed25519 authority and human-evidence contracts."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, BeforeValidator, ConfigDict, JsonValue, model_validator

from envresearch.models.artifact import ArtifactRef
from envresearch.models.principal import PrincipalKind

_ENROLLMENT_DOMAIN = b"envresearch.blind-enrollment.v1\0"
_EVIDENCE_DOMAIN = b"envresearch.blind-human-evidence.v1\0"


def _tuple(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


class _StrictFrozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class HumanKeyEnrollment(_StrictFrozen):
    case_id: str
    role: Literal[PrincipalKind.EXPERT, PrincipalKind.ADJUDICATOR]
    slot: int
    principal_id: str
    key_id: str
    public_key: str

    @model_validator(mode="after")
    def require_canonical_human_key(self) -> HumanKeyEnrollment:
        if self.slot < 1:
            raise ValueError("human key slot must be positive")
        _require_text(self.case_id, self.principal_id, self.key_id)
        if self.principal_id.startswith(f"principal-{self.case_id}-"):
            raise ValueError("participant cannot use a reserved controller principal")
        _decode_exact(self.public_key, 32, "Ed25519 public key")
        return self

    @property
    def public_key_sha256(self) -> str:
        return hashlib.sha256(_decode(self.public_key)).hexdigest()


class EnrolledBlindCase(_StrictFrozen):
    case_id: str
    method_family: str
    cohort: Literal["pilot", "held_out"]
    source_generation: int
    descriptor_sha256: str
    source_ref: ArtifactRef
    claim_fact_map_ref: ArtifactRef
    blinded_brief_ref: ArtifactRef

    @model_validator(mode="after")
    def require_case_commitments(self) -> EnrolledBlindCase:
        _require_text(self.case_id, self.method_family)
        if self.source_generation < 1:
            raise ValueError("source generation must be positive")
        _require_sha256(self.descriptor_sha256, "descriptor digest")
        return self


class BlindEnrollmentPayload(_StrictFrozen):
    evaluation_id: str
    authority_key_id: str
    frozen_at: datetime
    cases: Annotated[tuple[EnrolledBlindCase, ...], BeforeValidator(_tuple)]
    participants: Annotated[tuple[HumanKeyEnrollment, ...], BeforeValidator(_tuple)]
    profile_registry_sha256: str
    rubric_sha256: str
    policy_sha256: str

    @model_validator(mode="after")
    def require_complete_distinct_enrollment(self) -> BlindEnrollmentPayload:
        _require_text(self.evaluation_id, self.authority_key_id)
        for value in (
            self.profile_registry_sha256,
            self.rubric_sha256,
            self.policy_sha256,
        ):
            _require_sha256(value, "enrollment digest")
        if not self.cases:
            raise ValueError("enrollment requires at least one case")
        case_ids = tuple(case.case_id for case in self.cases)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("enrollment case IDs must be distinct")
        identities = tuple(item.principal_id for item in self.participants)
        key_ids = tuple(item.key_id for item in self.participants)
        keys = tuple(item.public_key_sha256 for item in self.participants)
        if any(len(values) != len(set(values)) for values in (identities, key_ids, keys)):
            raise ValueError("human identities and keys must be distinct across roles")
        expected = {
            (case_id, PrincipalKind.EXPERT, 1)
            for case_id in case_ids
        } | {
            (case_id, PrincipalKind.EXPERT, 2)
            for case_id in case_ids
        } | {
            (case_id, PrincipalKind.ADJUDICATOR, 1)
            for case_id in case_ids
        }
        actual = {(item.case_id, item.role, item.slot) for item in self.participants}
        if actual != expected or len(actual) != len(self.participants):
            raise ValueError("enrollment requires exactly two experts and one adjudicator per case")
        return self


class SignedBlindEnrollment(_StrictFrozen):
    payload: BlindEnrollmentPayload
    signature: str

    @model_validator(mode="after")
    def require_signature_shape(self) -> SignedBlindEnrollment:
        _decode_exact(self.signature, 64, "Ed25519 signature")
        return self


class AuthorityTrustAnchor(_StrictFrozen):
    """Owner-pinned public authority identity; never a signing secret."""

    key_id: str
    public_key: str

    @model_validator(mode="after")
    def require_public_authority(self) -> AuthorityTrustAnchor:
        _require_text(self.key_id)
        _decode_exact(self.public_key, 32, "Ed25519 public key")
        return self


class SignedHumanEvidence(_StrictFrozen):
    case_id: str
    role: Literal[PrincipalKind.EXPERT, PrincipalKind.ADJUDICATOR]
    slot: int
    source_generation: int
    assignment_id: str
    order_hash: str
    candidate_schema: str
    candidate_sha256: str
    key_id: str
    candidate: JsonValue
    signature: str

    @model_validator(mode="after")
    def require_bound_candidate(self) -> SignedHumanEvidence:
        _require_text(
            self.case_id,
            self.assignment_id,
            self.order_hash,
            self.candidate_schema,
            self.key_id,
        )
        if self.slot < 1 or self.source_generation < 1:
            raise ValueError("signed evidence slot and generation must be positive")
        _require_sha256(self.candidate_sha256, "candidate digest")
        if self.candidate_sha256 != hashlib.sha256(
            canonical_json(self.candidate)
        ).hexdigest():
            raise ValueError("signed evidence candidate digest mismatch")
        _decode_exact(self.signature, 64, "Ed25519 signature")
        return self


@dataclass(frozen=True, slots=True)
class VerifiedBlindEnrollment:
    """Verified data result; never itself a release authorization."""
    payload: BlindEnrollmentPayload
    signed_sha256: str


def verify_enrollment_signature(
    signed: SignedBlindEnrollment, anchor: AuthorityTrustAnchor
) -> tuple[BlindEnrollmentPayload, str]:
    """Verify cryptography only; this does not grant release authority."""
    if signed.payload.authority_key_id != anchor.key_id:
        raise ValueError("enrollment authority key ID mismatch")
    _verify(anchor.public_key, enrollment_signing_bytes(signed.payload), signed.signature)
    digest = hashlib.sha256(canonical_json(signed.model_dump(mode="json"))).hexdigest()
    return signed.payload, digest


def verify_human_evidence(
    evidence: SignedHumanEvidence,
    participant: HumanKeyEnrollment,
    *,
    case_id: str,
    role: PrincipalKind,
    slot: int,
    source_generation: int,
    assignment_id: str,
    order_hash: str,
    candidate_schema: str,
) -> bytes:
    expected = (
        case_id,
        role,
        slot,
        source_generation,
        assignment_id,
        order_hash,
        candidate_schema,
        participant.key_id,
    )
    actual = (
        evidence.case_id,
        evidence.role,
        evidence.slot,
        evidence.source_generation,
        evidence.assignment_id,
        evidence.order_hash,
        evidence.candidate_schema,
        evidence.key_id,
    )
    if actual != expected or (
        participant.case_id,
        participant.role,
        participant.slot,
    ) != (case_id, role, slot):
        raise ValueError("signed human evidence does not match the current assignment")
    _verify(participant.public_key, evidence_signing_bytes(evidence), evidence.signature)
    return canonical_json(evidence.candidate)


def enrollment_signing_bytes(payload: BlindEnrollmentPayload) -> bytes:
    return _ENROLLMENT_DOMAIN + canonical_json(payload.model_dump(mode="json"))


def evidence_signing_bytes(evidence: SignedHumanEvidence) -> bytes:
    return _EVIDENCE_DOMAIN + canonical_json(
        evidence.model_dump(mode="json", exclude={"signature"})
    )


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()


def encode_binary(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("invalid base64 encoding") from error


def _decode_exact(value: str, length: int, label: str) -> None:
    if len(_decode(value)) != length:
        raise ValueError(f"{label} has invalid length")


def _verify(public_key: str, message: bytes, signature: str) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(_decode(public_key)).verify(
            _decode(signature), message
        )
    except (InvalidSignature, ValueError) as error:
        raise ValueError("Ed25519 signature verification failed") from error


def _require_text(*values: str) -> None:
    if any(not value or value != value.strip() for value in values):
        raise ValueError("authority text fields must be canonical and nonblank")


def _require_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{label} must be lowercase SHA-256")
