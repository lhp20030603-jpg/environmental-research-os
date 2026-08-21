"""Strict contracts for managed external R-package authorities."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from envresearch.econometrics.managed_r_library import ManagedRLibrary
from envresearch.econometrics.method_authority import (
    MethodAuthority,
    MethodAuthorityProposal,
)


def _proposal(**updates: object) -> MethodAuthorityProposal:
    payload: dict[str, object] = {
        "package": "rdrobust",
        "version": "3.0.0",
        "source_url": "https://cran.r-project.org/src/contrib/rdrobust_3.0.0.tar.gz",
        "source_sha256": "a" * 64,
        "license": "GPL-3.0-or-later",
        "description_license": "GPL-3",
        "dependencies": ({"package": "R", "version": "4.4.3", "base": True},),
    }
    payload.update(updates)
    return MethodAuthorityProposal.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("package", " rdrobust"),
        ("version", ""),
        ("source_url", "http://cran.r-project.org/rdrobust.tar.gz"),
        ("source_sha256", "A" * 64),
        ("license", "not-a-license"),
        (
            "dependencies",
            (
                {"package": "R", "version": "4.4.3", "base": True},
                {"package": "R", "version": "4.4.3", "base": True},
            ),
        ),
    ],
)
def test_proposal_rejects_mutable_or_ambiguous_authority(
    field: str, value: object
) -> None:
    """Authority declarations are canonical before external bytes are fetched."""
    with pytest.raises(ValidationError):
        _proposal(**{field: value})


def test_authority_reference_is_content_derived() -> None:
    """The durable reference changes with any admitted package identity."""
    authority = MethodAuthority(
        proposal=_proposal(),
        installed_tree_sha256="b" * 64,
        source_relative_path=Path("authorities/sources/" + "a" * 64 + "/source.tar.gz"),
        package_relative_path=Path("authorities/r-library/rdrobust"),
        description_sha256="c" * 64,
        observed_license="GPL-3",
        observed_at=datetime(2026, 8, 12, tzinfo=UTC),
    )

    reference = authority.ref()

    assert reference.artifact_id == "r-package-authority-rdrobust-3.0.0"
    assert reference.artifact_version == 1
    assert reference.content_hash == authority.content_hash()
    assert (
        authority.model_copy(update={"installed_tree_sha256": "d" * 64}).ref()
        != reference
    )


def test_managed_library_is_run_root_scoped(tmp_path: Path) -> None:
    """The package library never resolves through a default user R library."""
    library = ManagedRLibrary(tmp_path / "store")

    assert library.root == (tmp_path / "store/authorities/r-library").resolve()
    assert library.root.is_relative_to((tmp_path / "store").resolve())


def test_proposal_accepts_canonical_dual_spdx_expression() -> None:
    proposal = _proposal(
        package="synthdid",
        version="0.0.9",
        license="GPL-2.0-or-later OR BSD-3-Clause",
        description_license="GPL (>= 2) | BSD_3_clause + file LICENSE",
    )

    assert proposal.license == "GPL-2.0-or-later OR BSD-3-Clause"

    with pytest.raises(ValidationError):
        _proposal(license="GPL-3.0-only OR GPL-3.0-only")
