"""Strict standalone lineage and nested-instance audit contract regressions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from test_paper_audit import _report

from envresearch.paper.audit_contracts import PaperAuditFinding, PaperAuditReport


def test_report_contract_requires_explicit_complete_lineage_commitment() -> None:
    clean = _report(blocked=False)
    primary = tuple(
        sorted(
            {
                clean.draft_ref,
                clean.map_ref,
                clean.ledger_ref,
                clean.citation_report_ref,
            },
            key=lambda item: (
                item.artifact_id,
                item.artifact_version,
                item.content_hash,
            ),
        )
    )
    with pytest.raises(ValidationError):
        PaperAuditReport.model_validate(
            {**clean.model_dump(mode="python"), "transitive_refs": primary}
        )
    forged_roles = {
        name: (clean.draft_ref,)
        for name in (
            "transition_refs",
            "snapshot_refs",
            "citation_source_refs",
            "claim_fact_map_refs",
            "blinded_brief_refs",
            "accepted_artifact_refs",
        )
    }
    with pytest.raises(ValidationError):
        PaperAuditReport.model_validate(
            {
                **clean.model_dump(mode="python"),
                "transitive_refs": primary,
                **forged_roles,
            }
        )


def test_report_rejects_semantically_swapped_lineage_roles() -> None:
    report = _report(blocked=False)
    with pytest.raises(ValidationError):
        PaperAuditReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "transition_refs": report.snapshot_refs,
                "snapshot_refs": report.transition_refs,
            }
        )


def test_report_revalidates_nested_evidence_and_target_instances() -> None:
    finding = _report().findings[0]
    forged_analysis = finding.analysis_refs[0].model_copy(update={"generation": "1"})
    forged_output = finding.output_refs[0].model_copy(update={"size_bytes": "1"})
    forged_target = finding.target.model_copy(update={"paragraph_id": "BAD"})
    for update in (
        {"analysis_refs": (forged_analysis,)},
        {"output_refs": (forged_output,)},
        {"target": forged_target},
    ):
        with pytest.raises(ValidationError):
            PaperAuditFinding.model_validate(
                {**finding.model_dump(mode="python"), **update}
            )


def test_report_requires_each_finding_to_bind_complete_lineage() -> None:
    report = _report()
    finding = report.findings[0].model_copy(
        update={
            "upstream_refs": tuple(
                sorted(
                    (report.draft_ref, report.ledger_ref), key=lambda item: str(item)
                )
            )
        }
    )
    with pytest.raises(ValidationError):
        PaperAuditReport.model_validate(
            {**report.model_dump(mode="python"), "findings": (finding,)}
        )
