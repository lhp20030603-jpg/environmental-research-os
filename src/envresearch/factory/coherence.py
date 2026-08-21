"""Independent typed reconstruction of V0.2-to-V0.4 coherence."""

from __future__ import annotations

from envresearch.factory.contracts import (
    BindingField,
    BindingRelation,
    CrossStageBindingReport,
)
from envresearch.factory.design_contracts import ApprovedDesignHandoff
from envresearch.factory.errors import FactoryScopeExceeded, FactorySupportInvalid
from envresearch.models.design import EstimandSpecPayload
from envresearch.paper._audit_lineage import analysis_ref_key, output_ref_key
from envresearch.paper.argument_contracts import ArgumentMap
from envresearch.paper.contracts import ClaimEvidenceLedger, ClaimEvidenceRow
from envresearch.paper.release_contracts import PaperReleaseCandidate

_PROFILE_TO_METHOD = {
    "contingent-valuation": "contingent-valuation",
    "dce": "dce-clogit",
    "did-event-study": "did-event-study",
    "hedonic": "hedonic-pricing",
    "spatiotemporal": "spatiotemporal",
    "synthetic-control": "synthetic-control",
    "travel-cost": "travel-cost",
}


def reconstruct_binding_report(
    design: ApprovedDesignHandoff,
    release: PaperReleaseCandidate,
    ledger: ClaimEvidenceLedger,
    argument_map: ArgumentMap | None = None,
) -> CrossStageBindingReport:
    """Reconstruct typed compatibility without draft prose or acceptance helpers."""
    design = ApprovedDesignHandoff.model_validate(design.model_dump(mode="python"))
    release = PaperReleaseCandidate.model_validate(release.model_dump(mode="python"))
    ledger = ClaimEvidenceLedger.model_validate(ledger.model_dump(mode="python"))
    _require_release_lineage(release, ledger)
    if argument_map is not None:
        argument_map = ArgumentMap.model_validate(
            argument_map.model_dump(mode="python")
        )
        _require_argument_claims(argument_map, ledger)
    estimand = design.plan.estimand
    if estimand is None:
        raise FactorySupportInvalid(
            "approved design lacks the embedded typed estimand required for coherence",
            finding_kind="estimand-binding-missing",
        )
    expected_price = _typed_price_base(design.plan.data_boundaries)
    allowed_methods = _allowed_methods(design)
    release_limitations = {
        limitation for row in ledger.claims for limitation in row.limitations
    }
    design_limitations = tuple(sorted(design.plan.fallback_rules))
    fields = tuple(
        field
        for row in sorted(ledger.claims, key=lambda item: item.claim_id)
        for field in _row_bindings(
            row,
            allowed_methods=allowed_methods,
            estimand=estimand,
            price_base=expected_price,
            claim_mode=design.plan.claim_mode.value,
            design_limitations=design_limitations,
        )
    )
    return CrossStageBindingReport(
        schema_version="factory.cross-stage-binding.v1",
        producer="research-factory-coherence-v1",
        provenance_claim="retrospective-coherence",
        design_id=design.design_id,
        release_id=release.release_id,
        fields=fields,
        limitations=tuple(sorted({*design.plan.fallback_rules, *release_limitations})),
        verdict="coherent",
    )


def _allowed_methods(design: ApprovedDesignHandoff) -> dict[str, str]:
    refs = (
        design.plan.primary_method_profile_ref,
        *design.plan.alternative_method_profile_refs,
    )
    result: dict[str, str] = {}
    for reference in refs:
        profile_id, separator, version = reference.rpartition("@")
        if not separator or not profile_id or not version:
            raise FactorySupportInvalid(
                "approved method profile reference is not versioned",
                finding_kind="method-binding-invalid",
            )
        registered = design.manifest.method_profiles.get(profile_id)
        digest = design.method_profile_sha256.get(profile_id)
        method_id = _PROFILE_TO_METHOD.get(profile_id)
        if registered != version or digest is None or method_id is None:
            raise FactorySupportInvalid(
                "approved method profile lacks an explicit registered mapping",
                finding_kind="method-binding-invalid",
            )
        result[method_id] = reference
    return result


def _row_bindings(
    row: ClaimEvidenceRow,
    *,
    allowed_methods: dict[str, str],
    estimand: EstimandSpecPayload,
    price_base: str,
    claim_mode: str,
    design_limitations: tuple[str, ...],
) -> tuple[BindingField, ...]:
    method_ref = allowed_methods.get(row.method_id)
    if method_ref is None:
        raise FactorySupportInvalid(
            "paper method is not one of the approved registered profiles",
            finding_kind="method-binding-invalid",
        )
    target_parameter = estimand.target_parameter
    if row.quantity != target_parameter:
        raise FactorySupportInvalid(
            "paper quantity differs from the approved typed estimand",
            finding_kind="estimand-binding-invalid",
        )
    unit = _scope_field(row.claim_id, "unit", estimand.unit, row.unit)
    population = _scope_field(
        row.claim_id,
        "population",
        estimand.population,
        row.population_basis,
    )
    time = _scope_field(
        row.claim_id,
        "time",
        estimand.time_horizon,
        row.time_basis,
    )
    price = _scope_field(row.claim_id, "price", price_base, row.price_base)
    strength_relation = _strength_relation(claim_mode, row.allowed_strength)
    return (
        BindingField(
            dimension="method",
            claim_id=row.claim_id,
            design_value=method_ref,
            release_value=row.method_id,
            relation="exact",
        ),
        BindingField(
            dimension="estimand",
            claim_id=row.claim_id,
            design_value=target_parameter,
            release_value=row.quantity,
            relation="exact",
        ),
        unit,
        population,
        time,
        price,
        BindingField(
            dimension="strength",
            claim_id=row.claim_id,
            design_value=claim_mode,
            release_value=row.allowed_strength,
            relation=strength_relation,
        ),
        BindingField(
            dimension="limitation",
            claim_id=row.claim_id,
            design_value=" | ".join(design_limitations),
            release_value=" | ".join(sorted(row.limitations)),
            relation=_limitation_relation(design_limitations, row),
        ),
    )


def _typed_price_base(boundaries: tuple[str, ...]) -> str:
    values = tuple(
        item.removeprefix("price-base:")
        for item in boundaries
        if item.startswith("price-base:")
    )
    if len(values) != 1 or not values[0]:
        raise FactorySupportInvalid(
            "approved design lacks one explicit typed price-base binding",
            finding_kind="price-binding-missing",
        )
    return values[0]


def _scope_field(
    claim_id: str, dimension: str, design: str, release: str
) -> BindingField:
    if design == release:
        relation: BindingRelation = "exact"
    elif design.endswith(":*"):
        prefix = design[:-2]
        namespace = f"{prefix}:"
        suffix = release.removeprefix(namespace)
        if release == prefix or (
            release.startswith(namespace)
            and suffix
            and all(part and part == part.strip() for part in suffix.split(":"))
        ):
            relation = "narrower"
        else:
            raise FactoryScopeExceeded(
                f"paper {dimension} exceeds the approved typed scope",
                finding_kind=f"{dimension}-binding-invalid",
            )
    else:
        raise FactoryScopeExceeded(
            f"paper {dimension} exceeds the approved typed scope",
            finding_kind=f"{dimension}-binding-invalid",
        )
    return BindingField(
        dimension=dimension,  # type: ignore[arg-type]
        claim_id=claim_id,
        design_value=design,
        release_value=release,
        relation=relation,
    )


def _strength_relation(claim_mode: str, strength: str) -> BindingRelation:
    allowed: dict[str, dict[str, BindingRelation]] = {
        "causal": {
            "design-based-causal": "exact",
            "associational": "narrower",
            "model-conditional-valuation": "narrower",
            "descriptive": "narrower",
        },
        "descriptive": {"descriptive": "exact"},
    }
    relation = allowed.get(claim_mode, {}).get(strength)
    if relation is None:
        raise FactoryScopeExceeded(
            "paper claim strength exceeds the approved claim mode",
            finding_kind="strength-binding-invalid",
        )
    return relation


def _limitation_relation(
    design_limitations: tuple[str, ...], row: ClaimEvidenceRow
) -> BindingRelation:
    required = set(design_limitations)
    released = set(row.limitations)
    if not required.issubset(released):
        raise FactorySupportInvalid(
            "paper claim does not preserve every approved design limitation",
            finding_kind="limitation-binding-invalid",
        )
    return "exact" if required == released else "narrower"


def _require_argument_claims(
    argument_map: ArgumentMap, ledger: ClaimEvidenceLedger
) -> None:
    if (
        argument_map.ledger_ref.artifact_id != ledger.ledger_id
        or argument_map.transition_ref != ledger.transition_ref
    ):
        raise FactorySupportInvalid(
            "argument map does not bind the supplied typed ledger",
            finding_kind="argument-binding-invalid",
        )
    mapped = {
        claim_id
        for node in argument_map.nodes
        if node.node_type == "empirical-claim"
        for claim_id in node.claim_ids
    }
    expected = {row.claim_id for row in ledger.claims}
    if mapped != expected:
        raise FactorySupportInvalid(
            "argument map does not bind every paper claim row",
            finding_kind="argument-binding-invalid",
        )


def _require_release_lineage(
    release: PaperReleaseCandidate, ledger: ClaimEvidenceLedger
) -> None:
    if ledger.transition_ref not in release.transitive_refs:
        raise FactorySupportInvalid(
            "ledger transition differs from the exact release closure",
            finding_kind="transition-binding-invalid",
        )
    analysis_refs = tuple(
        sorted({row.analysis_ref for row in ledger.claims}, key=analysis_ref_key)
    )
    output_refs = tuple(
        sorted(
            {output for row in ledger.claims for output in row.output_evidence},
            key=output_ref_key,
        )
    )
    if analysis_refs != release.analysis_refs:
        raise FactorySupportInvalid(
            "ledger analyses differ from the exact release closure",
            finding_kind="analysis-binding-invalid",
        )
    if output_refs != release.output_refs:
        raise FactorySupportInvalid(
            "ledger outputs differ from the exact release closure",
            finding_kind="output-binding-invalid",
        )


__all__ = ["reconstruct_binding_report"]
