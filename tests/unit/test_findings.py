from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from envresearch.models.enums import FindingSeverity
from envresearch.models.finding import Finding


def test_critical_finding_cannot_be_resolved_by_producer() -> None:
    """The producer self-approving a critical finding must be rejected."""
    finding = Finding(
        id="finding-001",
        code="INTEGRITY_SELF_APPROVAL",
        severity=FindingSeverity.CRITICAL,
        message="Producer cannot close its own critical finding.",
        producer="agent-a",
        evidence=["events.jsonl#12"],
    )

    with pytest.raises(ValueError, match="independent resolver"):
        finding.resolve("agent-a")


def test_resolving_finding_returns_new_utc_resolved_artifact() -> None:
    """Resolution must retain the original immutable finding and record its resolver."""
    finding = Finding(
        id="finding-002",
        code="MISSING_INPUT",
        severity=FindingSeverity.WARNING,
        message="An input artifact is missing.",
        producer="agent-a",
        evidence=["inputs/required.csv"],
    )

    resolved = finding.resolve("reviewer-b")

    assert finding.resolved_by is None
    assert finding.resolved_at is None
    assert resolved.resolved_by == "reviewer-b"
    assert resolved.resolved_at is not None
    assert resolved.resolved_at.tzinfo is UTC


@pytest.mark.parametrize(
    "resolved_at",
    [
        datetime(2026, 8, 4, 9, 0),  # noqa: DTZ001
        datetime(2026, 8, 4, 9, 0, tzinfo=timezone(timedelta(hours=8))),
    ],
)
def test_finding_rejects_non_utc_resolution_time(resolved_at: datetime) -> None:
    """Naive or offset timestamps would make audit evidence ambiguous."""
    with pytest.raises(ValidationError, match="UTC"):
        Finding(
            id="finding-003",
            code="NAIVE_TIME",
            severity=FindingSeverity.ERROR,
            message="Timestamp must be timezone-aware.",
            producer="agent-a",
            evidence=[],
            resolved_at=resolved_at,
        )


def test_finding_evidence_is_immutable_after_list_input() -> None:
    """Evidence mutation must not rewrite an immutable finding artifact."""
    finding = Finding(
        id="finding-004",
        code="IMMUTABLE_EVIDENCE",
        severity=FindingSeverity.INFO,
        message="Evidence cannot be changed in place.",
        producer="agent-a",
        evidence=["events.jsonl#13"],
    )

    with pytest.raises(AttributeError):
        finding.evidence.append("events.jsonl#14")
