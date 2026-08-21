"""Protected independent human promotion for exact governed factory runs."""

from __future__ import annotations

import hashlib

from pydantic import ValidationError

from envresearch.factory._promotion_publish import _PromotionPublisher
from envresearch.factory._promotion_status import promotion_status
from envresearch.factory._promotion_store import _FactoryPromotionStore
from envresearch.factory.contracts import FactoryRunStatus, ResearchFactoryRun
from envresearch.factory.errors import (
    FactoryAuthorityInvalid,
    FactoryError,
    FactoryIntegrityInvalid,
    FactoryScopeExceeded,
)
from envresearch.factory.promotion_contracts import (
    FactoryPromotionContext,
    FactoryRunPromotion,
    derive_promotion_context,
    factory_promotion_id,
)
from envresearch.factory.service import FactoryRunService, _factory_error
from envresearch.kernel.events import EventRecord
from envresearch.kernel.gates import GateDecision
from envresearch.models.artifact import ArtifactRef
from envresearch.models.enums import GateStatus
from envresearch.paper.errors import PaperBuilderError
from envresearch.paper.release_contracts import PaperReleaseCandidate
from envresearch.paper.release_revisions import RevisionPair


class FactoryPromotionService:
    def __init__(self, run_service: FactoryRunService) -> None:
        self.run_service = run_service
        self.principals = run_service.design_resolver.orchestrator.principals
        self.store = _FactoryPromotionStore(run_service.registry, self.principals)
        self._publisher = _PromotionPublisher(self)

    def request(self, run_ref: ArtifactRef, requested_by: str) -> ArtifactRef:
        try:
            requester = requested_by.strip().casefold()
            if not requester or requester != requested_by:
                raise FactoryAuthorityInvalid(
                    "promotion requester must be one canonical principal",
                    finding_kind="promotion-requester-invalid",
                )
            self.store.probe_context_intent(run_ref, requester)
            preview = self.run_service.store.load(run_ref)
            with self.run_service.authority.lease(
                design_id=preview.design.design_id,
                release_ref=preview.release_ref,
                defer_body_errors=True,
                bootstrap=True,
            ) as (locked_release, chain):
                run = self.run_service._status_locked(
                    run_ref, preview, locked_release, chain
                ).run
                with self.store.lease(bootstrap=True):
                    return self._request_locked(
                        run_ref, run, requester, locked_release, chain
                    )
        except FactoryError:
            raise
        except PaperBuilderError as exc:
            raise _factory_error(exc) from exc

    def record(
        self,
        context_ref: ArtifactRef,
        decision: GateDecision,
        principal_capability: str,
    ) -> ArtifactRef:
        durable_decision = GateDecision.model_validate_json(decision.model_dump_json())
        capability_digest = hashlib.sha256(
            principal_capability.strip().encode()
        ).hexdigest()
        context_preview = self.store.load_context(context_ref)
        self.run_service.authority.require_independent_decision(
            context_preview, durable_decision, ()
        )
        principal = self.run_service.authority.authenticate_promotion_principal(
            principal_capability, durable_decision.decided_by, ()
        )
        self.store.probe_promotion_intent(
            context_ref, durable_decision, capability_digest, principal
        )
        try:
            with self.run_service.authority.lease(
                design_id=context_preview.run.design.design_id,
                release_ref=context_preview.run.release_ref,
                defer_body_errors=True,
                bootstrap=True,
            ) as (locked_release, chain):
                run = self.run_service._status_locked(
                    context_preview.run_ref,
                    context_preview.run,
                    locked_release,
                    chain,
                ).run
                with self.store.lease():
                    return self._record_locked(
                        context_ref,
                        context_preview.run_ref,
                        run,
                        durable_decision,
                        principal_capability,
                        locked_release,
                        chain,
                    )
        except FactoryError:
            raise
        except PaperBuilderError as exc:
            raise _factory_error(exc) from exc

    def status(
        self, promotion_ref: ArtifactRef, run_ref: ArtifactRef
    ) -> FactoryRunStatus:
        return promotion_status(self, promotion_ref, run_ref)

    def _request_locked(
        self,
        run_ref: ArtifactRef,
        run: ResearchFactoryRun,
        requested_by: str,
        locked_release: PaperReleaseCandidate,
        chain: tuple[RevisionPair, ...],
    ) -> ArtifactRef:
        requester = requested_by.strip().casefold()
        if not requester or requester != requested_by:
            raise ValueError("promotion requester must be one canonical principal")
        prepared = self.store.context_prepared()
        committed = self.store.context_committed()
        promotion_prepared = self.store.promotion_prepared()
        promotion_committed = self.store.promotion_committed()
        if promotion_prepared != promotion_committed:
            if promotion_prepared is None or committed is None:
                raise FactoryIntegrityInvalid(
                    "promotion decision pointers conflict",
                    finding_kind="promotion-pointer-conflict",
                )
            staged = self.store.load_promotion(promotion_prepared)
            if (
                staged.context_ref != committed
                or staged.context.requested_by != requester
            ):
                raise FactoryIntegrityInvalid(
                    "prepared promotion conflicts with request retry",
                    finding_kind="promotion-request-conflict",
                )
            self.store.ensure_request_event(committed, requester)
            return committed
        if prepared != committed:
            return self._resume_context(
                prepared, committed, run_ref, run, requester, locked_release, chain
            )
        generation = 1
        if committed is not None:
            existing = self._validate_context_locked(committed, run, current=True)
            current_promotion = promotion_committed
            if current_promotion is None:
                if existing.requested_by != requester:
                    raise FactoryIntegrityInvalid(
                        "pending promotion request conflicts with another requester",
                        finding_kind="promotion-request-conflict",
                    )
                self.store.ensure_request_event(committed, requester)
                return committed
            staged = self.store.load_promotion(current_promotion)
            if staged.context_ref != committed:
                self._require_previous_rejection(
                    current_promotion, existing, run_ref, run
                )
                if existing.requested_by != requester:
                    raise FactoryIntegrityInvalid(
                        "pending promotion request conflicts with another requester",
                        finding_kind="promotion-request-conflict",
                    )
                self.store.ensure_request_event(committed, requester)
                return committed
            event = self.store.require_request_event(committed, existing.requested_by)
            terminal = self._validate_promotion_locked(
                current_promotion,
                committed,
                run_ref,
                run,
                event,
                require_current=True,
            )
            if terminal.decision.status is GateStatus.APPROVED:
                raise FactoryIntegrityInvalid(
                    "approved promotion is terminal",
                    finding_kind="promotion-terminal",
                )
            generation = existing.generation + 1
        context = derive_promotion_context(run_ref, run, requester, generation)
        return self._publisher.context(
            context, prepared, committed, locked_release, chain
        )

    def _require_previous_rejection(
        self,
        promotion_ref: ArtifactRef,
        current: FactoryPromotionContext,
        run_ref: ArtifactRef,
        run: ResearchFactoryRun,
    ) -> None:
        promotion = self.store.load_promotion(promotion_ref)
        previous_ref = promotion.context_ref
        event = self.store.require_request_event(
            previous_ref, promotion.context.requested_by
        )
        terminal = self._validate_promotion_locked(
            promotion_ref, previous_ref, run_ref, run, event, require_current=False
        )
        if (
            terminal.decision.status is not GateStatus.REJECTED
            or current.generation != terminal.context.generation + 1
        ):
            raise FactoryIntegrityInvalid(
                "current promotion context lacks an exact prior rejection",
                finding_kind="promotion-context-current-invalid",
            )

    def _resume_context(
        self,
        prepared: ArtifactRef | None,
        committed: ArtifactRef | None,
        run_ref: ArtifactRef,
        run: ResearchFactoryRun,
        requester: str,
        locked_release: PaperReleaseCandidate,
        chain: tuple[RevisionPair, ...],
    ) -> ArtifactRef:
        if prepared is None:
            raise FactoryIntegrityInvalid(
                "promotion context pointer pair is impossible",
                finding_kind="promotion-context-current-invalid",
            )
        candidate = self.store.load_context(prepared)
        if candidate.run_ref != run_ref or candidate.requested_by != requester:
            raise FactoryIntegrityInvalid(
                "prepared promotion context conflicts with retry",
                finding_kind="promotion-request-conflict",
            )
        if committed is not None:
            promotion_ref = self.store.promotion_current()
            if promotion_ref is None:
                raise FactoryIntegrityInvalid(
                    "prior promotion is missing during context recovery",
                    finding_kind="promotion-context-current-invalid",
                )
            self._require_previous_rejection(promotion_ref, candidate, run_ref, run)
        return self._publisher.context(
            candidate, prepared, committed, locked_release, chain
        )

    def _record_locked(
        self,
        context_ref: ArtifactRef,
        run_ref: ArtifactRef,
        run: ResearchFactoryRun,
        decision: GateDecision,
        capability: str,
        locked_release: PaperReleaseCandidate,
        chain: tuple[RevisionPair, ...],
    ) -> ArtifactRef:
        context = self._validate_context_locked(context_ref, run, current=True)
        event = self.store.require_request_event(context_ref, context.requested_by)
        reviewed = self.run_service.authority.reviewed_producers(run)
        self.run_service.authority.require_independent_decision(
            context, decision, reviewed
        )
        self.run_service.authority.require_decision_time(decision, event)
        principal = self.run_service.authority.authenticate_promotion_principal(
            capability, decision.decided_by, reviewed
        )
        capability_digest = hashlib.sha256(capability.strip().encode()).hexdigest()
        try:
            promotion = FactoryRunPromotion(
                schema_version="factory.run-promotion.v1",
                promotion_id=factory_promotion_id(
                    context_ref, decision, capability_digest, principal
                ),
                producer="research-factory-promotion-v1",
                context_ref=context_ref,
                context=context,
                decision=decision,
                principal_capability_sha256=capability_digest,
                authenticated_principal=principal,
                promotion_scope="individual-run-only",
                hidden_evaluation_status="not-run",
                product_release_status="scientific_release_pending",
            )
        except ValidationError as exc:
            if "broaden" in str(exc):
                raise FactoryScopeExceeded(
                    "promotion decision conditions broaden scientific scope",
                    finding_kind="promotion-scope",
                ) from exc
            raise
        return self._publish_promotion(
            promotion,
            context_ref,
            run_ref,
            run,
            event,
            capability,
            locked_release,
            chain,
        )

    def _publish_promotion(
        self,
        promotion: FactoryRunPromotion,
        context_ref: ArtifactRef,
        run_ref: ArtifactRef,
        run: ResearchFactoryRun,
        event: EventRecord,
        capability: str,
        locked_release: PaperReleaseCandidate,
        chain: tuple[RevisionPair, ...],
    ) -> ArtifactRef:
        return self._publisher.promotion(
            promotion,
            context_ref,
            run_ref,
            run,
            event,
            capability,
            locked_release,
            chain,
        )

    def _validate_context_locked(
        self, reference: ArtifactRef, run: ResearchFactoryRun, *, current: bool
    ) -> FactoryPromotionContext:
        context = self.store.load_context(reference)
        if context.run != run or (
            current and self.store.context_current() != reference
        ):
            raise FactoryIntegrityInvalid(
                "promotion context differs from exact current run authority",
                finding_kind="promotion-context-invalid",
            )
        return context

    def _validate_promotion_locked(
        self,
        reference: ArtifactRef,
        context_ref: ArtifactRef,
        run_ref: ArtifactRef,
        run: ResearchFactoryRun,
        event: EventRecord,
        *,
        require_current: bool,
    ) -> FactoryRunPromotion:
        promotion = self.store.load_promotion(reference)
        context = self._validate_context_locked(
            context_ref, run, current=require_current
        )
        fresh_event = self.store.require_request_event(
            context_ref, context.requested_by
        )
        if (
            promotion.context != context
            or context.run_ref != run_ref
            or event != fresh_event
            or promotion.decision.decided_at <= event.timestamp
            or (require_current and self.store.promotion_current() != reference)
        ):
            raise FactoryIntegrityInvalid(
                "promotion differs from exact current authority",
                finding_kind="promotion-invalid",
            )
        self.run_service.authority.require_promotion_principal(promotion, run)
        self.store.require_principal_capability(promotion)
        self.store.require_promotion_anchor(reference)
        self.store.require_decision_event(reference, promotion)
        return promotion

    def _fresh_run(
        self,
        run_ref: ArtifactRef,
        locked_release: PaperReleaseCandidate,
        chain: tuple[RevisionPair, ...],
    ) -> ResearchFactoryRun:
        return self.run_service._status_locked(
            run_ref,
            self.run_service.store.load(run_ref),
            locked_release,
            chain,
        ).run
