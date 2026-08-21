"""Governed research-factory handoffs and immutable run assembly."""

from envresearch.factory.authority import AuthorityRootManifest
from envresearch.factory.contracts import (
    BindingField,
    CapabilityProfileBinding,
    CrossStageBindingReport,
    FactoryRunStatus,
    ResearchFactoryRun,
    factory_run_id,
)
from envresearch.factory.design_contracts import ApprovedDesignHandoff
from envresearch.factory.design_resolver import V02ApprovedDesignResolver
from envresearch.factory.promotion_contracts import (
    FACTORY_PROMOTION_CHECKLIST,
    FactoryPromotionContext,
    FactoryPromotionRejected,
    FactoryPromotionRequired,
    FactoryRunPromotion,
    derive_promotion_context,
    factory_promotion_id,
    promotion_context_id,
)
from envresearch.factory.service import FactoryRunService

__all__ = [
    "FACTORY_PROMOTION_CHECKLIST",
    "ApprovedDesignHandoff",
    "AuthorityRootManifest",
    "BindingField",
    "CapabilityProfileBinding",
    "CrossStageBindingReport",
    "FactoryPromotionContext",
    "FactoryPromotionRejected",
    "FactoryPromotionRequired",
    "FactoryRunPromotion",
    "FactoryRunService",
    "FactoryRunStatus",
    "ResearchFactoryRun",
    "V02ApprovedDesignResolver",
    "derive_promotion_context",
    "factory_promotion_id",
    "factory_run_id",
    "promotion_context_id",
]
