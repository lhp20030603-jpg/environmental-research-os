"""Canonical reference identities for protected citation attestations."""

import hashlib
import json

from envresearch.benchmarks.blind_registry import LoadedBlindCase
from envresearch.models.artifact import ArtifactRef


def ref_payload(value: ArtifactRef) -> dict[str, object]:
    """Return one reference in its canonical JSON representation."""
    return value.model_dump(mode="json")


def case_ref_sha256(case: LoadedBlindCase) -> str:
    """Bind every exact source reference belonging to a blind case."""
    payload = {
        "source_sheet_ref": ref_payload(case.source_ref),
        "claim_fact_map_ref": ref_payload(case.claim_fact_map_ref),
        "blinded_brief_ref": ref_payload(case.brief_ref),
    }
    data = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(data).hexdigest()
