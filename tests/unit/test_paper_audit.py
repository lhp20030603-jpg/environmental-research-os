"""Strict contract tests for independent paper audit artifacts."""

from __future__ import annotations

from hashlib import sha256

import pytest
from paper_draft_fixtures import evidence
from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.paper._audit_store import audit_id, audit_subject
from envresearch.paper.audit_contracts import (
    DraftBindingTarget,
    OutputBindingTarget,
    PaperAuditFinding,
    PaperAuditReport,
    TextSpan,
)


def _ref(name: str, character: str) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=name, artifact_version=1, content_hash=character * 64
    )


def _text_target() -> TextSpan:
    text = "The registered value is 1.25 USD."
    return TextSpan(
        target_type="text-span",
        paragraph_id="results-primary",
        start=0,
        end=len(text),
        text_sha256=sha256(text.encode()).hexdigest(),
    )


def _finding() -> PaperAuditFinding:
    draft_ref = _ref("paper-draft", "1")
    ledger = evidence()[0]
    return PaperAuditFinding(
        finding_id="numeric-contradiction-111111111111",
        finding_kind="numeric-contradiction",
        code="PAPER_SUPPORT_INVALID",
        draft_ref=draft_ref,
        target=_text_target(),
        claim_ids=("contingent-valuation-median-wtp",),
        upstream_refs=(draft_ref, _ref("valuation-core-ledger", "2")),
        analysis_refs=(ledger.claims[0].analysis_ref,),
        output_refs=ledger.claims[0].output_evidence,
    )


def _report(*, blocked: bool = True) -> PaperAuditReport:
    draft_ref = _ref("paper-draft", "1")
    map_ref = _ref("argument-map", "3")
    ledger_ref = _ref("valuation-core-ledger", "2")
    citation_ref = _ref("citation-integrity-report", "4")
    finding = _finding()
    ledger = evidence()[0]
    transition_ref = _ref("valuation-transition-v031", "6")
    snapshot_ref = _ref("local-data-7777777777777777", "7")
    source_ref = _ref("curator-source-sheet", "5")
    fact_map_ref = _ref("claim-fact-map", "8")
    brief_ref = _ref("blinded-brief", "9")
    accepted_ref = _ref("analysis-plan", "a")
    refs = tuple(
        sorted(
            {
                draft_ref,
                map_ref,
                ledger_ref,
                citation_ref,
                transition_ref,
                snapshot_ref,
                source_ref,
                fact_map_ref,
                brief_ref,
                accepted_ref,
                *finding.upstream_refs,
            },
            key=lambda item: (
                item.artifact_id,
                item.artifact_version,
                item.content_hash,
            ),
        )
    )
    if blocked:
        finding = finding.model_copy(update={"upstream_refs": refs})
    return PaperAuditReport(
        schema_version="paper.audit-report.v1",
        audit_id="paper-audit-111111111111",
        producer="paper-builder-auditor-v1",
        draft_ref=draft_ref,
        map_ref=map_ref,
        ledger_ref=ledger_ref,
        citation_report_ref=citation_ref,
        transitive_refs=refs,
        transition_refs=(transition_ref,),
        snapshot_refs=(snapshot_ref,),
        citation_source_refs=(source_ref,),
        claim_fact_map_refs=(fact_map_ref,),
        blinded_brief_refs=(brief_ref,),
        accepted_artifact_refs=(accepted_ref,),
        findings=(finding,) if blocked else (),
        verdict="blocked" if blocked else "clean",
        analysis_refs=(ledger.claims[0].analysis_ref,),
        output_refs=ledger.claims[0].output_evidence,
    )


def test_text_and_output_targets_are_strict_frozen_and_canonical() -> None:
    """Catch permissive spans, output kinds, hashes, identifiers, or mutation."""
    target = _text_target()

    with pytest.raises(ValidationError):
        target.start = 2
    with pytest.raises(ValidationError):
        TextSpan(
            target_type="text-span",
            paragraph_id="Bad ID",
            start=4,
            end=4,
            text_sha256="x" * 64,
            surprise=True,  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        OutputBindingTarget(
            target_type="output-binding",
            kind="chart",  # type: ignore[arg-type]
            binding_id="Bad ID",
        )

    output = OutputBindingTarget(
        target_type="output-binding",
        kind="figure",
        binding_id="valuation-figure",
    )
    assert output.kind == "figure"


def test_draft_binding_target_is_strict_frozen_canonical_and_exported() -> None:
    """Catch lossy targets for dangling or out-of-bounds draft bindings."""
    from envresearch import paper

    target = DraftBindingTarget(
        target_type="claim-binding",
        paragraph_id="results-primary",
        start=0,
        end=151,
        binding_sha256="a" * 64,
    )

    assert paper.DraftBindingTarget is DraftBindingTarget
    assert target.end == 151
    with pytest.raises(ValidationError):
        target.end = 150
    for update in (
        {"surprise": True},
        {"target_type": "output-binding"},
        {"target_type": "draft-binding"},
        {"paragraph_id": "Bad ID"},
        {"start": -1},
        {"start": 1, "end": 1},
        {"binding_sha256": "x" * 64},
        {"start": "0"},
    ):
        with pytest.raises(ValidationError):
            DraftBindingTarget.model_validate(
                {**target.model_dump(mode="python"), **update}
            )

    finding = _finding().model_dump(mode="python")
    rebound = PaperAuditFinding.model_validate({**finding, "target": target})
    assert isinstance(rebound.target, DraftBindingTarget)
    citation = DraftBindingTarget(
        target_type="citation-binding",
        paragraph_id="missing-methods",
        start=0,
        end=42,
        binding_sha256="b" * 64,
    )
    assert citation.target_type == "citation-binding"


def test_finding_requires_typed_target_stable_code_and_canonical_refs() -> None:
    """Catch free-form targets, code drift, or ambiguous upstream provenance."""
    finding = _finding()
    dumped = finding.model_dump(mode="json")

    assert dumped["target"]["target_type"] == "text-span"
    with pytest.raises(ValidationError):
        PaperAuditFinding.model_validate({**dumped, "code": "PAPER_SCOPE_EXCEEDED"})
    with pytest.raises(ValidationError):
        PaperAuditFinding.model_validate(
            {
                **dumped,
                "upstream_refs": [dumped["draft_ref"], dumped["draft_ref"]],
            }
        )
    with pytest.raises(ValidationError):
        PaperAuditFinding.model_validate(
            {**dumped, "target": {"paragraph_id": "results-primary"}}
        )


def test_output_mutation_is_an_integrity_finding() -> None:
    """Catch downgrading a table or figure mutation to ordinary support failure."""
    draft_ref = _ref("paper-draft", "1")
    finding = PaperAuditFinding(
        finding_id="output-evidence-mismatch-222222222222",
        finding_kind="output-evidence-mismatch",
        code="PAPER_INTEGRITY_INVALID",
        draft_ref=draft_ref,
        target=OutputBindingTarget(
            target_type="output-binding", kind="table", binding_id="results-table"
        ),
        claim_ids=("contingent-valuation-median-wtp",),
        upstream_refs=(draft_ref, _ref("valuation-core-ledger", "2")),
        analysis_refs=_finding().analysis_refs,
        output_refs=_finding().output_refs,
    )

    assert finding.code == "PAPER_INTEGRITY_INVALID"


def test_report_binds_exact_authority_and_verdict_to_canonical_findings() -> None:
    """Catch reports that omit authority, reorder findings, or lie about verdict."""
    blocked = _report()
    clean = _report(blocked=False)

    assert blocked.verdict == "blocked"
    assert clean.verdict == "clean"
    with pytest.raises(ValidationError):
        blocked.verdict = "clean"
    with pytest.raises(ValidationError):
        PaperAuditReport.model_validate(
            {**blocked.model_dump(mode="json"), "verdict": "clean"}
        )
    with pytest.raises(ValidationError):
        PaperAuditReport.model_validate(
            {
                **blocked.model_dump(mode="json"),
                "transitive_refs": blocked.model_dump(mode="json")["transitive_refs"][
                    ::-1
                ],
            }
        )
    with pytest.raises(ValidationError):
        PaperAuditReport.model_validate(
            {
                **blocked.model_dump(mode="json"),
                "draft_ref": _ref("other-draft", "9").model_dump(mode="json"),
            }
        )


def test_report_rejects_duplicate_finding_ids_and_coerced_nested_refs() -> None:
    """Catch ambiguous findings or noncanonical exact-reference coercion."""
    blocked = _report()
    dumped = blocked.model_dump(mode="json")

    with pytest.raises(ValidationError):
        PaperAuditReport.model_validate(
            {**dumped, "findings": [dumped["findings"][0]] * 2}
        )
    with pytest.raises(ValidationError):
        PaperAuditReport.model_validate(
            {
                **dumped,
                "map_ref": {**dumped["map_ref"], "artifact_version": "1"},
            }
        )
    forged = blocked.map_ref.model_copy(update={"artifact_version": "1"})
    forged_refs = tuple(
        forged if item == blocked.map_ref else item for item in blocked.transitive_refs
    )
    with pytest.raises(ValidationError):
        PaperAuditReport.model_validate(
            {
                **blocked.model_dump(mode="python"),
                "map_ref": forged,
                "transitive_refs": forged_refs,
            }
        )


def test_report_requires_canonical_typed_analysis_and_output_lineage() -> None:
    """Catch audit reports that collapse exact local evidence into ArtifactRefs."""
    blocked = _report()
    dumped = blocked.model_dump(mode="json")

    assert blocked.analysis_refs
    assert blocked.output_refs
    with pytest.raises(ValidationError):
        PaperAuditReport.model_validate({**dumped, "analysis_refs": []})
    with pytest.raises(ValidationError):
        PaperAuditReport.model_validate(
            {**dumped, "output_refs": dumped["output_refs"][::-1]}
        )
    alien = dumped["analysis_refs"][0].copy()
    alien["analysis_id"] = "alien-analysis"
    alien["relative_path"] = str(alien["relative_path"]).replace(
        "cv-analysis", "alien-analysis"
    )
    output = dumped["output_refs"][0].copy()
    output["analysis_ref"] = alien
    with pytest.raises(ValidationError):
        PaperAuditReport.model_validate({**dumped, "output_refs": [output]})


def test_audit_identity_uses_the_complete_exact_draft_reference() -> None:
    """Catch truncated draft hashes that can substitute another audit authority."""
    left = ArtifactRef(
        artifact_id="paper-draft",
        artifact_version=1,
        content_hash="1" * 16 + "2" * 48,
    )
    right = left.model_copy(update={"content_hash": "1" * 16 + "3" * 48})
    other_id = left.model_copy(update={"artifact_id": "other-paper-draft"})
    other_version = left.model_copy(update={"artifact_version": 2})

    assert (
        len(
            {
                audit_id(left),
                audit_id(right),
                audit_id(other_id),
                audit_id(other_version),
            }
        )
        == 4
    )
    assert (
        len(
            {
                audit_subject(left),
                audit_subject(right),
                audit_subject(other_id),
                audit_subject(other_version),
            }
        )
        == 4
    )


def test_complete_audit_api_is_importable_from_paper_package() -> None:
    """Catch public API omissions that force consumers onto private modules."""
    from envresearch import paper

    assert paper.PaperAuditFinding is PaperAuditFinding
    assert paper.PaperAuditReport is PaperAuditReport
    assert paper.PaperAuditService.__name__ == "PaperAuditService"
    assert paper.TextSpan is TextSpan
    assert paper.OutputBindingTarget is OutputBindingTarget
    assert paper.DraftBindingTarget is DraftBindingTarget
    assert callable(paper.audit_subject)
    assert callable(paper.reconstruct_audit_findings)
