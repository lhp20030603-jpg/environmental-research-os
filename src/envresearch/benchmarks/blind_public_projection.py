"""Canonical public projections for source-blind benchmark handoffs."""

from __future__ import annotations

from typing import Final, cast

from envresearch.models.benchmark_blinding import BlindedBrief

PUBLIC_BRIEF_EXCLUDED_FIELDS: Final = frozenset(
    {"masker_principal", "source_sheet_ref"}
)


def public_brief_payload(brief: BlindedBrief) -> dict[str, object]:
    """Return exactly the semantic brief fields visible to recommenders."""
    return cast(
        dict[str, object],
        brief.model_dump(mode="json", exclude=set(PUBLIC_BRIEF_EXCLUDED_FIELDS)),
    )
