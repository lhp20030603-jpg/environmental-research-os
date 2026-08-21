"""Canonical internal identity bytes for one research orchestrator run."""

from __future__ import annotations

import hashlib
import json

from envresearch.methods.registry import MethodProfileRegistry
from envresearch.models.intake import ResearchBriefPayload
from envresearch.research.workflow import ResearchRunConfig
from envresearch.storage.paths import require_safe_workspace_root

_SET_LIKE_PROFILE_FIELDS = (
    "compatible_estimands",
    "required_data_structures",
    "required_features",
)


def prepare_run(
    config: ResearchRunConfig, brief: ResearchBriefPayload
) -> tuple[ResearchRunConfig, ResearchBriefPayload]:
    """Revalidate immutable inputs and enforce their shared workspace identity."""
    durable_config = ResearchRunConfig.model_validate(config.model_dump())
    durable_brief = ResearchBriefPayload.model_validate(brief.model_dump())
    if durable_config.input_mode is not durable_brief.intake_mode:
        raise ValueError("run input_mode must match the research brief")
    require_safe_workspace_root(durable_config.workspace)
    return durable_config, durable_brief


def serialize_run_config(config: ResearchRunConfig) -> bytes:
    """Return the one canonical byte representation accepted for run identity."""
    return json.dumps(
        config.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def method_profile_digests(registry: MethodProfileRegistry) -> dict[str, str]:
    """Bind every loaded scientific profile to canonical immutable content."""
    return {
        profile_id: hashlib.sha256(
            json.dumps(
                _canonical_profile(profile.model_dump(mode="json")),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        for profile_id, profile in sorted(registry.profiles.items())
    }


def _canonical_profile(payload: dict[str, object]) -> dict[str, object]:
    """Sort unordered profile capabilities while preserving checklist order."""
    canonical = dict(payload)
    for field in _SET_LIKE_PROFILE_FIELDS:
        value = canonical[field]
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise TypeError(f"method profile {field} must serialize as strings")
        canonical[field] = sorted(value)
    return canonical
