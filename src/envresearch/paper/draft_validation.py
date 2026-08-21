"""Pure validation for exact claim, citation, and output draft bindings."""

from __future__ import annotations

from collections.abc import Iterable
from itertools import pairwise

from pydantic import ValidationError

from envresearch.benchmarks.claim_report import (
    CitationIntegrityReport,
    report_binding_is_valid,
)
from envresearch.models.artifact import ArtifactRef
from envresearch.models.benchmark_claims import ClaimVerificationStatus
from envresearch.paper._draft_prose import (
    CAUSAL_LANGUAGE as _CAUSAL_LANGUAGE,
)
from envresearch.paper._draft_prose import (
    POLICY_OVERREACH as _POLICY_OVERREACH,
)
from envresearch.paper._draft_prose import (
    render_claim_sentence,
    render_output_caption,
)
from envresearch.paper._draft_prose import (
    require_limitations_coverage as _require_limitations_coverage,
)
from envresearch.paper._draft_prose import (
    require_methods_coverage as _require_methods_coverage,
)
from envresearch.paper._draft_prose import (
    require_numbers_are_bound as _require_numbers_are_bound,
)
from envresearch.paper._draft_prose import (
    require_results_coverage as _require_results_coverage,
)
from envresearch.paper._draft_prose import (
    require_safe_unbound_sections as _require_safe_unbound_sections,
)
from envresearch.paper.argument_contracts import ArgumentMap
from envresearch.paper.citation_authority import CitationAuthoritySnapshot
from envresearch.paper.contracts import (
    ClaimEvidenceLedger,
    ClaimEvidenceRow,
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
from envresearch.paper.errors import PaperScopeExceeded, PaperSupportInvalid

DraftPayload = PaperDraftCandidate | PaperDraft


def validate_draft(
    candidate: DraftPayload,
    *,
    argument_map: ArgumentMap,
    ledger: ClaimEvidenceLedger,
    citation_snapshot: CitationAuthoritySnapshot,
    map_ref: ArtifactRef | None = None,
    ledger_ref: ArtifactRef | None = None,
    citation_report_ref: ArtifactRef | None = None,
) -> None:
    """Fail closed unless every draft statement has exact upstream support."""
    candidate = _strict_candidate(candidate)
    _require_exact_refs(
        candidate,
        argument_map=argument_map,
        citation_snapshot=citation_snapshot,
        map_ref=map_ref,
        ledger_ref=ledger_ref,
        citation_report_ref=citation_report_ref,
    )
    paragraphs = {item.paragraph_id: item for item in candidate.paragraphs}
    claims = {item.claim_id: item for item in ledger.claims}
    argument_claims = frozenset(
        claim_id
        for node in argument_map.nodes
        if node.node_type == "empirical-claim"
        for claim_id in node.claim_ids
    )
    if argument_map.transition_ref != ledger.transition_ref or not argument_claims:
        raise PaperSupportInvalid(
            "draft argument and ledger authority do not agree",
            finding_kind="draft-upstream-mismatch",
        )

    _reject_strength_overreach(candidate.paragraphs)
    _validate_claim_bindings(
        candidate.claim_bindings,
        paragraphs=paragraphs,
        claims=claims,
        argument_claims=argument_claims,
    )
    _validate_citations(
        candidate.citation_bindings,
        paragraphs=paragraphs,
        snapshot=citation_snapshot,
    )
    _validate_outputs(
        (*candidate.tables, *candidate.figures),
        claims=claims,
        argument_claims=argument_claims,
    )
    _require_results_coverage(candidate.paragraphs, candidate.claim_bindings)
    _require_limitations_coverage(candidate.paragraphs, candidate.claim_bindings)
    _require_methods_coverage(candidate.paragraphs, candidate.citation_bindings)
    _require_safe_unbound_sections(candidate.paragraphs)
    _require_numbers_are_bound(candidate.paragraphs, candidate.claim_bindings)


def _require_exact_refs(
    candidate: DraftPayload,
    *,
    argument_map: ArgumentMap,
    citation_snapshot: CitationAuthoritySnapshot,
    map_ref: ArtifactRef | None,
    ledger_ref: ArtifactRef | None,
    citation_report_ref: ArtifactRef | None,
) -> None:
    if not isinstance(candidate, PaperDraft):
        return
    if (
        map_ref is None
        or ledger_ref is None
        or citation_report_ref is None
        or candidate.map_ref != map_ref
        or candidate.ledger_ref != ledger_ref
        or candidate.citation_report_ref != citation_report_ref
        or argument_map.ledger_ref != ledger_ref
        or citation_snapshot.report[0] != citation_report_ref
    ):
        raise PaperSupportInvalid(
            "draft exact authority reference does not match reopened upstream evidence",
            finding_kind="draft-reference-mismatch",
        )


def _strict_candidate(candidate: DraftPayload) -> DraftPayload:
    try:
        return type(candidate).model_validate(candidate.model_dump(mode="python"))
    except ValidationError as exc:
        raise PaperSupportInvalid(
            "draft spans or bindings overlap, duplicate, or violate the strict contract",
            finding_kind="draft-candidate-invalid",
        ) from exc


def _validate_claim_bindings(
    bindings: tuple[ClaimSpanBinding, ...],
    *,
    paragraphs: dict[str, PaperParagraph],
    claims: dict[str, ClaimEvidenceRow],
    argument_claims: frozenset[str],
) -> None:
    _require_nonoverlap(bindings)
    for binding in bindings:
        paragraph = paragraphs.get(binding.paragraph_id)
        if paragraph is None or binding.end > len(paragraph.text):
            raise PaperSupportInvalid(
                "claim span offsets do not select current paragraph text",
                finding_kind="claim-span-invalid",
            )
        rows = tuple(claims.get(item) for item in binding.claim_ids)
        if any(row is None for row in rows) or any(
            item not in argument_claims for item in binding.claim_ids
        ):
            raise PaperSupportInvalid(
                "claim span references unsupported ledger evidence",
                finding_kind="claim-span-unsupported",
            )
        exact_rows = tuple(row for row in rows if row is not None)
        _require_exact_scope(binding, exact_rows)
        selected = paragraph.text[binding.start : binding.end]
        if binding.purpose == "finding":
            if paragraph.section != "results" or selected != " ".join(
                render_claim_sentence(row) for row in exact_rows
            ):
                raise PaperScopeExceeded(
                    "claim span does not match the exact registered number and basis template",
                    finding_kind="claim-span-scope-exceeded",
                )
        elif binding.purpose == "limitation":
            allowed = frozenset(
                limitation for row in exact_rows for limitation in row.limitations
            )
            if paragraph.section != "limitations" or selected not in allowed:
                raise PaperScopeExceeded(
                    "limitation span exceeds the registered claim boundary",
                    finding_kind="limitation-scope-exceeded",
                )
        elif paragraph.section != "validation-scope":
            raise PaperSupportInvalid(
                "validation scope cannot be promoted as a finding",
                finding_kind="validation-scope-promoted",
            )


def _require_exact_scope(
    binding: ClaimSpanBinding, rows: tuple[ClaimEvidenceRow, ...]
) -> None:
    for row in rows:
        if (
            binding.allowed_strength != row.allowed_strength
            or binding.unit != row.unit
            or binding.population_basis != row.population_basis
            or binding.time_basis != row.time_basis
            or binding.price_base != row.price_base
        ):
            raise PaperScopeExceeded(
                "claim strength, unit, population, time, or price basis changed",
                finding_kind="claim-basis-changed",
            )


def _validate_citations(
    bindings: tuple[CitationBinding, ...],
    *,
    paragraphs: dict[str, PaperParagraph],
    snapshot: CitationAuthoritySnapshot,
) -> None:
    report_ref, report = snapshot.report
    source_refs = tuple(item[0] for item in snapshot.source_sheets)
    if (
        type(report) is not CitationIntegrityReport
        or report_ref.artifact_id != "citation-integrity-report"
        or snapshot.token.report_ref != report_ref
        or not report.passed
        or report.findings
        or report.validator_version != "claim-integrity-v1"
        or not report_binding_is_valid(report)
        or source_refs != report.source_sheet_refs
        or source_refs != tuple(sorted(source_refs, key=str))
    ):
        raise PaperSupportInvalid(
            "citation report or source binding is not a passing exact authority",
            finding_kind="citation-report-invalid",
        )
    if not source_refs or len(source_refs) != len(set(source_refs)):
        raise PaperSupportInvalid(
            "citation source authority is empty or ambiguous",
            finding_kind="citation-source-invalid",
        )
    _require_nonoverlap(bindings)
    for binding in bindings:
        paragraph = paragraphs.get(binding.paragraph_id)
        source = next(
            (
                sheet
                for ref, sheet in snapshot.source_sheets
                if ref == binding.source_sheet_ref
            ),
            None,
        )
        claim = (
            next(
                (item for item in source.claims if item.claim_id == binding.claim_id),
                None,
            )
            if source is not None
            else None
        )
        if paragraph is None or binding.end > len(paragraph.text) or claim is None:
            raise PaperSupportInvalid(
                "citation binding is invented or has invalid offsets",
                finding_kind="citation-binding-invalid",
            )
        if claim.status is not ClaimVerificationStatus.CLAIM_VERIFIED:
            raise PaperSupportInvalid(
                "citation claim is not independently verified",
                finding_kind="citation-claim-unverified",
            )
        if paragraph.text[binding.start : binding.end] != claim.normalized_claim:
            raise PaperSupportInvalid(
                "citation span does not equal the accepted verified claim",
                finding_kind="citation-span-mismatch",
            )


def _validate_outputs(
    bindings: tuple[TableBinding | FigureBinding, ...],
    *,
    claims: dict[str, ClaimEvidenceRow],
    argument_claims: frozenset[str],
) -> None:
    for binding in bindings:
        rows = tuple(claims.get(item) for item in binding.claim_ids)
        if any(row is None for row in rows) or any(
            item not in argument_claims for item in binding.claim_ids
        ):
            raise PaperSupportInvalid(
                "output binding references an unsupported claim",
                finding_kind="output-claim-unsupported",
            )
        expected_path = f"outputs/{binding.output.name}"
        extension_ok = (
            binding.output.name.endswith(".svg")
            if isinstance(binding, FigureBinding)
            else not binding.output.name.endswith(".svg")
        )
        if (
            any(
                row is None or binding.output not in row.output_evidence for row in rows
            )
            or binding.artifact_path != expected_path
            or not extension_ok
            or binding.caption
            != render_output_caption(
                binding.kind, tuple(row for row in rows if row is not None)
            )
        ):
            raise PaperSupportInvalid(
                "table or figure output and caption must match every bound claim",
                finding_kind="output-evidence-mismatch",
            )


def _require_nonoverlap(
    bindings: Iterable[ClaimSpanBinding | CitationBinding],
) -> None:
    by_paragraph: dict[str, list[tuple[int, int]]] = {}
    for item in bindings:
        by_paragraph.setdefault(item.paragraph_id, []).append((item.start, item.end))
    for spans in by_paragraph.values():
        ordered = sorted(spans)
        if any(left[1] > right[0] for left, right in pairwise(ordered)):
            raise PaperSupportInvalid(
                "draft spans overlap",
                finding_kind="draft-span-overlap",
            )


def _reject_strength_overreach(
    paragraphs: tuple[PaperParagraph, ...],
) -> None:
    text = " ".join(item.text for item in paragraphs)
    if _POLICY_OVERREACH.search(text):
        raise PaperScopeExceeded(
            "policy language exceeds registered evidence strength",
            finding_kind="policy-overclaim",
        )
    if _CAUSAL_LANGUAGE.search(text):
        raise PaperScopeExceeded(
            "causal language requires a controlled design-based renderer",
            finding_kind="causal-overclaim",
        )


__all__ = ["render_claim_sentence", "render_output_caption", "validate_draft"]
