"""Read-only terminal promotion reconstruction."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from envresearch.factory.contracts import FactoryRunStatus
from envresearch.factory.errors import FactoryError
from envresearch.factory.service import _factory_error
from envresearch.models.artifact import ArtifactRef
from envresearch.models.enums import GateStatus
from envresearch.paper.errors import PaperBuilderError

if TYPE_CHECKING:
    from envresearch.factory.promotion import FactoryPromotionService


def promotion_status(
    service: FactoryPromotionService,
    promotion_ref: ArtifactRef,
    run_ref: ArtifactRef,
) -> FactoryRunStatus:
    """Reconstruct a terminal exact-run decision without recovery."""
    preview = service.run_service.store.load(run_ref)
    try:
        with service.run_service.authority.lease(
            design_id=preview.design.design_id,
            release_ref=preview.release_ref,
            defer_body_errors=True,
        ) as (locked_release, chain):
            run = service.run_service._status_locked(
                run_ref, preview, locked_release, chain
            ).run
            with service.store.lease():
                promotion = service.store.load_promotion(promotion_ref)
                event = service.store.require_request_event(
                    promotion.context_ref, promotion.context.requested_by
                )
                service._validate_promotion_locked(
                    promotion_ref,
                    promotion.context_ref,
                    run_ref,
                    run,
                    event,
                    require_current=False,
                )
                state: Literal["promoted", "promotion-rejected"] = (
                    "promoted"
                    if promotion.decision.status is GateStatus.APPROVED
                    else "promotion-rejected"
                )
                return FactoryRunStatus(state=state, run_ref=run_ref, run=run)
    except FactoryError:
        raise
    except PaperBuilderError as exc:
        raise _factory_error(exc) from exc
