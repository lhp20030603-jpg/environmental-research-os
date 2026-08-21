"""Mechanical evidence-link semantics for blind method recommendations."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from envresearch.benchmarks.claim_report import CitationIntegrityFinding
from envresearch.models.benchmark_claims import (
    ClaimUsage,
    ClaimVerificationStatus,
    VerifiedClaim,
)
from envresearch.models.benchmark_evaluation import AcceptedArtifactClaims

_FACT_ID = re.compile(r"^fact-[a-z0-9]+(?:-[a-z0-9]+)*$")
_FACT_ID_TOKEN = re.compile(r"\bfact-[a-z0-9]+(?:-[a-z0-9]+)*\b")
SourceDependencyCheck = Callable[[str, str, str | None], bool]


@dataclass(frozen=True, slots=True)
class CurrentClaimBinding:
    """A source claim and its exact current blinded-fact mapping, if eligible."""

    claim: VerifiedClaim
    fact_id: str | None


def validate_blind_recommendation(
    artifact: AcceptedArtifactClaims,
    claims: Mapping[str, CurrentClaimBinding],
    *,
    leaves: Mapping[str, str],
    statement_sha256: Callable[[str], str],
    is_source_dependent: SourceDependencyCheck,
) -> tuple[CitationIntegrityFinding, ...]:
    """Validate fact linkage mechanically without claiming prose entailment."""
    findings: list[CitationIntegrityFinding] = []
    fact_pointers = _fact_pointers(artifact, findings)
    fact_values = tuple(fact_pointers.values())
    if not fact_values:
        findings.append(
            _finding(
                "BLIND_FACT_REFS_INVALID",
                artifact,
                "/fact_refs",
                None,
                "method recommendation requires structured fact_refs",
            )
        )
    if len(fact_values) != len(set(fact_values)):
        findings.append(
            _finding(
                "BLIND_FACT_REFS_INVALID",
                artifact,
                "/fact_refs",
                None,
                "fact_refs must not contain duplicate fact IDs",
            )
        )

    usages_by_pointer: dict[str, list[ClaimUsage]] = {}
    for usage in artifact.usages:
        usages_by_pointer.setdefault(usage.json_pointer, []).append(usage)
    bound_pointers: set[str] = set()
    for pointer in sorted(usages_by_pointer):
        pointer_usages = usages_by_pointer[pointer]
        statement = fact_pointers.get(pointer)
        if statement is None or len(pointer_usages) != 1:
            findings.append(
                _finding(
                    "CLAIM_USAGE_UNBOUND",
                    artifact,
                    pointer,
                    None,
                    "blind claim usage must bind exactly one fact_refs string leaf",
                )
            )
            continue
        usage = pointer_usages[0]
        canonical_fact = _FACT_ID.fullmatch(statement) is not None
        exact_hash = usage.statement_sha256 == statement_sha256(statement)
        if not canonical_fact or not exact_hash:
            findings.append(
                _finding(
                    "CLAIM_USAGE_UNBOUND",
                    artifact,
                    pointer,
                    usage.claim_id,
                    "claim usage does not bind the exact canonical fact ID leaf",
                )
            )
            continue
        current = claims.get(usage.claim_id)
        if (
            current is None
            or current.fact_id != statement
            or current.claim.status is not ClaimVerificationStatus.CLAIM_VERIFIED
        ):
            findings.append(
                _finding(
                    "CLAIM_NOT_CURRENT_VERIFIED",
                    artifact,
                    pointer,
                    usage.claim_id,
                    "claim does not map to this current verified fact",
                )
            )
            continue
        bound_pointers.add(pointer)

    for pointer, statement in sorted(fact_pointers.items()):
        if _FACT_ID.fullmatch(statement) is None:
            findings.append(
                _finding(
                    "BLIND_FACT_REFS_INVALID",
                    artifact,
                    pointer,
                    None,
                    "fact_refs leaf is not a canonical fact ID",
                )
            )
        if pointer not in bound_pointers:
            findings.append(
                _finding(
                    "SOURCE_STATEMENT_UNBOUND",
                    artifact,
                    pointer,
                    None,
                    "fact reference has no exact current claim usage",
                )
            )

    _reject_blind_prose(
        artifact,
        leaves,
        fact_pointers,
        is_source_dependent,
        findings,
    )
    return tuple(findings)


def require_source_independent_recommendation(
    payload: object,
    is_source_dependent: SourceDependencyCheck,
) -> None:
    """Reject blind prose markers before a candidate can be persisted."""
    if not isinstance(payload, Mapping):
        raise TypeError("recommendation payload must be a mapping")
    raw_fact_refs = payload.get("fact_refs")
    if not isinstance(raw_fact_refs, Sequence) or isinstance(
        raw_fact_refs, (str, bytes, bytearray)
    ):
        raise TypeError("fact_refs must be a sequence")
    fact_pointers = {f"/fact_refs/{index}" for index in range(len(raw_fact_refs))}
    for pointer, statement in _string_leaves(payload):
        if pointer in fact_pointers:
            continue
        if _FACT_ID_TOKEN.search(statement) or is_source_dependent(
            pointer, statement, "method-recommendation"
        ):
            raise ValueError("citation integrity validation failed")


def _fact_pointers(
    artifact: AcceptedArtifactClaims,
    findings: list[CitationIntegrityFinding],
) -> dict[str, str]:
    payload = artifact.payload
    raw = payload.get("fact_refs") if isinstance(payload, Mapping) else None
    if not isinstance(raw, (list, tuple)) or any(
        not isinstance(value, str) for value in raw
    ):
        findings.append(
            _finding(
                "BLIND_FACT_REFS_INVALID",
                artifact,
                "/fact_refs",
                None,
                "fact_refs must be an array of canonical fact IDs",
            )
        )
        return {}
    result: dict[str, str] = {}
    for index, value in enumerate(raw):
        assert isinstance(value, str)
        result[f"/fact_refs/{index}"] = value
    return result


def _reject_blind_prose(
    artifact: AcceptedArtifactClaims,
    leaves: Mapping[str, str],
    fact_pointers: Mapping[str, str],
    is_source_dependent: SourceDependencyCheck,
    findings: list[CitationIntegrityFinding],
) -> None:
    for pointer, statement in sorted(leaves.items()):
        if pointer in fact_pointers:
            continue
        if _FACT_ID_TOKEN.search(statement):
            findings.append(
                _finding(
                    "BLIND_FACT_IN_PROSE",
                    artifact,
                    pointer,
                    None,
                    "fact IDs are permitted only in structured fact_refs leaves",
                )
            )
        if is_source_dependent(pointer, statement, "method-recommendation"):
            findings.append(
                _finding(
                    "BLIND_SOURCE_PROSE_FORBIDDEN",
                    artifact,
                    pointer,
                    None,
                    "source-dependent prose is forbidden in blind recommendations",
                )
            )


def _finding(
    code: str,
    artifact: AcceptedArtifactClaims,
    pointer: str,
    claim_id: str | None,
    detail: str,
) -> CitationIntegrityFinding:
    return CitationIntegrityFinding(
        code=code,
        artifact_ref=artifact.artifact_ref,
        json_pointer=pointer,
        claim_id=claim_id,
        detail=detail,
    )


def _string_leaves(
    payload: object, pointer: tuple[str, ...] = ()
) -> Iterator[tuple[str, str]]:
    if isinstance(payload, str):
        yield ("" if not pointer else "/" + "/".join(pointer)), payload
    elif isinstance(payload, Mapping):
        for key in sorted(payload, key=str):
            if not isinstance(key, str):
                raise TypeError("recommendation payload keys must be strings")
            yield from _string_leaves(payload[key], (*pointer, key))
    elif isinstance(payload, Sequence) and not isinstance(payload, (bytes, bytearray)):
        for index, value in enumerate(payload):
            yield from _string_leaves(value, (*pointer, str(index)))
