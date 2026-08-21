"""Raw draft-bound output authentication at the public audit boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
from paper_audit_integration_support import (
    audit_service,
    install_reopening_resolver,
    publish_draft,
)
from paper_draft_integration_fixtures import build_stack

from envresearch.paper.auditor import audit_subject
from envresearch.paper.errors import PaperIntegrityInvalid


@pytest.mark.parametrize("kind", ("table", "figure"))
def test_audit_reopens_and_rejects_real_draft_bound_output_bytes(
    tmp_path: Path, kind: str
) -> None:
    """Catch cached reports that hide mutation of draft-bound output bytes."""
    stack = build_stack(tmp_path)
    try:
        draft_ref = publish_draft(stack)
        analysis_root, report = install_reopening_resolver(stack)
        binding = (
            stack.candidate.tables[0] if kind == "table" else stack.candidate.figures[0]
        )
        evidence = next(
            item for item in report.outputs if item.name == binding.output.name
        )
        output_path = analysis_root / evidence.relative_path
        output_path.chmod(0o600)
        output_path.write_bytes(output_path.read_bytes() + b"mutated")
        service = audit_service(stack)

        with pytest.raises(PaperIntegrityInvalid):
            service.audit(draft_ref)

        assert service.registry.current(audit_subject(draft_ref)) is None
    finally:
        stack.orchestrator.close()
