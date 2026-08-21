"""Dependency-neutral principal assignment contracts."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from envresearch.models.artifact import ProducerIdentity

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class PrincipalKind(StrEnum):
    """Trusted responsibility assigned by the workflow control plane."""

    WORKER = "worker"
    CRITIC = "critic"
    GATE = "gate"
    REVISION = "revision"
    CURATOR = "curator"
    MASKER = "masker"
    LEAKAGE_VALIDATOR = "leakage-validator"
    RECOMMENDER = "recommender"
    EXPERT = "expert"
    ADJUDICATOR = "adjudicator"


class PrincipalVerification(StrEnum):
    """Whether identity comes from protected control state or mere attestation."""

    OWNER_CONTROL = "owner_control"
    PUBLIC_KEY_SIGNATURE = "public_key_signature"
    UNVERIFIED_LOCAL_ATTESTATION = "unverified_local_attestation"


class PrincipalAssignment(BaseModel):
    """Immutable scheduler assignment carried by authenticated control state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assignment_id: str
    principal_id: str
    kind: PrincipalKind
    producer: ProducerIdentity
    verification: PrincipalVerification
    key_id: str | None = None
    public_key_sha256: str | None = None

    @field_validator("assignment_id", "principal_id")
    @classmethod
    def require_safe_identity(cls, value: str) -> str:
        if not _SAFE.fullmatch(value):
            raise ValueError("principal identity must be a canonical identifier")
        return value

    @model_validator(mode="after")
    def require_external_human_key_binding(self) -> PrincipalAssignment:
        keyed = self.key_id is not None or self.public_key_sha256 is not None
        if keyed and (
            self.key_id is None
            or self.public_key_sha256 is None
            or not _SAFE.fullmatch(self.key_id)
            or len(self.public_key_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.public_key_sha256)
        ):
            raise ValueError("principal public-key binding is invalid")
        if self.kind in {PrincipalKind.EXPERT, PrincipalKind.ADJUDICATOR} and (
            self.verification is not PrincipalVerification.PUBLIC_KEY_SIGNATURE
            or not keyed
        ):
            raise ValueError("benchmark human principals require external public keys")
        return self
