"""Late authenticated-object checks for the claim ledger."""

from __future__ import annotations

from pathlib import Path

import pytest
from paper_claim_fixtures import cv_resolver

from envresearch.paper.errors import PaperIntegrityInvalid
from envresearch.paper.ledger import ClaimLedgerService


def test_status_reopens_ledger_after_final_transition_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver = cv_resolver(tmp_path)
    service = ClaimLedgerService.for_resolver(
        paper_root=tmp_path / "paper", resolver=resolver
    )
    ledger_ref = service.build(resolver.transition_ref)
    path = (
        service.registry.root
        / "exit/objects"
        / ledger_ref.artifact_id
        / f"v{ledger_ref.artifact_version}-{ledger_ref.content_hash}.json"
    )
    original = resolver.require_current
    calls = 0

    def mutate_after_final_transition_check(transition_ref) -> None:
        nonlocal calls
        original(transition_ref)
        calls += 1
        if calls == 2:
            data = path.read_bytes()
            path.chmod(0o600)
            path.write_bytes(
                data.replace(b"paper-builder-ledger-v1", b"attacker-ledger-v1")
            )

    monkeypatch.setattr(
        resolver, "require_current", mutate_after_final_transition_check
    )

    with pytest.raises(PaperIntegrityInvalid):
        service.status(ledger_ref, resolver.transition_ref)

    assert calls == 2
