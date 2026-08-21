"""Strict contracts for replaceable research workers."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from envresearch.kernel.task_identity import payload_hash
from envresearch.models.artifact import ArtifactRef, ProducerIdentity
from envresearch.models.principal import PrincipalAssignment

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SCHEMA_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RESERVED_NAMESPACES = frozenset(
    {"artifacts", "decisions", "node-checkpoints", "work-orders", "worker-submissions"}
)
_RESERVED_INTERNAL_NAMES = frozenset(
    {
        "locks",
        "orders",
        "queue.key",
        "receipt.json",
        "receipts",
        "staging",
        "transactions",
    }
)
_PORTABLE_NAME_MAX = 255
_WINDOWS_DEVICE_NAMES = frozenset(
    {"aux", "con", "nul", "prn"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)
_STRICT_FROZEN = ConfigDict(
    extra="forbid",
    frozen=True,
    strict=True,
    validate_default=True,
    revalidate_instances="always",
)


class WorkerRole(StrEnum):
    """Exact provider-neutral responsibilities supported by the workflow."""

    RESEARCH_FRAMER = "research-framer"
    LITERATURE_CARTOGRAPHER = "literature-cartographer"
    DATA_SCOUT = "data-scout"
    ESTIMAND_DESIGNER = "estimand-designer"
    METHOD_STRATEGIST = "method-strategist"
    DESIGN_CRITIC = "independent-design-critic"
    PLAN_COMPOSER = "analysis-plan-composer"
    BENCHMARK_CURATOR = "benchmark-curator"
    BENCHMARK_MASKER = "benchmark-masker"
    BENCHMARK_LEAKAGE_VALIDATOR = "benchmark-leakage-validator"
    BENCHMARK_RECOMMENDER = "benchmark-recommender"
    BENCHMARK_EXPERT = "benchmark-expert"
    BENCHMARK_ADJUDICATOR = "benchmark-adjudicator"


class WorkOrder(BaseModel):
    """Immutable content-addressed instructions issued to one worker role."""

    model_config = _STRICT_FROZEN

    order_id: str
    order_hash: str | None = None
    node_id: str
    node_version: str
    role: WorkerRole
    input_artifacts: tuple[ArtifactRef, ...]
    expected_output_schema: str
    expected_output_filenames: tuple[str, ...]
    policy_constraints: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    principal_assignment: PrincipalAssignment | None = None

    @field_validator("order_id")
    @classmethod
    def require_order_id(cls, value: str) -> str:
        """Require a portable identity for every derived order entry."""
        return require_safe_order_id(value)

    @field_validator("node_id")
    @classmethod
    def require_safe_id(cls, value: str) -> str:
        """Require one canonical filename-safe identity segment."""
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("ID must be a canonical safe filename segment")
        return value

    @field_validator("node_version")
    @classmethod
    def require_node_version(cls, value: str) -> str:
        """Keep node versions stable, nonblank, and path independent."""
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("node version must be a canonical identifier")
        return value

    @field_validator("expected_output_schema")
    @classmethod
    def require_schema_id(cls, value: str) -> str:
        """Require an explicit canonical schema identifier."""
        return require_schema_identifier(value)

    @field_validator("expected_output_filenames")
    @classmethod
    def require_output_filenames(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Restrict candidate targets to distinct safe basenames."""
        if not value:
            raise ValueError("expected output filenames must not be empty")
        if len({filename.casefold() for filename in value}) != len(value):
            raise ValueError("expected output filenames must be unique")
        for filename in value:
            require_candidate_filename(filename)
        return value

    @field_validator("policy_constraints", "evidence_requirements")
    @classmethod
    def require_unique_statements(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject ambiguous, blank, or repeated textual requirements."""
        if len(set(value)) != len(value):
            raise ValueError("requirements must be unique")
        if any(not item.strip() or item != item.strip() for item in value):
            raise ValueError("requirements must be nonblank canonical strings")
        return value

    @field_validator("input_artifacts", mode="before")
    @classmethod
    def require_strict_input_refs(
        cls, value: object, info: ValidationInfo
    ) -> tuple[ArtifactRef, ...]:
        """Validate nested provenance before Pydantic can coerce its scalars."""
        if not isinstance(value, tuple) and not (
            info.mode == "json" and isinstance(value, list)
        ):
            raise ValueError("input artifact references must be an immutable tuple")
        validated: list[ArtifactRef] = []
        for reference in value:
            if isinstance(reference, ArtifactRef):
                payload: object = dict(reference.__dict__)
            elif isinstance(reference, Mapping):
                payload = dict(reference)
            else:
                raise TypeError("input artifact reference must be an object")
            validated.append(ArtifactRef.model_validate(payload, strict=True))
        return tuple(validated)

    @field_validator("input_artifacts")
    @classmethod
    def require_input_refs(
        cls, value: tuple[ArtifactRef, ...]
    ) -> tuple[ArtifactRef, ...]:
        """Revalidate and de-duplicate immutable provenance references."""
        validated: list[ArtifactRef] = []
        identities: set[tuple[str, int, str]] = set()
        for reference in value:
            durable = ArtifactRef.model_validate(dict(reference.__dict__))
            if not _SAFE_ID.fullmatch(durable.artifact_id):
                raise ValueError("artifact ID must be a canonical safe identifier")
            identity = (
                durable.artifact_id,
                durable.artifact_version,
                durable.content_hash,
            )
            if identity in identities:
                raise ValueError("input artifact references must be unique")
            identities.add(identity)
            validated.append(durable)
        return tuple(validated)

    @field_validator("order_hash")
    @classmethod
    def require_order_hash(cls, value: str | None) -> str | None:
        """Accept only a canonical digest when the caller supplies one."""
        if value is not None and not _SHA256.fullmatch(value):
            raise ValueError("order hash must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def bind_order_hash(self) -> Self:
        """Compute the order identity or reject a supplied mismatching identity."""
        digest = work_order_hash(self)
        if self.order_hash is not None and self.order_hash != digest:
            raise ValueError("work order hash mismatch")
        object.__setattr__(self, "order_hash", digest)
        return self


class WorkerSubmission(BaseModel):
    """Queue-authored receipt for one or more untrusted candidate files."""

    model_config = _STRICT_FROZEN

    order_id: str
    order_hash: str
    producer: ProducerIdentity
    candidate_relative_paths: tuple[Path, ...]
    candidate_sha256: tuple[str, ...]
    claimed_schema: str
    submitted_at: datetime
    principal_assignment: PrincipalAssignment | None = None

    @field_validator("order_id")
    @classmethod
    def require_safe_order_id(cls, value: str) -> str:
        """Bind receipts to a canonical order filename segment."""
        return require_safe_order_id(value)

    @field_validator("order_hash")
    @classmethod
    def require_sha256_order_hash(cls, value: str) -> str:
        """Require the full immutable order identity."""
        if not _SHA256.fullmatch(value):
            raise ValueError("order hash must be a lowercase SHA-256 digest")
        return value

    @field_validator("producer")
    @classmethod
    def require_strict_producer(cls, value: ProducerIdentity) -> ProducerIdentity:
        """Revalidate nested identities and reject blank attribution."""
        return revalidate_producer_identity(value)

    @field_validator("candidate_relative_paths")
    @classmethod
    def require_candidate_paths(cls, value: tuple[Path, ...]) -> tuple[Path, ...]:
        """Confine every receipt path to the isolated submission namespace."""
        if not value or len(set(value)) != len(value):
            raise ValueError("candidate paths must be nonempty and unique")
        for path in value:
            if (
                path.is_absolute()
                or ".." in path.parts
                or len(path.parts) != 5
                or path.parts[0] != "worker-submissions"
                or path.parts[2] != "transactions"
                or path.parts[3] != f"{path.name}.submission"
            ):
                raise ValueError("candidate path must be safe and queue-relative")
            require_candidate_filename(path.name)
        return value

    @field_validator("candidate_sha256")
    @classmethod
    def require_candidate_hashes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require queue-computed canonical byte digests."""
        if not value or any(not _SHA256.fullmatch(digest) for digest in value):
            raise ValueError("candidate hash must be a lowercase SHA-256 digest")
        return value

    @field_validator("claimed_schema")
    @classmethod
    def require_claimed_schema(cls, value: str) -> str:
        """Keep the worker claim explicit and canonical."""
        return require_schema_identifier(value)

    @field_validator("submitted_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """Require the repository's canonical UTC representation."""
        if value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
            raise ValueError("timestamps must be UTC-aware")
        return value

    @model_validator(mode="after")
    def require_aligned_candidates(self) -> Self:
        """Keep paths and hashes aligned and bound to this order identity."""
        if len(self.candidate_relative_paths) != len(self.candidate_sha256):
            raise ValueError("candidate paths and hashes must have equal length")
        if any(
            path.parts[1] != self.order_id for path in self.candidate_relative_paths
        ):
            raise ValueError("candidate path order ID mismatch")
        return self


def work_order_hash(order: WorkOrder) -> str:
    """Return the canonical SHA-256 over every non-hash order field."""
    canonical = order.model_dump(mode="json", exclude={"order_hash"})
    return payload_hash(canonical)


def require_schema_identifier(value: str) -> str:
    """Validate one portable schema name without normalization."""
    if not _SCHEMA_ID.fullmatch(value):
        raise ValueError("schema must be a nonblank canonical identifier")
    return value


def require_candidate_filename(value: str) -> str:
    """Validate one queue target basename and reject reserved namespaces."""
    path = Path(value)
    if (
        path.is_absolute()
        or len(path.parts) != 1
        or path.name != value
        or not _SAFE_ID.fullmatch(value)
    ):
        raise ValueError("output filename must be a canonical safe basename")
    if value.casefold() in _RESERVED_NAMESPACES | _RESERVED_INTERNAL_NAMES:
        raise ValueError("output filename targets a reserved namespace")
    _require_portable_component(value, description="output filename")
    if len(os.fsencode(f"{value}.submission")) > _PORTABLE_NAME_MAX:
        raise ValueError("output filename exceeds the filesystem byte limit")
    return value


def require_safe_order_id(value: str) -> str:
    """Validate an order identity at non-model filesystem boundaries."""
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError("order ID must be a canonical safe filename segment")
    _require_portable_component(value, description="order ID")
    if len(os.fsencode(f"{value}.filelock")) > _PORTABLE_NAME_MAX:
        raise ValueError("order ID exceeds the filesystem byte limit")
    return value


def _require_portable_component(value: str, *, description: str) -> None:
    if value.endswith((".", " ")) or value.split(".", maxsplit=1)[0].casefold() in (
        _WINDOWS_DEVICE_NAMES
    ):
        raise ValueError(f"{description} is not portable")


def revalidate_work_order_instance(order: WorkOrder) -> WorkOrder:
    """Defeat forged model copies before they cross a persistence boundary."""
    if order.order_hash is None:
        raise ValueError("work order hash is missing")
    try:
        return WorkOrder.model_validate(dict(order.__dict__))
    except ValidationError as error:
        if "work order hash mismatch" in str(error):
            raise ValueError("work order hash mismatch") from error
        raise


def revalidate_producer_identity(producer: ProducerIdentity) -> ProducerIdentity:
    """Return a strict, attributable copy of a possibly forged producer model."""
    durable = ProducerIdentity.model_validate(dict(producer.__dict__))
    if not _SAFE_ID.fullmatch(durable.component):
        raise ValueError("producer component must be a canonical identifier")
    if not durable.version.strip() or durable.version != durable.version.strip():
        raise ValueError("producer version must be nonblank and canonical")
    for optional in (durable.model, durable.runtime, durable.context_id):
        if optional is not None and (
            not optional.strip() or optional != optional.strip()
        ):
            raise ValueError("producer details must be nonblank and canonical")
    return durable


def require_bound_order_hash(order: WorkOrder) -> str:
    """Narrow the validated optional construction field at persistence boundaries."""
    if order.order_hash is None:
        raise ValueError("work order hash is missing")
    return order.order_hash
