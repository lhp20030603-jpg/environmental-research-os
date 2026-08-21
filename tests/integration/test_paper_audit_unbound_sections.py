"""End-to-end closed-boundary tests for unbound title and question prose."""

from pathlib import Path

import pytest
from paper_audit_integration_support import audit_service
from paper_draft_integration_fixtures import build_stack

from envresearch.paper.draft_contracts import PaperDraftCandidate


@pytest.mark.parametrize(
    ("section", "text"),
    (
        ("title", "Regulators ought to implement this program nationwide."),
        ("research-question", "Should this apply to every household?"),
    ),
)
def test_public_draft_can_publish_but_audit_blocks_unbound_policy_prose(
    tmp_path: Path, section: str, text: str
) -> None:
    stack = build_stack(tmp_path)
    try:
        payload = stack.candidate.model_dump(mode="python")
        payload["paragraphs"] = tuple(
            {**paragraph, "text": text}
            if paragraph["section"] == section
            else paragraph
            for paragraph in payload["paragraphs"]
        )
        candidate = PaperDraftCandidate.model_validate(payload)
        draft_ref = stack.draft_service.publish(
            candidate,
            map_ref=stack.map_ref,
            ledger_ref=stack.ledger_ref,
            citation_report_ref=stack.report_ref,
        )

        service = audit_service(stack)
        audit_ref = service.audit(draft_ref)
        report = service.status(audit_ref, draft_ref)

        assert report.verdict == "blocked"
        assert {item.finding_kind for item in report.findings} == {"policy-overclaim"}
        assert {
            (item.target.paragraph_id, item.target.start, item.target.end)
            for item in report.findings
        } == {
            (
                "paper-title" if section == "title" else "research-question",
                0,
                len(text),
            )
        }
    finally:
        stack.orchestrator.close()
