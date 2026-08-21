"""Cycle-safe review closure reopening and withheld-canary scanning."""

from __future__ import annotations

import hashlib
import json
import posixpath
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation._strict import STRICT, ReviewerBehavioralContract
from envresearch.personal_validation.contracts import (
    CompletedFactoryRunTarget,
    PersonalCanonicalCase,
    PersonalValidationAttempt,
)
from envresearch.personal_validation.errors import (
    PersonalValidationAuthorityInvalid,
    PersonalValidationIntegrityInvalid,
)
from envresearch.personal_validation.private_store import PersonalValidationStore

_MAX_LOCATOR_DECODE_PASSES = 8


class WithheldReviewManifest(BaseModel):
    """Exact private values and normalized locators forbidden to reviewers."""

    model_config = STRICT
    expected_behavior_ref: ArtifactRef
    forbidden_values: tuple[str, ...]
    normalized_locators: tuple[str, ...]


class ReviewClosureBundle(Protocol):
    behavioral_contract_ref: ArtifactRef
    target_refs: tuple[ArtifactRef, ...]
    evidence_refs: tuple[ArtifactRef, ...]

    def model_dump_json(self) -> str: ...


def build_withheld_manifest(
    store: PersonalValidationStore,
    case: PersonalCanonicalCase,
    contract: ReviewerBehavioralContract,
) -> WithheldReviewManifest:
    values = {
        case.expected_behavior_ref.artifact_id,
        case.expected_behavior_ref.content_hash,
    }
    category_values = {
        "prior_reviews": {"predecessor_finding", "prior_review"},
        "seeded_location": {"seeded/private/location"},
    }
    for field in contract.withheld_fields:
        values.update(category_values.get(field, set()))
    locators = {
        normalize_locator(str(store.root.lexical_path)),
        normalize_locator(str(store.root.lexical_path.resolve())),
        normalize_locator(
            str(
                store.root.lexical_path
                / "exit/objects"
                / case.expected_behavior_ref.artifact_id
            )
        ),
    }
    return WithheldReviewManifest(
        expected_behavior_ref=case.expected_behavior_ref,
        forbidden_values=tuple(sorted(values)),
        normalized_locators=tuple(sorted(locators)),
    )


def normalize_locator(value: str) -> str:
    normalized = _fully_decode_locator(value)
    parsed = urlsplit(normalized)
    if parsed.scheme and parsed.hostname:
        path = posixpath.normpath(parsed.path or "/")
        query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
        return urlunsplit(
            (parsed.scheme.casefold(), parsed.netloc.casefold(), path, query, "")
        )
    if normalized.startswith("/"):
        return posixpath.normpath(normalized)
    return normalized


def _fully_decode_locator(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).replace("\\", "/")
    for _ in range(_MAX_LOCATOR_DECODE_PASSES):
        decoded = unicodedata.normalize("NFKC", unquote(normalized)).replace("\\", "/")
        if decoded == normalized:
            break
        normalized = decoded
    else:
        raise ValueError("locator percent encoding exceeds normalization limit")
    return normalized


def require_bundle_closure_safe(
    store: PersonalValidationStore,
    case: PersonalCanonicalCase,
    attempt: PersonalValidationAttempt,
    bundle: ReviewClosureBundle,
) -> None:
    contract = store.load(case.reviewer_contract_ref, ReviewerBehavioralContract)
    if contract.case_kind != case.kind:
        raise _integrity("reviewer contract differs from canonical case")
    manifest = build_withheld_manifest(store, case, contract)
    require_manifest_safe_bytes(manifest, bundle.model_dump_json().encode())
    roots = {
        bundle.behavioral_contract_ref,
        *bundle.target_refs,
        *bundle.evidence_refs,
    }
    embedded: dict[ArtifactRef, bytes] = {}
    if isinstance(attempt.target, CompletedFactoryRunTarget):
        embedded[attempt.target.run_ref] = attempt.target.run.model_dump_json().encode()
    _walk_refs(
        store,
        manifest,
        roots,
        embedded,
        protected_authority_refs={
            attempt.case_ref,
            attempt.protocol_ref,
            _attempt_ref(attempt),
            case.reviewer_contract_ref,
        },
    )


def require_review_bytes_safe(
    store: PersonalValidationStore,
    case: PersonalCanonicalCase,
    data: bytes,
) -> None:
    contract = store.load(case.reviewer_contract_ref, ReviewerBehavioralContract)
    require_manifest_safe_bytes(build_withheld_manifest(store, case, contract), data)


def require_manifest_safe_bytes(manifest: WithheldReviewManifest, data: bytes) -> None:
    try:
        decoded = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _integrity("review closure bytes are not canonical JSON") from error
    values = tuple(value.casefold() for value in manifest.forbidden_values if value)
    normalized_values = tuple(normalize_locator(value).casefold() for value in values)
    locators = tuple(
        locator.casefold() for locator in manifest.normalized_locators if locator
    )
    for candidate in _strings(decoded):
        try:
            decoded_candidate = _fully_decode_locator(candidate)
            normalized = normalize_locator(candidate)
        except ValueError as error:
            raise _disclosure() from error
        forms = (
            candidate.casefold(),
            decoded_candidate.casefold(),
            normalized.casefold(),
        )
        if (
            any(value in form for value in values for form in forms)
            or any(value in form for value in normalized_values for form in forms)
            or any(locator in form for locator in locators for form in forms)
        ):
            raise _disclosure()


def _walk_refs(
    store: PersonalValidationStore,
    manifest: WithheldReviewManifest,
    roots: Iterable[ArtifactRef],
    embedded: dict[ArtifactRef, bytes],
    protected_authority_refs: set[ArtifactRef],
) -> None:
    pending = list(roots)
    visited: set[ArtifactRef] = set()
    while pending:
        reference = pending.pop()
        if reference in visited:
            continue
        visited.add(reference)
        if reference == manifest.expected_behavior_ref:
            raise _disclosure()
        if reference in protected_authority_refs:
            require_manifest_safe_bytes(manifest, reference.model_dump_json().encode())
            continue
        data = embedded.get(reference)
        if data is None:
            data = _personal_object_bytes(store, reference)
        if data is None:
            require_manifest_safe_bytes(manifest, reference.model_dump_json().encode())
            continue
        if hashlib.sha256(data).hexdigest() != reference.content_hash:
            raise _integrity("review closure object digest differs")
        require_manifest_safe_bytes(manifest, data)
        try:
            decoded = json.loads(data)
        except json.JSONDecodeError as error:
            raise _integrity("review closure object is not JSON") from error
        pending.extend(_refs(decoded))


def _personal_object_bytes(
    store: PersonalValidationStore, reference: ArtifactRef
) -> bytes | None:
    relative = (
        Path("exit/objects")
        / reference.artifact_id
        / f"v{reference.artifact_version}-{reference.content_hash}.json"
    )
    if not store.objects.exists(relative):
        return None
    return store.objects.read_file(relative, description="review closure object")


def _attempt_ref(attempt: PersonalValidationAttempt) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=attempt.attempt_id,
        artifact_version=1,
        content_hash=hashlib.sha256(attempt.model_dump_json().encode()).hexdigest(),
    )


def _refs(value: Any) -> tuple[ArtifactRef, ...]:
    found: list[ArtifactRef] = []
    if isinstance(value, dict):
        if set(value) == {"artifact_id", "artifact_version", "content_hash"}:
            try:
                found.append(ArtifactRef.model_validate(value))
            except ValueError as error:
                raise _integrity(
                    "review closure contains an invalid reference"
                ) from error
        else:
            for child in value.values():
                found.extend(_refs(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_refs(child))
    return tuple(found)


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                yield key
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def _disclosure() -> PersonalValidationAuthorityInvalid:
    return PersonalValidationAuthorityInvalid(
        "private oracle or canary disclosure is forbidden",
        finding_kind="review-oracle-disclosure",
    )


def _integrity(message: str) -> PersonalValidationIntegrityInvalid:
    return PersonalValidationIntegrityInvalid(
        message, finding_kind="review-closure-invalid"
    )


__all__ = [
    "WithheldReviewManifest",
    "build_withheld_manifest",
    "normalize_locator",
    "require_bundle_closure_safe",
    "require_manifest_safe_bytes",
    "require_review_bytes_safe",
]
