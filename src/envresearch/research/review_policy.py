"""Deterministic closure policies for independently reviewed research designs."""

from __future__ import annotations

from envresearch.models.design import (
    DesignFinding,
    ResearchQualityScores,
    ReviewSeverity,
)


class ReviewPolicy:
    """Apply review severity and explicit human risk-acceptance rules."""

    @staticmethod
    def can_compose(findings: tuple[DesignFinding, ...]) -> bool:
        """Return whether an open blocking finding permits plan composition."""
        validated_findings = ReviewPolicy._validate_findings(findings)
        return not any(
            item.severity is ReviewSeverity.BLOCKING and not item.resolved
            for item in validated_findings
        )

    @staticmethod
    def final_gate_eligible(
        findings: tuple[DesignFinding, ...], accepted_major_ids: frozenset[str]
    ) -> bool:
        """Require closure or explicit residual-risk acceptance for open majors."""
        validated_findings = ReviewPolicy._validate_findings(findings)
        findings_by_id = {item.finding_id: item for item in validated_findings}
        ReviewPolicy._validate_accepted_major_ids(findings_by_id, accepted_major_ids)
        return ReviewPolicy.can_compose(validated_findings) and all(
            item.resolved
            or item.severity is not ReviewSeverity.MAJOR
            or (
                item.finding_id in accepted_major_ids
                and item.residual_risk is not None
                and bool(item.residual_risk.strip())
            )
            for item in validated_findings
        )

    @staticmethod
    def _validate_findings(
        findings: tuple[DesignFinding, ...],
    ) -> tuple[DesignFinding, ...]:
        """Revalidate findings before applying policy to copied model instances."""
        validated_findings = tuple(
            DesignFinding.model_validate(item.__dict__) for item in findings
        )
        finding_ids = tuple(item.finding_id for item in validated_findings)
        if len(set(finding_ids)) != len(validated_findings):
            raise ValueError("duplicate finding_id in review findings")
        return validated_findings

    @staticmethod
    def _validate_accepted_major_ids(
        findings_by_id: dict[str, DesignFinding], accepted_major_ids: frozenset[str]
    ) -> None:
        """Ensure human acceptance can only target an open major finding."""
        if any(not finding_id.strip() for finding_id in accepted_major_ids):
            raise ValueError("accepted_major_ids must not contain blank values")
        unknown_ids = accepted_major_ids - findings_by_id.keys()
        if unknown_ids:
            raise ValueError("accepted_major_ids contain unknown finding IDs")
        invalid_ids = tuple(
            finding_id
            for finding_id in accepted_major_ids
            if findings_by_id[finding_id].resolved
            or findings_by_id[finding_id].severity is not ReviewSeverity.MAJOR
        )
        if invalid_ids:
            raise ValueError("accepted_major_ids may contain only unresolved major IDs")


class ResearchQualityPolicy:
    """Pass only complete quality scores and final-gate-eligible review closure."""

    @staticmethod
    def passes(
        scores: ResearchQualityScores,
        findings: tuple[DesignFinding, ...],
        *,
        accepted_major_ids: frozenset[str] = frozenset(),
    ) -> bool:
        """Require every rubric dimension and review finding to clear its threshold."""
        validated_scores = ResearchQualityScores.model_validate(scores.__dict__)
        return (
            min(validated_scores.model_dump().values()) >= 3
            and ReviewPolicy.final_gate_eligible(findings, accepted_major_ids)
        )
