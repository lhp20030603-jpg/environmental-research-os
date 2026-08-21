"""Governed assembly and read-only status for complete research-factory runs."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError

from envresearch.econometrics.exit_registry import ExitRegistry
from envresearch.factory._event_log import append_event_atomically
from envresearch.factory._store import _FactoryRunStore
from envresearch.factory.authority import FactoryAuthority
from envresearch.factory.coherence import reconstruct_binding_report
from envresearch.factory.contracts import (
    CapabilityProfileBinding,
    FactoryRunStatus,
    ResearchFactoryRun,
    _canonical_artifact_refs,
    factory_run_id,
)
from envresearch.factory.design_resolver import V02ApprovedDesignResolver
from envresearch.factory.errors import (
    FactoryAuthorityInvalid,
    FactoryError,
    FactoryIntegrityInvalid,
    FactorySupportInvalid,
)
from envresearch.kernel.events import EventLog, EventRecord
from envresearch.kernel.gates import GateDecision
from envresearch.models.artifact import ArtifactRef
from envresearch.models.enums import WorkflowStatus
from envresearch.paper.errors import (
    PaperAuthorityInvalid,
    PaperBuilderError,
    PaperIntegrityInvalid,
    PaperSupportInvalid,
)
from envresearch.paper.release import PaperReleaseService
from envresearch.paper.release_contracts import PaperReleaseCandidate
from envresearch.paper.release_revisions import RevisionPair


class FactoryRunService:
    """Assemble and independently reopen one exact retrospective research run."""

    def __init__(
        self,
        *,
        design_resolver: V02ApprovedDesignResolver,
        release_service: PaperReleaseService,
    ) -> None:
        self.design_resolver = design_resolver
        self.release_service = release_service
        self.authority = FactoryAuthority(
            design_resolver=design_resolver, release_service=release_service
        )
        self.registry = ExitRegistry(design_resolver.factory_root, create=False)
        self.store = _FactoryRunStore(self.registry)
        from envresearch.factory.promotion import FactoryPromotionService

        self._promotions = FactoryPromotionService(self)

    def assemble(
        self, design_ref: ArtifactRef, release_ref: ArtifactRef
    ) -> ArtifactRef:
        """Publish one deterministic run only after repeated typed reconstruction."""
        try:
            self.store.probe_recovery_intent(design_ref, release_ref)
            design_preview = self.design_resolver.resolve(design_ref)
            release_preview = self.release_service.store.load(release_ref)
            deferred: FactoryError | None = None
            result: ArtifactRef | None = None
            with self.authority.lease(
                design_id=design_preview.design_id,
                release_ref=release_ref,
                bootstrap=True,
            ) as (locked_release, chain):
                try:
                    result = self._assemble_locked(
                        design_ref,
                        release_ref,
                        design_preview.design_id,
                        release_preview,
                        locked_release,
                        chain,
                    )
                except FactoryError as exc:
                    deferred = exc
            if deferred is not None:
                raise deferred
            if result is None:
                raise FactoryIntegrityInvalid(
                    "factory run assembly produced no reference",
                    finding_kind="run-assembly-invalid",
                )
            return result
        except FactoryError:
            raise
        except PaperBuilderError as exc:
            raise _factory_error(exc) from exc
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            raise FactoryIntegrityInvalid(
                "factory run assembly failed", finding_kind="run-assembly-invalid"
            ) from exc

    def status(self, run_ref: ArtifactRef) -> FactoryRunStatus:
        """Return derived promotion state after two reconstructions and no recovery."""
        try:
            initial = self.store.load(run_ref)
            deferred: FactoryError | None = None
            result: FactoryRunStatus | None = None
            with self.authority.lease(
                design_id=initial.design.design_id,
                release_ref=initial.release_ref,
            ) as (locked_release, chain):
                try:
                    result = self._status_locked(
                        run_ref, initial, locked_release, chain
                    )
                except FactoryError as exc:
                    deferred = exc
            if deferred is not None:
                raise deferred
            if result is None:
                raise FactoryIntegrityInvalid(
                    "factory run status produced no snapshot",
                    finding_kind="run-status-invalid",
                )
            return result
        except FactoryError:
            raise
        except PaperBuilderError as exc:
            raise _factory_error(exc) from exc
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            raise FactoryIntegrityInvalid(
                "factory run status failed", finding_kind="run-status-invalid"
            ) from exc

    def request_promotion(self, run_ref: ArtifactRef, requested_by: str) -> ArtifactRef:
        """Delegate one exact run-level promotion request."""
        requester = requested_by.strip().casefold()
        if not requester or requester != requested_by:
            raise FactoryAuthorityInvalid(
                "promotion requester must be one canonical principal",
                finding_kind="promotion-requester-invalid",
            )
        try:
            return self._promotions.request(run_ref, requester)
        except FactoryError:
            raise
        except (TypeError, ValueError, ValidationError) as exc:
            raise FactoryIntegrityInvalid(
                "promotion request is invalid",
                finding_kind="promotion-request-invalid",
            ) from exc

    def record_promotion(
        self,
        context_ref: ArtifactRef,
        decision: GateDecision,
        principal_capability: str,
    ) -> ArtifactRef:
        """Delegate one protected independent terminal decision."""
        try:
            return self._promotions.record(context_ref, decision, principal_capability)
        except FactoryError:
            raise
        except (TypeError, ValueError, ValidationError) as exc:
            raise FactoryIntegrityInvalid(
                "promotion decision conditions must only narrow scope",
                finding_kind="promotion-conditions-invalid",
            ) from exc

    def promotion_status(
        self, promotion_ref: ArtifactRef, run_ref: ArtifactRef
    ) -> FactoryRunStatus:
        """Delegate exact read-only promotion reconstruction."""
        return self._promotions.status(promotion_ref, run_ref)

    def _assemble_locked(
        self,
        design_ref: ArtifactRef,
        release_ref: ArtifactRef,
        design_id: str,
        release_preview: PaperReleaseCandidate,
        locked_release: PaperReleaseCandidate,
        chain: tuple[RevisionPair, ...],
    ) -> ArtifactRef:
        if locked_release != release_preview:
            raise FactoryIntegrityInvalid(
                "paper release changed before factory authority",
                finding_kind="release-changed",
            )
        candidate = self._reconstruct(design_ref, release_ref, locked_release, chain)
        if candidate.design.design_id != design_id:
            raise FactoryIntegrityInvalid(
                "approved design changed before factory authority",
                finding_kind="design-changed",
            )
        prior_prepared = self.store.prepared()
        prior_committed = self.store.committed()
        if prior_committed is not None and prior_prepared != prior_committed:
            raise FactoryIntegrityInvalid(
                "factory run pointers begin in an impossible committed state",
                finding_kind="run-current-invalid",
            )
        reference = self.store.prepare(candidate)
        prepared_installed = prior_prepared != reference
        try:
            repeated = self._reconstruct(design_ref, release_ref, locked_release, chain)
            if repeated != candidate or self.store.load(reference) != candidate:
                raise FactoryIntegrityInvalid(
                    "factory run inputs changed during immutable publication",
                    finding_kind="run-reconstruction-mismatch",
                )
        except (
            FactoryError,
            PaperBuilderError,
            OSError,
            TypeError,
            ValueError,
            ValidationError,
        ):
            if prepared_installed:
                self.store.compare_and_restore(
                    self.store.prepared_subject,
                    installed=reference,
                    previous=prior_prepared,
                )
            raise
        self.store.commit(reference)
        committed_installed = prior_committed != reference
        try:
            final = self._reconstruct(design_ref, release_ref, locked_release, chain)
            if (
                final != candidate
                or self.store.current() != reference
                or self.store.load(reference) != candidate
            ):
                raise FactoryIntegrityInvalid(
                    "factory run changed during final linearization",
                    finding_kind="run-current-invalid",
                )
            self.design_resolver.require_current(design_ref)
        except (
            FactoryError,
            PaperBuilderError,
            OSError,
            TypeError,
            ValueError,
            ValidationError,
        ):
            if committed_installed:
                self.store.compare_and_restore(
                    self.store.committed_subject,
                    installed=reference,
                    previous=prior_committed,
                )
            if prepared_installed:
                self.store.compare_and_restore(
                    self.store.prepared_subject,
                    installed=reference,
                    previous=prior_prepared,
                )
            raise
        self._record_publication(reference)
        return reference

    def _status_locked(
        self,
        run_ref: ArtifactRef,
        initial: ResearchFactoryRun,
        locked_release: PaperReleaseCandidate,
        chain: tuple[RevisionPair, ...],
    ) -> FactoryRunStatus:
        first = self._reconstruct(
            initial.design_ref, initial.release_ref, locked_release, chain
        )
        second = self._reconstruct(
            initial.design_ref, initial.release_ref, locked_release, chain
        )
        if first != initial or second != initial:
            raise FactoryIntegrityInvalid(
                "factory run differs from independent reconstruction",
                finding_kind="run-reconstruction-mismatch",
            )
        if self.store.current() != run_ref:
            raise FactoryIntegrityInvalid(
                "factory run is not the exact committed current run",
                finding_kind="run-current-invalid",
            )
        final = self.store.load(run_ref)
        if final != initial:
            raise FactoryIntegrityInvalid(
                "factory run changed during read-only status",
                finding_kind="run-reconstruction-mismatch",
            )
        return FactoryRunStatus(state="promotion-required", run_ref=run_ref, run=final)

    def _reconstruct(
        self,
        design_ref: ArtifactRef,
        release_ref: ArtifactRef,
        initial_release: PaperReleaseCandidate,
        chain: tuple[RevisionPair, ...],
    ) -> ResearchFactoryRun:
        design = self.design_resolver.resolve(design_ref)
        release = self.release_service._status_locked(
            release_ref, initial_release, chain
        )
        ledgers = self.release_service.audit_service.ledger_service
        maps = self.release_service.audit_service.map_service
        ledger = ledgers._load(release.ledger_ref)
        argument_map = maps._load(release.map_ref)
        binding = reconstruct_binding_report(
            design, release, ledger, argument_map=argument_map
        )
        profiles = tuple(
            CapabilityProfileBinding(
                profile_id=profile_id,
                registered_version=design.manifest.method_profiles[profile_id],
                sha256=digest,
            )
            for profile_id, digest in design.method_profile_sha256.items()
        )
        return ResearchFactoryRun(
            schema_version="factory.research-run.v1",
            factory_run_id=factory_run_id(design_ref, release_ref),
            producer="research-factory-run-v1",
            design_ref=design_ref,
            design=design,
            release_ref=release_ref,
            release=release,
            binding_report=binding,
            artifact_refs=_canonical_artifact_refs(
                design_ref, design, release_ref, release
            ),
            analysis_refs=release.analysis_refs,
            output_refs=release.output_refs,
            capability_profiles=profiles,
            assembly_verdict="assembled",
        )

    def _record_publication(self, reference: ArtifactRef) -> None:
        events = EventLog(self.design_resolver.factory_root / "factory-events.jsonl")
        event_id = f"{reference.artifact_id}.published"
        existing = tuple(
            item for item in events.read_all() if item.event_id == event_id
        )
        if existing:
            if len(existing) != 1 or existing[0].payload.get(
                "run_ref"
            ) != reference.model_dump(mode="json"):
                raise FactoryIntegrityInvalid(
                    "factory publication event identity conflicts",
                    finding_kind="run-event-conflict",
                )
            return
        append_event_atomically(
            events,
            EventRecord(
                event_id=event_id,
                run_id=reference.artifact_id,
                event_type="factory_run_published",
                actor="research-factory-run-v1",
                timestamp=datetime.now(UTC),
                from_status=WorkflowStatus.RUNNING,
                to_status=WorkflowStatus.REVIEW_REQUIRED,
                payload={"run_ref": reference.model_dump(mode="json")},
            ),
        )


def _factory_error(error: PaperBuilderError) -> FactoryError:
    if isinstance(error, PaperAuthorityInvalid):
        return FactoryAuthorityInvalid(str(error), finding_kind=error.finding_kind)
    if isinstance(error, PaperSupportInvalid):
        return FactorySupportInvalid(str(error), finding_kind=error.finding_kind)
    if isinstance(error, PaperIntegrityInvalid):
        return FactoryIntegrityInvalid(str(error), finding_kind=error.finding_kind)
    return FactoryIntegrityInvalid(str(error), finding_kind="paper-boundary-invalid")


__all__ = ["FactoryRunService"]
