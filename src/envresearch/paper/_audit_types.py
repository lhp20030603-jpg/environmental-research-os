"""Stable public literal types and code mapping for paper audit findings."""

from __future__ import annotations

from typing import Literal

AuditCode = Literal[
    "PAPER_AUTHORITY_INVALID",
    "PAPER_INTEGRITY_INVALID",
    "PAPER_SUPPORT_INVALID",
    "PAPER_SCOPE_EXCEEDED",
]
FindingKind = Literal[
    "citation-mismatch",
    "numeric-contradiction",
    "output-evidence-mismatch",
    "claim-strength-excess",
    "policy-overclaim",
    "basis-overreach",
    "scope-inconsistency",
    "cross-section-contradiction",
]

KIND_CODES: dict[FindingKind, AuditCode] = {
    "citation-mismatch": "PAPER_SUPPORT_INVALID",
    "numeric-contradiction": "PAPER_SUPPORT_INVALID",
    "output-evidence-mismatch": "PAPER_INTEGRITY_INVALID",
    "claim-strength-excess": "PAPER_SCOPE_EXCEEDED",
    "policy-overclaim": "PAPER_SCOPE_EXCEEDED",
    "basis-overreach": "PAPER_SCOPE_EXCEEDED",
    "scope-inconsistency": "PAPER_SUPPORT_INVALID",
    "cross-section-contradiction": "PAPER_SUPPORT_INVALID",
}

__all__ = ["KIND_CODES", "AuditCode", "FindingKind"]
