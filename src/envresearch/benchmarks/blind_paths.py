"""Canonical artifact paths for one blind benchmark case."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from envresearch.workers.contracts import require_safe_order_id


@dataclass(frozen=True, slots=True)
class BlindArtifactPaths:
    source_sheet: Path
    blinded_brief: Path
    claim_fact_map: Path
    leakage_report: Path
    recommendation: Path
    expert_one_evidence: Path
    expert_one: Path
    expert_two_evidence: Path
    expert_two: Path
    third_score_evidence: Path
    third_score: Path
    adjudication_evidence: Path
    adjudication: Path
    posthoc_comparison: Path
    citation_report: Path

    @classmethod
    def for_case(cls, case_id: str) -> BlindArtifactPaths:
        require_safe_order_id(case_id)
        root = Path("artifacts/blind-benchmarks") / case_id
        return cls(
            source_sheet=root / "curator-source-sheet.yaml",
            blinded_brief=root / "blinded-brief.yaml",
            claim_fact_map=root / "claim-fact-map.yaml",
            leakage_report=root / "leakage-report.yaml",
            recommendation=root / "method-recommendation.yaml",
            expert_one_evidence=root / "expert-score-1.signed.json",
            expert_one=root / "expert-score-1.yaml",
            expert_two_evidence=root / "expert-score-2.signed.json",
            expert_two=root / "expert-score-2.yaml",
            third_score_evidence=root / "adjudicator-score.signed.json",
            third_score=root / "adjudicator-score.yaml",
            adjudication_evidence=root / "adjudication.signed.json",
            adjudication=root / "adjudication.yaml",
            posthoc_comparison=root / "posthoc-comparison.yaml",
            citation_report=root / "citation-integrity.yaml",
        )

    @property
    def descendants(self) -> tuple[Path, ...]:
        """Return source descendants in stable topological order."""
        return (
            self.blinded_brief,
            self.claim_fact_map,
            self.leakage_report,
            self.recommendation,
            self.expert_one_evidence,
            self.expert_one,
            self.expert_two_evidence,
            self.expert_two,
            self.third_score_evidence,
            self.third_score,
            self.adjudication_evidence,
            self.adjudication,
            self.posthoc_comparison,
            self.citation_report,
        )

    def expert_score(self, slot: int) -> Path:
        if slot == 1:
            return self.expert_one
        if slot == 2:
            return self.expert_two
        raise ValueError("expert slot must be one or two")
