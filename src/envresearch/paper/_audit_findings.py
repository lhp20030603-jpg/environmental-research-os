"""Deterministic exact-target finding collection for independent paper audit."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Literal

from envresearch.models.artifact import ArtifactRef
from envresearch.paper.audit_contracts import (
    AuditTarget,
    DraftBindingTarget,
    FindingKind,
    OutputBindingTarget,
    PaperAuditFinding,
    TextSpan,
    analysis_ref_key,
    output_ref_key,
)
from envresearch.paper.contracts import ClaimEvidenceRow
from envresearch.paper.draft_contracts import (
    CitationBinding,
    ClaimSpanBinding,
    FigureBinding,
    PaperDraft,
    PaperParagraph,
    TableBinding,
)

_KIND_CODE: dict[str, str] = {
    "citation-mismatch": "PAPER_SUPPORT_INVALID",
    "numeric-contradiction": "PAPER_SUPPORT_INVALID",
    "output-evidence-mismatch": "PAPER_INTEGRITY_INVALID",
    "claim-strength-excess": "PAPER_SCOPE_EXCEEDED",
    "policy-overclaim": "PAPER_SCOPE_EXCEEDED",
    "basis-overreach": "PAPER_SCOPE_EXCEEDED",
    "scope-inconsistency": "PAPER_SUPPORT_INVALID",
    "cross-section-contradiction": "PAPER_SUPPORT_INVALID",
}


class AuditCollector:
    """Accumulate every distinct finding without fail-fast oracle sharing."""

    def __init__(
        self,
        *,
        draft_ref: ArtifactRef,
        draft: PaperDraft,
        upstream_refs: tuple[ArtifactRef, ...],
        default_claim_ids: tuple[str, ...],
        claims: dict[str, ClaimEvidenceRow],
    ) -> None:
        self.draft_ref = draft_ref
        self.draft = draft
        self.upstream_refs = upstream_refs
        self.default_claim_ids = default_claim_ids
        self.claims = claims
        self.findings: dict[str, PaperAuditFinding] = {}

    def text(
        self,
        kind: FindingKind,
        paragraph: PaperParagraph,
        *,
        start: int = 0,
        end: int | None = None,
        claim_ids: Iterable[str] = (),
    ) -> None:
        final_start = max(0, min(start, len(paragraph.text) - 1))
        final_end = max(
            final_start + 1, min(end or len(paragraph.text), len(paragraph.text))
        )
        selected = paragraph.text[final_start:final_end]
        self._add(
            kind,
            TextSpan(
                target_type="text-span",
                paragraph_id=paragraph.paragraph_id,
                start=final_start,
                end=final_end,
                text_sha256=hashlib.sha256(selected.encode()).hexdigest(),
            ),
            claim_ids,
        )

    def output(
        self,
        kind: FindingKind,
        binding: TableBinding | FigureBinding,
    ) -> None:
        self._add(
            kind,
            OutputBindingTarget(
                target_type="output-binding",
                kind=binding.kind,
                binding_id=binding.binding_id,
            ),
            binding.claim_ids,
        )

    def binding(
        self,
        kind: FindingKind,
        binding: ClaimSpanBinding | CitationBinding,
    ) -> None:
        target_type: Literal["claim-binding", "citation-binding"] = (
            "claim-binding"
            if isinstance(binding, ClaimSpanBinding)
            else "citation-binding"
        )
        self._add(
            kind,
            DraftBindingTarget(
                target_type=target_type,
                paragraph_id=binding.paragraph_id,
                start=binding.start,
                end=binding.end,
                binding_sha256=hashlib.sha256(
                    binding.model_dump_json().encode()
                ).hexdigest(),
            ),
            binding.claim_ids
            if isinstance(binding, ClaimSpanBinding)
            else (binding.claim_id,),
        )

    def _add(
        self,
        kind: FindingKind,
        target: AuditTarget,
        claim_ids: Iterable[str],
    ) -> None:
        claims = tuple(sorted(set(claim_ids))) or self.default_claim_ids
        rows = tuple(self.claims[item] for item in claims if item in self.claims)
        analysis_refs = tuple(
            sorted({item.analysis_ref for item in rows}, key=analysis_ref_key)
        )
        output_refs = tuple(
            sorted(
                {output for row in rows for output in row.output_evidence},
                key=output_ref_key,
            )
        )
        identity = {
            "kind": kind,
            "target": target.model_dump(mode="json"),
            "claim_ids": claims,
            "draft_ref": self.draft_ref.model_dump(mode="json"),
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        finding = PaperAuditFinding(
            finding_id=f"{kind}-{digest}",
            finding_kind=kind,
            code=_KIND_CODE[kind],  # type: ignore[arg-type]
            draft_ref=self.draft_ref,
            target=target,
            claim_ids=claims,
            upstream_refs=self.upstream_refs,
            analysis_refs=analysis_refs,
            output_refs=output_refs,
        )
        self.findings[finding.finding_id] = finding


__all__ = ["AuditCollector"]
