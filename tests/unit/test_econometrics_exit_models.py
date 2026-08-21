"""Strict blinded V0.3 exit contracts."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from envresearch.econometrics.exit_models import (
    ExitCase,
    ExpectedComparison,
    V03ExitManifest,
    numeric_matches,
)
from envresearch.models.artifact import ArtifactRef

GREEN = (
    "rct-itt",
    "did-event-study",
    "rdd-local-linear",
    "iv-2sls",
    "synthetic-control",
    "environmental-measurement",
    "meta-analysis",
    "panel-fe",
)
FAIL = GREEN[:-1]


def _ref(index: int) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=f"exit-case-{index:02d}",
        artifact_version=1,
        content_hash=f"{index + 1:064x}",
    )


def _manifest() -> dict[str, object]:
    cases = [
        ExitCase(
            case_id=f"green-{family}",
            family=family,
            role="green",
            case_ref=_ref(i),
            data_ref=_ref(i + 100),
        )
        for i, family in enumerate(GREEN)
    ]
    cases += [
        ExitCase(
            case_id=f"fail-{family}",
            family=family,
            role="assumption-failure",
            case_ref=_ref(i + 8),
            data_ref=_ref(i + 108),
        )
        for i, family in enumerate(FAIL)
    ]
    cases.append(
        ExitCase(
            case_id="integrity-output",
            family="rct-itt",
            role="integrity-failure",
            case_ref=_ref(15),
            data_ref=_ref(115),
        )
    )
    return {
        "schema_version": "econometrics.v03-exit-manifest.v1",
        "manifest_id": "v03-wave1",
        "cases": tuple(cases),
        "expectation_catalog_ref": _ref(16),
    }


def test_manifest_requires_exact_roles_families_and_unique_refs() -> None:
    assert V03ExitManifest.model_validate(_manifest())
    for mutation in ("missing", "duplicate", "family"):
        payload = deepcopy(_manifest())
        cases = list(payload["cases"])
        if mutation == "missing":
            cases.pop()
        elif mutation == "duplicate":
            cases[-1] = cases[0]
        else:
            cases[0] = cases[0].model_copy(update={"family": "panel-fe"})
        payload["cases"] = tuple(cases)
        with pytest.raises(ValidationError):
            V03ExitManifest.model_validate(payload)


def test_comparison_tolerance_is_finite_and_inclusive() -> None:
    comparison = ExpectedComparison(
        comparison_type="csv",
        output_name="effect.csv",
        selector="row=ATT,column=estimate",
        expected=10.0,
        atol=0.1,
        rtol=0.01,
    )
    assert numeric_matches(10.2, comparison)
    assert not numeric_matches(10.2000001, comparison)
    for value in (-1.0, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            ExpectedComparison(
                comparison_type="csv",
                output_name="x.csv",
                selector="row=a,column=b",
                expected=1.0,
                atol=value,
                rtol=0,
            )


def test_runner_case_contract_forbids_paths_and_expectations() -> None:
    case = ExitCase(
        case_id="green-rct",
        family="rct-itt",
        role="green",
        case_ref=_ref(1),
        data_ref=_ref(101),
    )
    assert (
        "path" not in case.model_dump_json()
        and "expected" not in case.model_dump_json()
    )
    with pytest.raises(ValidationError):
        ExitCase.model_validate({**case.model_dump(), "source_path": "/secret"})
