"""Small typed fixtures for Paper Builder draft unit tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from envresearch.benchmarks.claim_report import (
    AcceptedArtifactBinding,
    CitationIntegrityReport,
    binding_sha256,
)
from envresearch.econometrics.report import LocalAnalysisReference
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_claims import (
    ClaimUsage,
    ClaimVerificationStatus,
    CuratorSourceSheet,
    SourceLocator,
    VerifiedClaim,
)
from envresearch.paper.argument_contracts import ArgumentMap, ArgumentNode
from envresearch.paper.citation_authority import (
    CitationAuthoritySnapshot,
    CitationGenerationToken,
)
from envresearch.paper.contracts import (
    AnalysisOutputRef,
    ClaimEvidenceLedger,
    ClaimEvidenceRow,
    ClaimUncertainty,
    EstimatedClaimValue,
)
from envresearch.paper.draft_contracts import (
    CitationBinding,
    ClaimSpanBinding,
    FigureBinding,
    PaperDraft,
    PaperDraftCandidate,
    PaperParagraph,
    TableBinding,
)
from envresearch.paper.draft_validation import render_claim_sentence


def artifact_ref(name: str, character: str) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=name, artifact_version=1, content_hash=character * 64
    )


def evidence() -> tuple[ClaimEvidenceLedger, ArgumentMap, CitationAuthoritySnapshot]:
    digest = "4" * 64
    analysis_ref = LocalAnalysisReference(
        analysis_id="cv-analysis",
        generation=1,
        relative_path=Path(f"analyses/cv-analysis/history/generation-1-{digest}.json"),
        sha256=digest,
    )
    outputs = (
        AnalysisOutputRef(
            analysis_ref=analysis_ref,
            name="estimates.csv",
            sha256="5" * 64,
            size_bytes=120,
            result_pointers=("/welfare/0",),
        ),
        AnalysisOutputRef(
            analysis_ref=analysis_ref,
            name="response-curve.svg",
            sha256="6" * 64,
            size_bytes=240,
            result_pointers=("/figure_sha256",),
        ),
    )
    transition_ref = artifact_ref("valuation-transition-v031", "1")
    claim = ClaimEvidenceRow(
        claim_id="contingent-valuation-median-wtp",
        claim_type="welfare-estimate",
        method_id="contingent-valuation",
        quantity="median-wtp",
        value=EstimatedClaimValue(
            kind="estimate",
            estimate=1.25,
            uncertainty=ClaimUncertainty(
                std_error=0.1,
                confidence_low=1.05,
                confidence_high=1.45,
                confidence_level=0.95,
            ),
        ),
        transition_ref=transition_ref,
        analysis_ref=analysis_ref,
        snapshot_ref=artifact_ref("cv-snapshot", "2"),
        output_evidence=outputs,
        reconstruction_status="independently-reconstructed",
        welfare_transformation="median-wtp",
        unit="USD",
        population_basis="survey respondent",
        time_basis="annual",
        price_base="synthetic-2025-USD",
        allowed_strength="model-conditional-valuation",
        limitations=("The value is conditional on the registered response model.",),
    )
    ledger_ref = artifact_ref("valuation-core-ledger", "7")
    ledger = ClaimEvidenceLedger(
        schema_version="paper.claim-evidence-ledger.v1",
        ledger_id="valuation-core-ledger",
        producer="paper-builder-ledger-v1",
        transition_ref=transition_ref,
        claims=(claim,),
    )
    argument_map = ArgumentMap(
        schema_version="paper.argument-map.v1",
        map_id="argument-map-777777777777",
        producer="paper-builder-argument-map-v1",
        ledger_ref=ledger_ref,
        transition_ref=transition_ref,
        nodes=(
            ArgumentNode(
                node_id="cv-results",
                node_type="empirical-claim",
                proposition=None,
                claim_ids=(claim.claim_id,),
            ),
        ),
        edges=(),
    )
    source = _source_sheet()
    source_ref = artifact_ref("curator-source-sheet", "9")
    report_ref = artifact_ref("citation-integrity-report", "8")
    map_ref = artifact_ref("claim-fact-map", "a")
    brief_ref = artifact_ref("blinded-brief", "b")
    accepted_ref = artifact_ref("analysis-plan", "c")
    statement = "accepted-method"
    statement_hash = __import__("hashlib").sha256(statement.encode()).hexdigest()
    accepted_binding = AcceptedArtifactBinding(
        artifact_ref=accepted_ref,
        payload_leaf_hashes=(("/method", statement_hash),),
        usages=(
            ClaimUsage(
                claim_id="claim-001",
                statement_sha256=statement_hash,
                json_pointer="/method",
            ),
        ),
    )
    report = CitationIntegrityReport(
        findings=(),
        passed=True,
        validator_version="claim-integrity-v1",
        source_sheet_refs=(source_ref,),
        claim_fact_map_refs=(map_ref,),
        blinded_brief_refs=(brief_ref,),
        accepted_artifact_refs=(accepted_ref,),
        accepted_artifact_bindings=(accepted_binding,),
        binding_sha256=binding_sha256(
            (source_ref,),
            (map_ref,),
            (brief_ref,),
            (accepted_binding,),
            "claim-integrity-v1",
        ),
    )
    snapshot = CitationAuthoritySnapshot(
        report=(report_ref, report),
        source_sheets=((source_ref, source),),
        token=CitationGenerationToken(
            report_ref=report_ref,
            report_payload_sha256="d" * 64,
            source_generation=1,
            source_anchor_sha256="e" * 64,
        ),
    )
    return ledger, argument_map, snapshot


def candidate() -> tuple[
    PaperDraftCandidate,
    ClaimEvidenceLedger,
    ArgumentMap,
    CitationAuthoritySnapshot,
]:
    ledger, argument_map, snapshot = evidence()
    claim = ledger.claims[0]
    finding = render_claim_sentence(claim)
    limitation = claim.limitations[0]
    citation_text = snapshot.source_sheets[0][1].claims[0].normalized_claim
    paragraphs = (
        PaperParagraph(
            paragraph_id="paper-title",
            position=0,
            section="title",
            text="Registered contingent valuation evidence",
        ),
        PaperParagraph(
            paragraph_id="research-question",
            position=1,
            section="research-question",
            text="What value is supported within the registered model boundary?",
        ),
        PaperParagraph(
            paragraph_id="methods-source",
            position=2,
            section="methods",
            text=citation_text,
        ),
        PaperParagraph(
            paragraph_id="results-primary",
            position=3,
            section="results",
            text=finding,
        ),
        PaperParagraph(
            paragraph_id="limitations-primary",
            position=4,
            section="limitations",
            text=limitation,
        ),
    )
    finding_binding = ClaimSpanBinding(
        paragraph_id="results-primary",
        start=0,
        end=len(finding),
        claim_ids=(claim.claim_id,),
        purpose="finding",
        allowed_strength=claim.allowed_strength,
        unit=claim.unit,
        population_basis=claim.population_basis,
        time_basis=claim.time_basis,
        price_base=claim.price_base,
    )
    return (
        PaperDraftCandidate(
            paragraphs=paragraphs,
            claim_bindings=(
                finding_binding,
                finding_binding.model_copy(
                    update={
                        "paragraph_id": "limitations-primary",
                        "end": len(limitation),
                        "purpose": "limitation",
                    }
                ),
            ),
            citation_bindings=(
                CitationBinding(
                    paragraph_id="methods-source",
                    start=0,
                    end=len(citation_text),
                    source_sheet_ref=snapshot.source_sheets[0][0],
                    claim_id="claim-001",
                ),
            ),
            tables=(
                TableBinding(
                    kind="table",
                    binding_id="results-table",
                    claim_ids=(claim.claim_id,),
                    artifact_path="outputs/estimates.csv",
                    caption=(
                        "Registered table output for claim "
                        "contingent-valuation-median-wtp."
                    ),
                    output=claim.output_evidence[0],
                ),
            ),
            figures=(
                FigureBinding(
                    kind="figure",
                    binding_id="response-curve",
                    claim_ids=(claim.claim_id,),
                    artifact_path="outputs/response-curve.svg",
                    caption=(
                        "Registered figure output for claim "
                        "contingent-valuation-median-wtp."
                    ),
                    output=claim.output_evidence[1],
                ),
            ),
        ),
        ledger,
        argument_map,
        snapshot,
    )


def materialized_draft() -> tuple[
    PaperDraft,
    ClaimEvidenceLedger,
    ArgumentMap,
    CitationAuthoritySnapshot,
    ArtifactRef,
    ArtifactRef,
    ArtifactRef,
]:
    value, ledger, argument_map, snapshot = candidate()
    map_ref = artifact_ref(argument_map.map_id, "d")
    ledger_ref = argument_map.ledger_ref
    report_ref = snapshot.report[0]
    draft = PaperDraft(
        schema_version="paper.draft.v1",
        draft_id="paper-draft-authority",
        producer="paper-builder-draft-v1",
        map_ref=map_ref,
        ledger_ref=ledger_ref,
        citation_report_ref=report_ref,
        paragraphs=value.paragraphs,
        claim_bindings=tuple(
            sorted(
                value.claim_bindings,
                key=lambda item: (item.paragraph_id, item.start, item.end),
            )
        ),
        citation_bindings=value.citation_bindings,
        tables=value.tables,
        figures=value.figures,
    )
    return draft, ledger, argument_map, snapshot, map_ref, ledger_ref, report_ref


def _source_sheet() -> CuratorSourceSheet:
    verified = VerifiedClaim(
        claim_id="claim-001",
        normalized_claim="The policy applies to eligible facilities.",
        source_item_key="2PPVRAL8",
        source_attachment_key="7S8T9UVW",
        source_content_hash="a" * 64,
        locator=SourceLocator(page=12, section="Eligibility", paragraph=2),
        supporting_passage_hash="b" * 64,
        status=ClaimVerificationStatus.CLAIM_VERIFIED,
        extractor_principal="extractor",
        verifier_principal="verifier",
        verified_at=datetime(2026, 8, 9, 8, 0, tzinfo=UTC),
    )
    return CuratorSourceSheet(
        case_id="pilot-001",
        method_family="contingent-valuation",
        zotero_item_key="2PPVRAL8",
        zotero_attachment_key="7S8T9UVW",
        doi="10.1000/example.policy",
        title="A policy evaluation",
        authors=("Author One",),
        source_content_hash="a" * 64,
        source_generation=1,
        institutional_context=("Eligible facilities report annual emissions.",),
        restricted_terms=(),
        distinctive_phrase_hashes=("b" * 64,),
        claims=(verified,),
    )


__all__ = ["artifact_ref", "candidate", "evidence", "materialized_draft"]
