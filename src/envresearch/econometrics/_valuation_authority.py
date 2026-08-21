"""Shared method-selected package authority rules for valuation recipes."""

from __future__ import annotations

from envresearch.econometrics.installed_package_authority import (
    InstalledPackageAuthority,
)
from envresearch.econometrics.method_authority import MethodAuthority
from envresearch.econometrics.r_evidence import PackageAuthority
from envresearch.econometrics.valuation_contracts import (
    ContingentValuationSpec,
    DiscreteChoiceSpec,
    HedonicSpec,
    TravelCostSpec,
)

ValuationSpec = (
    HedonicSpec | TravelCostSpec | ContingentValuationSpec | DiscreteChoiceSpec
)


def required_package_names(spec: object) -> set[str]:
    """Return non-base package roots required by one approved valuation spec."""
    if isinstance(spec, HedonicSpec):
        return {"fixest"}
    if isinstance(spec, TravelCostSpec):
        return {"fixest" if spec.family == "poisson" else "MASS"}
    if isinstance(spec, DiscreteChoiceSpec):
        return {"survival"}
    return set()


def package_authorities_match(
    spec: ValuationSpec, authorities: tuple[PackageAuthority, ...]
) -> bool:
    """Require every method-selected package among bound execution authorities."""
    observed = {_package_name(item) for item in authorities}
    return required_package_names(spec).issubset(observed)


def external_package_authority_matches(
    spec: ValuationSpec,
    observed: tuple[PackageAuthority, ...],
    expected: tuple[PackageAuthority, ...] | None,
) -> bool:
    """Require exact externally reopened authority for package-backed methods."""
    if expected is None:
        return not observed and not required_package_names(spec)
    return observed == expected


def _package_name(authority: PackageAuthority) -> str:
    if isinstance(authority, MethodAuthority):
        return authority.proposal.package
    if isinstance(authority, InstalledPackageAuthority):
        return authority.package
    raise TypeError("unsupported package authority record")
