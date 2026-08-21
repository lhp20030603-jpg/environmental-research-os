"""Runtime compatibility checks shared by reviewed R package authorities."""

from __future__ import annotations

import re
from collections.abc import Sequence

from envresearch.econometrics.installed_package_authority import (
    InstalledPackageAuthority,
)
from envresearch.econometrics.method_authority import MethodAuthority
from envresearch.econometrics.r_evidence import PackageAuthority


def require_authority_runtime(
    authorities: Sequence[PackageAuthority], runtime_output: str
) -> None:
    """Bind every package's base dependency to one canonical R version."""
    match = re.search(
        r"\bversion\s+([0-9]+(?:\.[0-9]+){1,3})\b", runtime_output, re.IGNORECASE
    )
    if match is None:
        raise ValueError("reviewed R version is not canonical")
    runtime_version = match.group(1)
    for authority in authorities:
        dependencies = (
            authority.proposal.dependencies
            if isinstance(authority, MethodAuthority)
            else authority.dependencies
        )
        if isinstance(authority, InstalledPackageAuthority) and (
            authority.r_version != runtime_version
        ):
            raise ValueError(
                "frozen package runtime does not match the reviewed runtime"
            )
        if any(
            dependency.base and dependency.version != runtime_version
            for dependency in dependencies
        ):
            raise ValueError("R base dependency does not match the reviewed runtime")
