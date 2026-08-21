"""Stable external-authority lease boundaries for public paper audit calls."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from paper_audit_integration_support import audit_service, publish_draft
from paper_draft_integration_fixtures import build_stack

from envresearch.paper._audit_store import audit_commit_subject, audit_subject
from envresearch.paper.errors import PaperAuthorityInvalid, PaperIntegrityInvalid


@pytest.mark.parametrize("authority", ("transition", "citation"))
@pytest.mark.parametrize("operation", ("audit", "status"))
@pytest.mark.parametrize(
    ("failure", "expected"),
    (
        (OSError("lease storage failed"), PaperIntegrityInvalid),
        (ValueError("lease authority changed"), PaperAuthorityInvalid),
    ),
)
def test_authority_lease_acquisition_has_stable_error_and_no_pointer_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authority: str,
    operation: str,
    failure: Exception,
    expected: type[Exception],
) -> None:
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        service = audit_service(stack)
        audit_ref = service.audit(draft_ref) if operation == "status" else None
        pending = service.registry.current(audit_subject(draft_ref))
        committed = service.registry.current(audit_commit_subject(draft_ref))
        target = (
            stack.ledger_service.resolver
            if authority == "transition"
            else stack.citation_authority
        )

        @contextmanager
        def fail_lease() -> Iterator[None]:
            raise failure
            yield  # pragma: no cover - required generator shape

        monkeypatch.setattr(target, "authority_lease", fail_lease)
        with pytest.raises(expected):
            if operation == "status":
                assert audit_ref is not None
                service.status(audit_ref, draft_ref)
            else:
                service.audit(draft_ref)

        assert service.registry.current(audit_subject(draft_ref)) == pending
        assert service.registry.current(audit_commit_subject(draft_ref)) == committed
    finally:
        stack.orchestrator.close()
