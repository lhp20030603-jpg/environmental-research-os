"""Canonical durable representation for claim-integrity validation reports."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_claims import ClaimUsage
from envresearch.models.benchmark_evaluation import AcceptedArtifactClaims


@dataclass(frozen=True, slots=True)
class CitationIntegrityFinding:
    """One deterministic reason an accepted statement cannot be released."""

    code: str
    artifact_ref: ArtifactRef
    json_pointer: str
    claim_id: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class AcceptedArtifactBinding:
    """Exact accepted generation, payload leaves, and reviewed claim usages."""

    artifact_ref: ArtifactRef
    payload_leaf_hashes: tuple[tuple[str, str], ...]
    usages: tuple[ClaimUsage, ...]


@dataclass(frozen=True, slots=True)
class CitationIntegrityReport:
    """The fail-closed result of validating accepted source-dependent content."""

    findings: tuple[CitationIntegrityFinding, ...]
    passed: bool
    validator_version: str
    source_sheet_refs: tuple[ArtifactRef, ...] = ()
    claim_fact_map_refs: tuple[ArtifactRef, ...] = ()
    blinded_brief_refs: tuple[ArtifactRef, ...] = ()
    accepted_artifact_refs: tuple[ArtifactRef, ...] = ()
    accepted_artifact_bindings: tuple[AcceptedArtifactBinding, ...] = ()
    binding_sha256: str = ""


def accepted_artifact_binding(
    artifact: AcceptedArtifactClaims,
) -> AcceptedArtifactBinding:
    """Reduce a payload to canonical leaf hashes while retaining exact usages."""
    leaves = tuple(
        (pointer, hashlib.sha256(value.encode()).hexdigest())
        for pointer, value in _string_leaves(artifact.payload)
    )
    usages = tuple(
        sorted(
            artifact.usages,
            key=lambda item: (
                item.json_pointer,
                item.claim_id,
                item.statement_sha256,
            ),
        )
    )
    return AcceptedArtifactBinding(
        artifact_ref=artifact.artifact_ref,
        payload_leaf_hashes=leaves,
        usages=usages,
    )


def binding_sha256(
    source_refs: tuple[ArtifactRef, ...],
    map_refs: tuple[ArtifactRef, ...],
    brief_refs: tuple[ArtifactRef, ...],
    bindings: tuple[AcceptedArtifactBinding, ...],
    validator_version: str,
) -> str:
    """Hash sorted exact refs, accepted leaf hashes, usages, and validator."""
    value = {
        "source_sheet_refs": [_ref(item) for item in sorted(source_refs, key=str)],
        "claim_fact_map_refs": [_ref(item) for item in sorted(map_refs, key=str)],
        "blinded_brief_refs": [_ref(item) for item in sorted(brief_refs, key=str)],
        "accepted_artifact_bindings": [
            {
                "artifact_ref": _ref(item.artifact_ref),
                "payload_leaf_hashes": [
                    list(leaf) for leaf in item.payload_leaf_hashes
                ],
                "usages": [usage.model_dump(mode="json") for usage in item.usages],
            }
            for item in sorted(bindings, key=lambda item: str(item.artifact_ref))
        ],
        "validator_version": validator_version,
    }
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()


def report_binding_is_valid(report: CitationIntegrityReport) -> bool:
    """Verify structural coverage and the canonical report binding."""
    groups = (
        report.source_sheet_refs,
        report.claim_fact_map_refs,
        report.blinded_brief_refs,
        report.accepted_artifact_refs,
        tuple(item.artifact_ref for item in report.accepted_artifact_bindings),
    )
    if any(not refs or len(refs) != len(set(refs)) for refs in groups):
        return False
    if any(refs != tuple(sorted(refs, key=str)) for refs in groups):
        return False
    if groups[3] != groups[4]:
        return False
    for binding in report.accepted_artifact_bindings:
        leaves = dict(binding.payload_leaf_hashes)
        if len(leaves) != len(binding.payload_leaf_hashes) or any(
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            for digest in leaves.values()
        ):
            return False
        for usage in binding.usages:
            if leaves.get(usage.json_pointer) != usage.statement_sha256:
                return False
    return report.binding_sha256 == binding_sha256(
        report.source_sheet_refs,
        report.claim_fact_map_refs,
        report.blinded_brief_refs,
        report.accepted_artifact_bindings,
        report.validator_version,
    )


def report_payload(report: CitationIntegrityReport) -> dict[str, object]:
    """Serialize the canonical report body for lifecycle sealing."""
    return {
        "findings": [
            {
                "code": item.code,
                "artifact_ref": _ref(item.artifact_ref),
                "json_pointer": item.json_pointer,
                "claim_id": item.claim_id,
                "detail": item.detail,
            }
            for item in report.findings
        ],
        "passed": report.passed,
        "validator_version": report.validator_version,
        "source_sheet_refs": [_ref(item) for item in report.source_sheet_refs],
        "claim_fact_map_refs": [_ref(item) for item in report.claim_fact_map_refs],
        "blinded_brief_refs": [_ref(item) for item in report.blinded_brief_refs],
        "accepted_artifact_refs": [
            _ref(item) for item in report.accepted_artifact_refs
        ],
        "accepted_artifact_bindings": [
            {
                "artifact_ref": _ref(item.artifact_ref),
                "payload_leaf_hashes": [
                    list(leaf) for leaf in item.payload_leaf_hashes
                ],
                "usages": [usage.model_dump(mode="json") for usage in item.usages],
            }
            for item in report.accepted_artifact_bindings
        ],
        "binding_sha256": report.binding_sha256,
    }


def report_from_payload(payload: object) -> CitationIntegrityReport:
    """Strictly reconstruct one sealed report for independent verification."""
    if not isinstance(payload, dict):
        raise TypeError("citation integrity report payload is invalid")
    if set(payload) != {
        "findings",
        "passed",
        "validator_version",
        "source_sheet_refs",
        "claim_fact_map_refs",
        "blinded_brief_refs",
        "accepted_artifact_refs",
        "accepted_artifact_bindings",
        "binding_sha256",
    }:
        raise ValueError("citation integrity report fields are invalid")
    refs = {
        field: _refs(payload.get(field), field)
        for field in (
            "source_sheet_refs",
            "claim_fact_map_refs",
            "blinded_brief_refs",
            "accepted_artifact_refs",
        )
    }
    raw_bindings = payload.get("accepted_artifact_bindings")
    if not isinstance(raw_bindings, list):
        raise TypeError("accepted artifact bindings are invalid")
    bindings = tuple(_binding(item) for item in raw_bindings)
    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        raise TypeError("citation findings are invalid")
    findings = tuple(_finding(item) for item in raw_findings)
    passed = payload.get("passed")
    version = payload.get("validator_version")
    digest = payload.get("binding_sha256")
    if (
        type(passed) is not bool
        or not isinstance(version, str)
        or not isinstance(digest, str)
    ):
        raise TypeError("citation report verdict or identity is invalid")
    return CitationIntegrityReport(
        findings=findings,
        passed=passed,
        validator_version=version,
        source_sheet_refs=refs["source_sheet_refs"],
        claim_fact_map_refs=refs["claim_fact_map_refs"],
        blinded_brief_refs=refs["blinded_brief_refs"],
        accepted_artifact_refs=refs["accepted_artifact_refs"],
        accepted_artifact_bindings=bindings,
        binding_sha256=digest,
    )


def payload_leaf_hashes(payload: object) -> tuple[tuple[str, str], ...]:
    """Return the same canonical leaf identity used by accepted bindings."""
    return tuple(
        (pointer, hashlib.sha256(value.encode()).hexdigest())
        for pointer, value in _string_leaves(payload)
    )


def report_input_refs(report: CitationIntegrityReport) -> tuple[ArtifactRef, ...]:
    """Return every exact cited generation recorded in the sealed envelope."""
    return (
        *report.source_sheet_refs,
        *report.claim_fact_map_refs,
        *report.blinded_brief_refs,
        *report.accepted_artifact_refs,
    )


def _string_leaves(
    payload: object, pointer: tuple[str, ...] = ()
) -> Iterator[tuple[str, str]]:
    if isinstance(payload, str):
        yield _pointer(pointer), payload
    elif isinstance(payload, Mapping):
        for key in sorted(payload, key=str):
            if not isinstance(key, str):
                raise TypeError("accepted payload mappings require string keys")
            yield from _string_leaves(payload[key], (*pointer, key))
    elif isinstance(payload, Sequence) and not isinstance(payload, (bytes, bytearray)):
        for index, value in enumerate(payload):
            yield from _string_leaves(value, (*pointer, str(index)))


def _pointer(parts: tuple[str, ...]) -> str:
    return (
        ""
        if not parts
        else "/"
        + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)
    )


def _ref(value: ArtifactRef) -> dict[str, object]:
    return value.model_dump(mode="json")


def _refs(value: object, field: str) -> tuple[ArtifactRef, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{field} is invalid")
    return tuple(ArtifactRef.model_validate(item) for item in value)


def _binding(value: object) -> AcceptedArtifactBinding:
    if not isinstance(value, dict):
        raise TypeError("accepted artifact binding is invalid")
    raw_leaves = value.get("payload_leaf_hashes")
    raw_usages = value.get("usages")
    if not isinstance(raw_leaves, list) or not isinstance(raw_usages, list):
        raise TypeError("accepted artifact binding details are invalid")
    leaves: list[tuple[str, str]] = []
    for leaf in raw_leaves:
        if (
            not isinstance(leaf, list)
            or len(leaf) != 2
            or not all(isinstance(item, str) for item in leaf)
        ):
            raise TypeError("accepted artifact leaf hash is invalid")
        leaves.append((leaf[0], leaf[1]))
    return AcceptedArtifactBinding(
        artifact_ref=ArtifactRef.model_validate(value.get("artifact_ref")),
        payload_leaf_hashes=tuple(leaves),
        usages=tuple(ClaimUsage.model_validate(item) for item in raw_usages),
    )


def _finding(value: object) -> CitationIntegrityFinding:
    if not isinstance(value, dict):
        raise TypeError("citation finding is invalid")
    code = value.get("code")
    pointer = value.get("json_pointer")
    claim_id = value.get("claim_id")
    detail = value.get("detail")
    if (
        not isinstance(code, str)
        or not isinstance(pointer, str)
        or claim_id is not None
        and not isinstance(claim_id, str)
        or not isinstance(detail, str)
    ):
        raise TypeError("citation finding fields are invalid")
    return CitationIntegrityFinding(
        code=code,
        artifact_ref=ArtifactRef.model_validate(value.get("artifact_ref")),
        json_pointer=pointer,
        claim_id=claim_id,
        detail=detail,
    )
