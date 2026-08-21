"""Owner-configured trust root for canonical scientific release."""

from __future__ import annotations

import os
from pathlib import Path

from envresearch.benchmarks.blind_authority import (
    AuthorityTrustAnchor,
    canonical_json,
)
from envresearch.benchmarks.blind_enrollment_marker import require_frozen_enrollment
from envresearch.benchmarks.blind_release import CaseForRelease
from envresearch.benchmarks.blind_trust_store import read_authority_anchor
from envresearch.benchmarks.design_files import PinnedFixtureRoot
from envresearch.research.principal_registry import PrincipalRegistry
from envresearch.workers.filesystem import read_regular_at

_RELEASE_AUTHORITY_ENV = "ENVRESEARCH_BLIND_RELEASE_AUTHORITY"
_DIRECTORY_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
)


def read_expected_release_authority(
    catalog_root: Path, run_root: Path
) -> AuthorityTrustAnchor | None:
    """Read a protected owner configuration outside catalog and run inputs."""
    configured = os.environ.get(_RELEASE_AUTHORITY_ENV)
    if configured is None:
        return None
    path = Path(configured)
    if not path.is_absolute():
        raise ValueError("blind release authority path must be absolute")
    lexical = Path(os.path.abspath(path))
    if any(
        lexical == root or lexical.is_relative_to(root)
        for root in (Path(os.path.abspath(catalog_root)), Path(os.path.abspath(run_root)))
    ):
        raise ValueError("blind release authority must be outside catalog and run roots")
    with (
        PinnedFixtureRoot(catalog_root) as catalog,
        PinnedFixtureRoot(run_root) as run,
        PinnedFixtureRoot(lexical.parent) as parent,
    ):
        if _contains_directory(catalog.fd, parent.fd) or _contains_directory(
            run.fd, parent.fd
        ):
            raise ValueError(
                "blind release authority must be outside catalog and run roots"
            )
        data = read_regular_at(
            parent.fd,
            lexical.name,
            description="blind release authority",
            required_mode=0o600,
            required_owner=os.geteuid(),
        )
    anchor = AuthorityTrustAnchor.model_validate_json(data)
    if data != canonical_json(anchor.model_dump(mode="json")):
        raise ValueError("blind release authority is not canonical")
    return anchor


def authenticate_catalog_release(
    cases: tuple[CaseForRelease, ...],
    registries: tuple[tuple[str, PrincipalRegistry], ...],
    expected: AuthorityTrustAnchor,
) -> None:
    """Validate canonical on-disk authority evidence without issuing readiness."""
    case_ids = {case.case_id for case in cases}
    if (
        {case_id for case_id, _registry in registries} != case_ids
        or len(registries) != len(cases)
    ):
        raise ValueError("release registries do not match evaluated cases")
    verified = tuple(
        require_frozen_enrollment(registry, case_id)
        for case_id, registry in registries
    )
    if any(read_authority_anchor(registry) != expected for _, registry in registries):
        raise ValueError("run authority does not match release authority")
    if len({item.signed_sha256 for item in verified}) != 1:
        raise ValueError("release cases do not share one sealed enrollment")
    enrollment = verified[0]
    sealed = {case.case_id: case for case in enrollment.payload.cases}
    if set(sealed) != case_ids:
        raise ValueError("release cases do not match sealed enrollment")
    if any(
        case.method_family != sealed[case.case_id].method_family
        or case.cohort.value != sealed[case.case_id].cohort
        for case in cases
    ):
        raise ValueError("release case metadata does not match enrollment")


def _contains_directory(ancestor_fd: int, descendant_fd: int) -> bool:
    ancestor = os.fstat(ancestor_fd)
    current = os.dup(descendant_fd)
    try:
        while True:
            identity = os.fstat(current)
            if (identity.st_dev, identity.st_ino) == (ancestor.st_dev, ancestor.st_ino):
                return True
            parent = os.open("..", _DIRECTORY_FLAGS, dir_fd=current)
            parent_identity = os.fstat(parent)
            os.close(current)
            current = parent
            if (parent_identity.st_dev, parent_identity.st_ino) == (
                identity.st_dev,
                identity.st_ino,
            ):
                return False
    finally:
        os.close(current)
