"""Bounded regressions for the shared V0.3.1-to-paper lock order."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from paper_argument_fixtures import candidate as argument_candidate
from paper_draft_integration_fixtures import build_stack

from envresearch.paper.argument_map import ARGUMENT_MAP_SUBJECT
from envresearch.paper.draft_builder import PAPER_DRAFT_SUBJECT
from envresearch.paper.ledger import CLAIM_LEDGER_SUBJECT


@pytest.mark.parametrize("operation", ("map-build", "draft-publish", "draft-status"))
def test_external_authorities_enter_before_every_paper_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """Fail if a public paper path can acquire paper state before its authorities."""
    stack = build_stack(tmp_path)
    events: list[str] = []
    resolver = stack.ledger_service.resolver
    citation = stack.citation_authority
    registry = stack.draft_service.registry
    original_resolver = resolver.authority_lease
    original_citation = citation.authority_lease
    original_lock = registry.lock

    @contextmanager
    def resolver_lease():  # type: ignore[no-untyped-def]
        events.append("enter:resolver")
        with original_resolver():
            yield
        events.append("exit:resolver")

    @contextmanager
    def citation_lease():  # type: ignore[no-untyped-def]
        events.append("enter:citation")
        with original_citation():
            yield
        events.append("exit:citation")

    @contextmanager
    def paper_lock(subject: str):
        events.append(f"enter:{subject}")
        with original_lock(subject):
            yield
        events.append(f"exit:{subject}")

    monkeypatch.setattr(resolver, "authority_lease", resolver_lease)
    monkeypatch.setattr(citation, "authority_lease", citation_lease)
    monkeypatch.setattr(registry, "lock", paper_lock)
    try:
        if operation == "map-build":
            stack.map_service.build(stack.ledger_ref, argument_candidate())
        elif operation == "draft-publish":
            stack.draft_service.publish(
                stack.candidate,
                map_ref=stack.map_ref,
                ledger_ref=stack.ledger_ref,
                citation_report_ref=stack.report_ref,
            )
        else:
            draft_ref = stack.draft_service.publish(
                stack.candidate,
                map_ref=stack.map_ref,
                ledger_ref=stack.ledger_ref,
                citation_report_ref=stack.report_ref,
            )
            events.clear()
            stack.draft_service.status(
                draft_ref, map_ref=stack.map_ref, ledger_ref=stack.ledger_ref
            )

        paper_entries = [
            events.index(f"enter:{subject}")
            for subject in (
                CLAIM_LEDGER_SUBJECT,
                ARGUMENT_MAP_SUBJECT,
                PAPER_DRAFT_SUBJECT,
            )
            if f"enter:{subject}" in events
        ]
        assert paper_entries
        assert events.index("enter:resolver") < min(paper_entries)
        if operation.startswith("draft"):
            assert events.index("enter:citation") < min(paper_entries)
            assert events.index("enter:resolver") < events.index("enter:citation")
    finally:
        stack.orchestrator.close()
