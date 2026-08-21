"""Transactional publication for the V0.4 claim-evidence ledger."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Protocol

from envresearch.econometrics.exit_registry import ExitRegistry, validate_separate_roots
from envresearch.econometrics.report import LocalAnalysisReference, LocalAnalysisReport
from envresearch.econometrics.service import EvidenceTampered
from envresearch.econometrics.valuation_authority import valuation_authority_lease
from envresearch.econometrics.valuation_transition import (
    V031ExitHarness,
    accepted_analysis_reports,
)
from envresearch.models.artifact import ArtifactRef
from envresearch.paper.contracts import ClaimEvidenceLedger
from envresearch.paper.errors import (
    PaperAuthorityInvalid,
    PaperBuilderError,
    PaperIntegrityInvalid,
)
from envresearch.paper.valuation_claims import valuation_claims

CLAIM_LEDGER_SUBJECT = "paper-claim-ledger"


class AcceptedEvidenceResolver(Protocol):
    """Exact accepted-report boundary injected into claim-ledger publication."""

    def resolve(
        self, transition_ref: ArtifactRef
    ) -> tuple[tuple[LocalAnalysisReference, LocalAnalysisReport], ...]: ...

    def require_current(self, transition_ref: ArtifactRef) -> None: ...

    def authority_lease(self) -> AbstractContextManager[None]: ...


class V031AcceptedEvidenceResolver:
    """Production adapter for one caller-supplied exact V0.3.1 transition."""

    def __init__(self, run_root: Path) -> None:
        self.run_root = run_root.resolve(strict=True)

    def resolve(
        self, transition_ref: ArtifactRef
    ) -> tuple[tuple[LocalAnalysisReference, LocalAnalysisReport], ...]:
        with self.authority_lease():
            harness = V031ExitHarness.open_exact(self.run_root, transition_ref)
            return accepted_analysis_reports(harness)

    def require_current(self, transition_ref: ArtifactRef) -> None:
        with self.authority_lease():
            V031ExitHarness.open_exact(self.run_root, transition_ref)

    @contextmanager
    def authority_lease(self) -> Iterator[None]:
        """Exclude every V0.3.1 writer across a paper transaction."""
        runner = ExitRegistry(self.run_root / "runner", create=False)
        with valuation_authority_lease(runner):
            yield


class ClaimLedgerService:
    """Build and reopen one exact current claim-evidence ledger."""

    def __init__(
        self, *, registry: ExitRegistry, resolver: AcceptedEvidenceResolver
    ) -> None:
        self.registry = registry
        self.resolver = resolver

    @classmethod
    def for_resolver(
        cls, *, paper_root: Path, resolver: AcceptedEvidenceResolver
    ) -> ClaimLedgerService:
        """Compose an always-authenticated injected accepted-evidence boundary."""
        try:
            return cls(registry=ExitRegistry(paper_root), resolver=resolver)
        except (OSError, ValueError) as exc:
            raise PaperAuthorityInvalid(
                "paper evidence root is invalid", finding_kind="paper-root-invalid"
            ) from exc

    @classmethod
    def from_v031(cls, *, run_root: Path, paper_root: Path) -> ClaimLedgerService:
        """Compose physically separate V0.3.1 input and V0.4 output roots."""
        try:
            validated_run, validated_paper = validate_separate_roots(
                run_root, paper_root
            )
            return cls(
                registry=ExitRegistry(validated_paper),
                resolver=V031AcceptedEvidenceResolver(validated_run),
            )
        except (OSError, ValueError) as exc:
            raise PaperAuthorityInvalid(
                "V0.3.1 and Paper Builder roots must be physically separate",
                finding_kind="root-authority-overlap",
            ) from exc

    def build(self, transition_ref: ArtifactRef) -> ArtifactRef:
        """Derive, publish, and promote one ledger only while authority is current."""
        with self.resolver.authority_lease():
            return self._build(transition_ref)

    def _build(self, transition_ref: ArtifactRef) -> ArtifactRef:
        """Build while the caller owns the accepted-evidence authority lease."""
        self._require_current(transition_ref)
        candidate = self._derive(transition_ref)
        with self.registry.lock(CLAIM_LEDGER_SUBJECT):
            prior = self._paper_current()
            if prior is not None:
                existing = self._load(prior)
                if existing != candidate:
                    raise PaperAuthorityInvalid(
                        "a different claim ledger is already current",
                        finding_kind="ledger-current-conflict",
                    )
                self._require_current(transition_ref)
                if self._derive(transition_ref) != existing:
                    raise PaperIntegrityInvalid(
                        "claim evidence changed during idempotent recovery",
                        finding_kind="ledger-reconstruction-mismatch",
                    )
                return prior
            self._require_current(transition_ref)
            try:
                reference = self.registry.publish(candidate.ledger_id, candidate)
            except (OSError, ValueError) as exc:
                raise PaperIntegrityInvalid(
                    "claim ledger immutable publication failed",
                    finding_kind="ledger-publication-failed",
                ) from exc
            self._require_current(transition_ref)
            try:
                self.registry.set_current(CLAIM_LEDGER_SUBJECT, reference)
            except (OSError, ValueError) as exc:
                self._restore_current(prior)
                raise PaperIntegrityInvalid(
                    "claim ledger current publication failed",
                    finding_kind="ledger-publication-failed",
                ) from exc
            try:
                self._require_current(transition_ref)
                if self._derive(transition_ref) != candidate:
                    raise PaperIntegrityInvalid(
                        "claim evidence changed during publication",
                        finding_kind="ledger-reconstruction-mismatch",
                    )
                self._require_current(transition_ref)
            except PaperBuilderError:
                self._restore_current(prior)
                raise
            return reference

    def status(
        self, ledger_ref: ArtifactRef, transition_ref: ArtifactRef
    ) -> ClaimEvidenceLedger:
        """Reopen exact ledger and independently rederive every accepted row."""
        with self.resolver.authority_lease():
            return self._status(ledger_ref, transition_ref)

    def _status(
        self, ledger_ref: ArtifactRef, transition_ref: ArtifactRef
    ) -> ClaimEvidenceLedger:
        """Reopen while the caller owns the accepted-evidence authority lease."""
        self._require_current(transition_ref)
        if self._paper_current() != ledger_ref:
            raise PaperAuthorityInvalid(
                "claim ledger reference is not current",
                finding_kind="ledger-not-current",
            )
        ledger = self._load(ledger_ref)
        if ledger.transition_ref != transition_ref:
            raise PaperAuthorityInvalid(
                "claim ledger binds another transition",
                finding_kind="transition-reference-mismatch",
            )
        if self._derive(transition_ref) != ledger:
            raise PaperIntegrityInvalid(
                "claim ledger does not match reconstructed evidence",
                finding_kind="ledger-reconstruction-mismatch",
            )
        if self._paper_current() != ledger_ref:
            raise PaperAuthorityInvalid(
                "claim ledger changed during status",
                finding_kind="ledger-not-current",
            )
        self._require_current(transition_ref)
        if self._paper_current() != ledger_ref:
            raise PaperAuthorityInvalid(
                "claim ledger changed during status",
                finding_kind="ledger-not-current",
            )
        reopened = self._load(ledger_ref)
        if reopened != ledger:
            raise PaperIntegrityInvalid(
                "claim ledger changed during status",
                finding_kind="ledger-reconstruction-mismatch",
            )
        return reopened

    def _derive(self, transition_ref: ArtifactRef) -> ClaimEvidenceLedger:
        try:
            claims = valuation_claims(
                self.resolver.resolve(transition_ref), transition_ref
            )
            return ClaimEvidenceLedger(
                schema_version="paper.claim-evidence-ledger.v1",
                ledger_id=f"valuation-core-{transition_ref.content_hash[:12]}",
                producer="paper-builder-ledger-v1",
                transition_ref=transition_ref,
                claims=claims,
            )
        except PaperBuilderError:
            raise
        except EvidenceTampered as exc:
            raise PaperIntegrityInvalid(
                "accepted analysis evidence failed reconstruction",
                finding_kind="analysis-evidence-tampered",
            ) from exc
        except (OSError, ValueError) as exc:
            raise PaperAuthorityInvalid(
                "accepted evidence authority is invalid",
                finding_kind="accepted-evidence-invalid",
            ) from exc

    def _require_current(self, transition_ref: ArtifactRef) -> None:
        try:
            self.resolver.require_current(transition_ref)
        except PaperBuilderError:
            raise
        except (OSError, ValueError) as exc:
            raise PaperAuthorityInvalid(
                "accepted transition is not current",
                finding_kind="transition-not-current",
            ) from exc

    def _paper_current(self) -> ArtifactRef | None:
        try:
            return self.registry.current(CLAIM_LEDGER_SUBJECT)
        except (OSError, ValueError) as exc:
            raise PaperIntegrityInvalid(
                "claim ledger current pointer is invalid",
                finding_kind="ledger-current-invalid",
            ) from exc

    def _load(self, reference: ArtifactRef) -> ClaimEvidenceLedger:
        relative = (
            Path("exit/objects")
            / reference.artifact_id
            / f"v{reference.artifact_version}-{reference.content_hash}.json"
        )
        try:
            data = self.registry.files.read(relative)
            if hashlib.sha256(data).hexdigest() != reference.content_hash:
                raise ValueError("claim ledger content hash mismatch")
            ledger = ClaimEvidenceLedger.model_validate_json(data)
        except (OSError, ValueError) as exc:
            raise PaperIntegrityInvalid(
                "claim ledger bytes are invalid",
                finding_kind="ledger-bytes-invalid",
            ) from exc
        if reference.artifact_id != ledger.ledger_id:
            raise PaperIntegrityInvalid(
                "claim ledger reference identity is invalid",
                finding_kind="ledger-identity-invalid",
            )
        if data != ledger.model_dump_json().encode():
            raise PaperIntegrityInvalid(
                "claim ledger bytes are not canonical",
                finding_kind="ledger-bytes-noncanonical",
            )
        return ledger

    def _restore_current(self, reference: ArtifactRef | None) -> None:
        if reference is None:
            self.registry.files.unlink(
                Path("exit/current") / f"{CLAIM_LEDGER_SUBJECT}.json"
            )
        else:
            self.registry.set_current(CLAIM_LEDGER_SUBJECT, reference)


__all__ = [
    "CLAIM_LEDGER_SUBJECT",
    "AcceptedEvidenceResolver",
    "ClaimLedgerService",
    "V031AcceptedEvidenceResolver",
]
