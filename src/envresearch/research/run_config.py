"""Explicit YAML config parsing and byte-integrity binding for research runs."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from envresearch.models.evidence import AcquisitionBudget
from envresearch.models.intake import SCORE_FIELDS
from envresearch.research.ranking import CharterRankingPolicy
from envresearch.research.workflow import ResearchRunConfig

_CONFIG_KEYS = frozenset(
    {
        "schema_version",
        "ranking_weights",
        "acquisition_budget",
        "require_claim_verified_citations",
        "citation_catalog_roots",
    }
)
_PRE_CATALOG_CONFIG_KEYS = _CONFIG_KEYS - {"citation_catalog_roots"}
_LEGACY_CONFIG_KEYS = _PRE_CATALOG_CONFIG_KEYS - {"require_claim_verified_citations"}
_BUDGET_KEYS = frozenset(
    {
        "max_download_bytes",
        "max_local_storage_bytes",
        "max_api_calls",
        "max_external_cost",
        "max_elapsed_seconds",
    }
)


@dataclass(frozen=True, slots=True)
class ExplicitResearchConfig:
    """Validated operational policy plus the exact source-byte identity."""

    data: bytes
    sha256: str
    ranking_policy: CharterRankingPolicy
    acquisition_budget: AcquisitionBudget
    require_claim_verified_citations: bool
    citation_catalog_roots: tuple[Path, ...]


def load_explicit_config(path: Path) -> ExplicitResearchConfig:
    """Read and strictly validate one complete explicit run config."""
    return parse_explicit_config(path.read_bytes())


def parse_explicit_config(data: bytes) -> ExplicitResearchConfig:
    """Strictly validate exact configuration bytes from a trusted reader."""
    payload = yaml.safe_load(data)
    if not isinstance(payload, dict):
        raise TypeError("research config must contain one YAML mapping")
    if (
        set(payload)
        not in {_CONFIG_KEYS, _PRE_CATALOG_CONFIG_KEYS, _LEGACY_CONFIG_KEYS}
        or payload.get("schema_version") != "1.0"
    ):
        raise ValueError("research config has an unsupported schema")
    weights = _ranking_weights(payload.get("ranking_weights"))
    budget = _acquisition_budget(payload.get("acquisition_budget"))
    require_citations = payload.get("require_claim_verified_citations", False)
    if not isinstance(require_citations, bool):
        raise TypeError(
            "require_claim_verified_citations must use a boolean wire value"
        )
    catalog_roots = _citation_catalog_roots(payload.get("citation_catalog_roots", []))
    if require_citations and not catalog_roots:
        raise ValueError("strict citation policy requires an authorized catalog")
    return ExplicitResearchConfig(
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        ranking_policy=CharterRankingPolicy(weights=weights),
        acquisition_budget=budget,
        require_claim_verified_citations=require_citations,
        citation_catalog_roots=catalog_roots,
    )


def verify_bound_config(path: Path, config: ResearchRunConfig) -> None:
    """Require exact copied bytes and operational fields to match durable config."""
    verify_bound_config_data(path.read_bytes(), config)


def verify_bound_config_data(data: bytes, config: ResearchRunConfig) -> None:
    """Verify config bytes obtained through a caller-owned safe read boundary."""
    if config.config_sha256 is None:
        raise ValueError("research run has no bound explicit config digest")
    explicit = parse_explicit_config(data)
    if explicit.sha256 != config.config_sha256:
        raise ValueError("copied research config digest does not match durable run")
    if explicit.ranking_policy != config.ranking_policy:
        raise ValueError("copied ranking policy does not match durable run")
    if explicit.acquisition_budget != config.acquisition_budget:
        raise ValueError("copied acquisition budget does not match durable run")
    if (
        explicit.require_claim_verified_citations
        != config.require_claim_verified_citations
    ):
        raise ValueError("copied citation integrity policy does not match durable run")
    if explicit.citation_catalog_roots != config.citation_catalog_roots:
        raise ValueError("copied citation catalog authority does not match durable run")


def _citation_catalog_roots(value: object) -> tuple[Path, ...]:
    if not isinstance(value, list) or any(type(item) is not str for item in value):
        raise TypeError("citation_catalog_roots must use a YAML string list")
    roots = tuple(
        sorted(
            (Path(item).expanduser().resolve(strict=True) for item in value), key=str
        )
    )
    if len(roots) != len(set(roots)):
        raise ValueError("citation_catalog_roots must be unique")
    return roots


def _ranking_weights(value: object) -> dict[str, float]:
    if not isinstance(value, dict) or set(value) != set(SCORE_FIELDS):
        raise ValueError("ranking_weights must declare exactly six dimensions")
    if any(
        isinstance(weight, bool) or not isinstance(weight, (int, float))
        for weight in value.values()
    ):
        raise ValueError("ranking_weights must use numeric wire values")
    try:
        weights = {str(key): float(weight) for key, weight in value.items()}
    except OverflowError as error:
        raise ValueError("ranking_weights must be finite and nonnegative") from error
    if any(not math.isfinite(weight) or weight < 0 for weight in weights.values()):
        raise ValueError("ranking_weights must be finite and nonnegative")
    if not math.isclose(sum(weights.values()), 1.0, abs_tol=1e-12):
        raise ValueError("ranking_weights must sum to one")
    return weights


def _acquisition_budget(value: object) -> AcquisitionBudget:
    if not isinstance(value, dict) or set(value) != _BUDGET_KEYS:
        raise ValueError("acquisition_budget must declare every resource limit")
    integer_fields = _BUDGET_KEYS - {"max_external_cost"}
    if any(
        isinstance(value[field], bool) or not isinstance(value[field], int)
        for field in integer_fields
    ):
        raise ValueError("acquisition budget counts must be integer wire values")
    try:
        cost = Decimal(str(value["max_external_cost"]))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("max_external_cost must be a decimal") from error
    return AcquisitionBudget(
        max_download_bytes=value["max_download_bytes"],
        max_local_storage_bytes=value["max_local_storage_bytes"],
        max_api_calls=value["max_api_calls"],
        max_external_cost=cost,
        max_elapsed_seconds=value["max_elapsed_seconds"],
    )
