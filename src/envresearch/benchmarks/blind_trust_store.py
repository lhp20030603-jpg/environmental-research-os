"""Protected local trust anchor for authority-signed blind enrollments."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from envresearch.benchmarks.blind_authority import AuthorityTrustAnchor, canonical_json
from envresearch.research.principal_registry import PrincipalRegistry

_ANCHOR_PATH = Path("principals/blind-authority-trust-anchor.json")


class _ProtectedAuthorityAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    anchor: AuthorityTrustAnchor
    mac: str


def pin_authority_anchor(
    registry: PrincipalRegistry, anchor: AuthorityTrustAnchor
) -> AuthorityTrustAnchor:
    """Pin one immutable public trust root in owner-protected control state."""
    identity = canonical_json(anchor.model_dump(mode="json"))
    record = _ProtectedAuthorityAnchor(
        anchor=anchor,
        mac=hmac.new(registry.control.key, identity, hashlib.sha256).hexdigest(),
    )
    data = canonical_json(record.model_dump(mode="json"))
    storage = registry.control.storage
    if not storage.exists(_ANCHOR_PATH):
        storage.write_file_noreplace(_ANCHOR_PATH, data, mode=0o600)
    durable = _read_record(registry)
    if durable != record:
        raise ValueError("blind authority trust anchor cannot be replaced")
    return durable.anchor


def read_authority_anchor(registry: PrincipalRegistry) -> AuthorityTrustAnchor:
    """Authenticate the previously pinned trust root; never accept a caller key."""
    try:
        return _read_record(registry).anchor
    except FileNotFoundError as error:
        raise ValueError("pinned blind authority trust anchor is required") from error


def _read_record(registry: PrincipalRegistry) -> _ProtectedAuthorityAnchor:
    data = registry.control.storage.read_file(
        _ANCHOR_PATH,
        description="blind authority trust anchor",
        required_mode=0o600,
    )
    record = _ProtectedAuthorityAnchor.model_validate_json(data)
    identity = canonical_json(record.anchor.model_dump(mode="json"))
    expected = hmac.new(
        registry.control.key, identity, hashlib.sha256
    ).hexdigest()
    if (
        data != canonical_json(record.model_dump(mode="json"))
        or not hmac.compare_digest(record.mac, expected)
    ):
        raise ValueError("blind authority trust anchor authentication failed")
    return record
