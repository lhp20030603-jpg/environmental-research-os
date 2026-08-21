"""Generation-aware immutable draft storage before revision orchestration."""

from __future__ import annotations

from pathlib import Path

import pytest
from paper_draft_fixtures import materialized_draft

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.models.artifact import ArtifactRef
from envresearch.paper._draft_store import DraftStore, draft_id
from envresearch.paper.draft_contracts import PaperDraft
from envresearch.paper.errors import PaperIntegrityInvalid


def _successor() -> PaperDraft:
    draft = materialized_draft()[0]
    stable_id = draft_id(draft.map_ref, draft.ledger_ref, draft.citation_report_ref)
    return PaperDraft.model_validate(
        {
            **draft.model_dump(mode="python"),
            "draft_id": stable_id,
            "generation": 2,
            "predecessor_ref": ArtifactRef(
                artifact_id=stable_id,
                artifact_version=1,
                content_hash="1" * 64,
            ),
        }
    )


def test_draft_store_loads_exact_next_generation_and_rejects_version_alias(
    tmp_path: Path,
) -> None:
    registry = ExitRegistry((tmp_path / "paper").resolve())
    store = DraftStore(registry)
    successor = _successor()
    exact = registry.publish(successor.draft_id, successor, version=2)
    alias = registry.publish(successor.draft_id, successor, version=1)

    assert store.load(exact) == successor
    with pytest.raises(PaperIntegrityInvalid, match="identity"):
        store.load(alias)
