"""Owned pointer publication for promotion context and terminal decision."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from envresearch.factory.errors import FactoryError, FactoryIntegrityInvalid
from envresearch.factory.promotion_contracts import (
    FactoryPromotionContext,
    FactoryRunPromotion,
)
from envresearch.kernel.events import EventRecord
from envresearch.models.artifact import ArtifactRef

if TYPE_CHECKING:
    from envresearch.factory.contracts import ResearchFactoryRun
    from envresearch.factory.promotion import FactoryPromotionService
    from envresearch.paper.release_contracts import PaperReleaseCandidate
    from envresearch.paper.release_revisions import RevisionPair


class _PromotionPublisher:
    def __init__(self, owner: FactoryPromotionService) -> None:
        self.owner = owner

    def context(
        self,
        context: FactoryPromotionContext,
        prior_prepared: ArtifactRef | None,
        prior_committed: ArtifactRef | None,
        locked_release: PaperReleaseCandidate,
        chain: tuple[RevisionPair, ...],
    ) -> ArtifactRef:
        store = self.owner.store
        reference = store.publish_context(context)
        prepared_installed = prior_prepared != reference
        committed_installed = prior_committed != reference
        try:
            store.install(
                store.context_prepared_subject, reference, previous=prior_prepared
            )
            store.ensure_request_event(reference, context.requested_by)
            self.owner._validate_context_locked(reference, context.run, current=False)
            store.install(
                store.context_committed_subject, reference, previous=prior_committed
            )
            fresh = self.owner._fresh_run(context.run_ref, locked_release, chain)
            self.owner._validate_context_locked(reference, fresh, current=True)
            store.require_request_event(reference, context.requested_by)
            return reference
        except (FactoryError, OSError, TypeError, ValueError, ValidationError):
            if committed_installed and store.context_committed() == reference:
                store.compare_and_restore(
                    store.context_committed_subject,
                    installed=reference,
                    previous=prior_committed,
                )
            if prepared_installed and store.context_prepared() == reference:
                store.compare_and_restore(
                    store.context_prepared_subject,
                    installed=reference,
                    previous=prior_prepared,
                )
            raise

    def promotion(
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
        store = self.owner.store
        reference = store.publish_promotion(promotion)
        terminal = store.terminal_ref(context_ref)
        if terminal is not None and terminal != reference:
            raise FactoryIntegrityInvalid(
                "promotion context already has a different terminal decision",
                finding_kind="promotion-terminal",
            )
        prior_prepared = store.promotion_prepared()
        prior_committed = store.promotion_committed()
        if prior_prepared != prior_committed:
            if prior_prepared != reference:
                raise FactoryIntegrityInvalid(
                    "prepared promotion conflicts with retry",
                    finding_kind="promotion-pointer-conflict",
                )
        elif prior_committed is not None:
            existing = store.load_promotion(prior_committed)
            if existing.context_ref == context_ref:
                if existing == promotion:
                    self.owner._validate_promotion_locked(
                        prior_committed,
                        context_ref,
                        run_ref,
                        run,
                        event,
                        require_current=True,
                    )
                    return prior_committed
                raise FactoryIntegrityInvalid(
                    "promotion context already has a different terminal decision",
                    finding_kind="promotion-terminal",
                )
        return self._install_promotion(
            reference,
            promotion,
            context_ref,
            run_ref,
            run,
            event,
            capability,
            locked_release,
            chain,
            prior_prepared,
            prior_committed,
        )

    def _install_promotion(
        self,
        reference: ArtifactRef,
        promotion: FactoryRunPromotion,
        context_ref: ArtifactRef,
        run_ref: ArtifactRef,
        run: ResearchFactoryRun,
        event: EventRecord,
        capability: str,
        locked_release: PaperReleaseCandidate,
        chain: tuple[RevisionPair, ...],
        prior_prepared: ArtifactRef | None,
        prior_committed: ArtifactRef | None,
    ) -> ArtifactRef:
        store = self.owner.store
        prepared_installed = prior_prepared != reference
        committed_installed = prior_committed != reference
        try:
            store.install(
                store.promotion_prepared_subject, reference, previous=prior_prepared
            )
            store.ensure_decision_event(reference, promotion)
            self.owner._validate_promotion_locked(
                reference, context_ref, run_ref, run, event, require_current=False
            )
            store.install(
                store.promotion_committed_subject, reference, previous=prior_committed
            )
            fresh = self.owner._fresh_run(run_ref, locked_release, chain)
            self.owner.run_service.authority.authenticate_promotion_principal(
                capability,
                promotion.decision.decided_by,
                self.owner.run_service.authority.reviewed_producers(fresh),
            )
            fresh_event = store.require_request_event(
                context_ref, promotion.context.requested_by
            )
            self.owner._validate_promotion_locked(
                reference,
                context_ref,
                run_ref,
                fresh,
                fresh_event,
                require_current=True,
            )
            return reference
        except (FactoryError, OSError, TypeError, ValueError, ValidationError):
            if committed_installed and store.promotion_committed() == reference:
                store.compare_and_restore(
                    store.promotion_committed_subject,
                    installed=reference,
                    previous=prior_committed,
                )
            if prepared_installed and store.promotion_prepared() == reference:
                store.compare_and_restore(
                    store.promotion_prepared_subject,
                    installed=reference,
                    previous=prior_prepared,
                )
            raise


__all__ = ["_PromotionPublisher"]
