"""Read-only authentication for already-enrolled human principals."""

from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import TYPE_CHECKING

from envresearch.models.artifact import ProducerIdentity
from envresearch.models.principal import (
    PrincipalAssignment,
    PrincipalKind,
    PrincipalVerification,
)

if TYPE_CHECKING:
    from envresearch.research.principal_registry import PrincipalRegistry


def human_assignment(kind: PrincipalKind) -> PrincipalAssignment:
    """Return the fixed deterministic human-control assignment."""
    if kind not in {PrincipalKind.GATE, PrincipalKind.REVISION}:
        raise ValueError("human principal kind must be gate or revision")
    return PrincipalAssignment(
        assignment_id=f"assignment-human-{kind.value}",
        principal_id="human-reviewer",
        kind=kind,
        producer=ProducerIdentity(
            component="human-control",
            version="0.2.0",
            runtime="owner-control",
            context_id=f"context-human-{kind.value}",
        ),
        verification=PrincipalVerification.OWNER_CONTROL,
    )


def existing_capability(
    registry: PrincipalRegistry, kind: PrincipalKind, supplied: str
) -> PrincipalAssignment:
    """Require existing capability bytes and assignment without creating either."""
    if kind not in {PrincipalKind.GATE, PrincipalKind.REVISION}:
        raise ValueError("capability kind must be gate or revision")
    try:
        expected = registry.control.storage.read_file(
            Path("principals") / f"{kind.value}.capability",
            description=f"{kind.value} principal capability",
            required_mode=0o600,
            required_owner=os.getuid(),
        )
    except OSError as error:
        raise ValueError(f"{kind.value} principal capability is invalid") from error
    candidate = supplied.strip().encode()
    if not candidate or not hmac.compare_digest(candidate, expected):
        raise ValueError(f"{kind.value} principal capability is invalid")
    return _existing_human(registry, kind)


def existing_gate_actor(
    registry: PrincipalRegistry,
    actor: str,
    reviewed: tuple[ProducerIdentity, ...],
) -> PrincipalAssignment:
    """Require the existing gate actor and separation from reviewed producers."""
    assignment = _existing_human(registry, PrincipalKind.GATE)
    if actor != assignment.principal_id:
        raise ValueError("gate decision lacks the authenticated gate principal")
    contexts = {producer.context_id for producer in reviewed}
    if None in contexts or assignment.producer.context_id in contexts:
        raise ValueError("gate principal must differ from reviewed principals")
    return assignment


def _existing_human(
    registry: PrincipalRegistry, kind: PrincipalKind
) -> PrincipalAssignment:
    expected = human_assignment(kind)
    try:
        assignment = registry._read_benchmark_assignment(
            Path("principals") / f"{kind.value}.json"
        )
    except (OSError, ValueError) as error:
        raise ValueError("principal assignment authentication failed") from error
    if assignment != expected:
        raise ValueError("principal assignment authentication failed")
    return assignment


__all__ = ["existing_capability", "existing_gate_actor", "human_assignment"]
