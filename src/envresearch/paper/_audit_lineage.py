"""Strict exact-evidence and lineage helpers for paper audit contracts."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ValidationInfo

from envresearch.econometrics.report import LocalAnalysisReference
from envresearch.models.artifact import ArtifactRef
from envresearch.paper.contracts import AnalysisOutputRef

_ROLE_IDS = {
    "transition_refs": "valuation-transition-v031",
    "citation_source_refs": "curator-source-sheet",
    "claim_fact_map_refs": "claim-fact-map",
    "blinded_brief_refs": "blinded-brief",
    "accepted_artifact_refs": "analysis-plan",
}
_SNAPSHOT_ID = re.compile(r"local-data-[0-9a-f]{16}")


def artifact_ref_key(reference: ArtifactRef) -> tuple[str, int, str]:
    return (
        reference.artifact_id,
        reference.artifact_version,
        reference.content_hash,
    )


def analysis_ref_key(
    reference: LocalAnalysisReference,
) -> tuple[str, int, str, str]:
    return (
        reference.analysis_id,
        reference.generation,
        reference.sha256,
        str(reference.relative_path),
    )


def output_ref_key(reference: AnalysisOutputRef) -> tuple[object, ...]:
    return (
        *analysis_ref_key(reference.analysis_ref),
        reference.name,
        reference.sha256,
        reference.size_bytes,
        reference.result_pointers,
    )


def require_lineage_role(field: str, refs: tuple[ArtifactRef, ...]) -> None:
    """Reject canonical references labeled as a different authority role."""
    expected = _ROLE_IDS.get(field)
    valid = (
        all(item.artifact_id == expected for item in refs)
        if expected is not None
        else field == "snapshot_refs"
        and all(_SNAPSHOT_ID.fullmatch(item.artifact_id) for item in refs)
    )
    if not valid:
        raise ValueError(f"audit {field} identity is invalid")


def strict_model_input(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python", round_trip=True)
    return value


def exact_artifact_input(value: object) -> object:
    """Revalidate nested ArtifactRef instances without Python coercion."""
    if isinstance(value, ArtifactRef):
        value = value.model_dump(mode="python")
    fields = ("artifact_id", "artifact_version", "content_hash")
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("exact artifact ref input is malformed")
    if any(
        type(value[field]) is not expected
        for field, expected in zip(fields, (str, int, str), strict=True)
    ):
        raise ValueError("exact artifact ref fields cannot be coerced")
    return value


def exact_output_input(value: object, info: ValidationInfo) -> object:
    if isinstance(value, AnalysisOutputRef):
        return value.model_dump(mode="python")
    if isinstance(value, dict) and info.mode == "json":
        value = dict(value)
        pointers = value.get("result_pointers")
        if isinstance(pointers, list):
            value["result_pointers"] = tuple(pointers)
    return value


ExactAnalysisRef = Annotated[
    LocalAnalysisReference, BeforeValidator(strict_model_input)
]
ExactArtifactRef = Annotated[ArtifactRef, BeforeValidator(exact_artifact_input)]
ExactOutputRef = Annotated[AnalysisOutputRef, BeforeValidator(exact_output_input)]

__all__ = [
    "ExactAnalysisRef",
    "ExactArtifactRef",
    "ExactOutputRef",
    "analysis_ref_key",
    "artifact_ref_key",
    "exact_artifact_input",
    "output_ref_key",
    "require_lineage_role",
    "strict_model_input",
]
