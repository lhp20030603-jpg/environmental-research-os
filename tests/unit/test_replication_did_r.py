"""Tests for R-first Tier-2 DiD command planning and derived report parsing."""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from replication_did_r_fixtures import (
    approved_artifact,
    artifact_ref,
    author_mapping,
    cleanup_workspaces,
    did_spec,
    package,
    profile,
)

from envresearch.replication.contracts import Tier2ExpectedOutput
from envresearch.replication.did_r import (
    RDidAdapter,
    parse_derived_report,
)


@pytest.fixture(autouse=True)
def remove_test_workspaces() -> Generator[None, None, None]:
    """Remove only the exact trusted workspace children created in this module."""
    yield
    cleanup_workspaces()


def test_derived_r_plan_is_labelled_and_separate_from_author_plan(
    tmp_path: Path,
) -> None:
    """Derived diagnostics use their own output namespace and generated script."""
    approved = approved_artifact()
    adapter = RDidAdapter(profile(), approved)

    author = adapter.author_plan(
        package(artifact_ref(approved)), author_mapping(tmp_path)
    )
    derived = adapter.derived_plan(package(artifact_ref(approved)), did_spec(tmp_path))

    assert author.output_namespace == "author-reproduction"
    assert derived.output_namespace == "derived-did-event-study"
    assert author.argv == ("Rscript", "/input/code/run.R")
    assert derived.argv == ("Rscript", "/input/.generated/derived_did.R")
    assert author.output_root.name == "author-reproduction"
    assert derived.output_root.name == "derived-did-event-study"
    assert author.output_root != derived.output_root
    assert "did::att_gt" in derived.generated_files["derived_did.R"]
    assert "reproduction_pass" not in derived.generated_files["derived_did.R"]


def test_author_plan_rejects_a_script_not_declared_in_inventory(
    tmp_path: Path,
) -> None:
    """Author execution cannot choose files outside the acquired package mapping."""
    mapping = author_mapping(tmp_path).model_copy(
        update={"script_path": Path("code/not-approved.R")}
    )

    with pytest.raises(ValueError, match="author script"):
        RDidAdapter(profile(), approved_artifact()).author_plan(
            package(artifact_ref(approved_artifact())), mapping
        )


def test_derived_script_emits_the_required_machine_readable_diagnostics(
    tmp_path: Path,
) -> None:
    """The generated output retains diagnostic evidence without substantive claims."""
    script = (
        RDidAdapter(profile(), approved_artifact())
        .derived_plan(package(artifact_ref(approved_artifact())), did_spec(tmp_path))
        .generated_files["derived_did.R"]
    )

    for diagnostic in (
        "first_treated_period",
        "treated_observations",
        "units = length(unique(data[[unit_column]]))",
        "confidence_intervals",
        "fixest::coeftable(twfe)",
    ):
        assert diagnostic in script
    assert (
        "dir.create(dirname(output_path), recursive = TRUE, showWarnings = FALSE)"
        in script
    )


def test_adapter_rejects_inventory_not_bound_to_the_sealed_approval(
    tmp_path: Path,
) -> None:
    """An acquired package must be bound to the supplied approved intake reference."""
    approved = approved_artifact()
    other_ref = artifact_ref(approved).model_copy(update={"content_hash": "b" * 64})

    with pytest.raises(ValueError, match="approved intake"):
        RDidAdapter(profile(), approved).derived_plan(
            package(other_ref), did_spec(tmp_path)
        )


@pytest.mark.parametrize("variant", ["unsealed", "tampered"])
def test_adapter_rejects_unsealed_or_tampered_approval_artifacts(variant: str) -> None:
    """The resolved approval must remain sealed and content-authentic."""
    approved = approved_artifact()
    if variant == "unsealed":
        artifact = approved.model_copy(
            update={
                "envelope": approved.envelope.model_copy(update={"content_hash": None})
            }
        )
    else:
        artifact = approved.model_copy(
            update={
                "payload": approved.payload.model_copy(
                    update={"approved_at": datetime(2026, 8, 11, tzinfo=UTC)}
                )
            }
        )

    with pytest.raises(ValueError, match="unsealed|content hash mismatch"):
        RDidAdapter(profile(), artifact)


def test_derived_report_marks_unsupported_group_time_data_without_claiming_success() -> (
    None
):
    """Missing cohorts are a diagnostic limitation, never a reproduction pass."""
    report = parse_derived_report(
        {
            "schema_version": "derived-did-event-study-v1",
            "treatment_timing": {"first_treated_period": 2012},
            "support": {"group_time_supported": False},
            "balance": {"units": 2},
            "event_time": {"reference_period": -1},
            "twfe_event_study": {"estimates": []},
            "callaway_santanna": {
                "status": "unsupported",
                "reason": "no cohorts",
            },
            "configuration": {"outcome_column": "emissions"},
        }
    )

    assert report.callaway_santanna.status == "unsupported"
    assert report.reproduction_result is None


def test_derived_report_rejects_reproduction_pass_fields() -> None:
    """A derived diagnostic schema may not make author-reproduction claims."""
    with pytest.raises(ValueError, match="reproduction"):
        parse_derived_report(
            {
                "schema_version": "derived-did-event-study-v1",
                "treatment_timing": {},
                "support": {},
                "balance": {},
                "event_time": {},
                "twfe_event_study": {"estimates": []},
                "callaway_santanna": {"status": "completed", "estimates": []},
                "configuration": {},
                "reproduction_pass": True,
            }
        )


def test_completed_callaway_santanna_requires_estimates_intervals_and_config() -> None:
    """Completed group-time ATT diagnostics carry usable numerical evidence."""
    report = parse_derived_report(
        {
            "schema_version": "derived-did-event-study-v1",
            "treatment_timing": {},
            "support": {},
            "balance": {},
            "event_time": {},
            "twfe_event_study": {"estimates": []},
            "callaway_santanna": {
                "status": "completed",
                "estimates": [{"group": 2012, "time": 2013, "estimate": 0.1}],
                "confidence_intervals": [
                    {"group": 2012, "time": 2013, "lower": 0.0, "upper": 0.2}
                ],
                "configuration": {
                    "estimator": "did::att_gt",
                    "control_group": "nevertreated",
                },
            },
            "configuration": {},
        }
    )

    assert report.callaway_santanna.estimates[0].estimate == 0.1


@pytest.mark.parametrize(
    "field, value",
    [
        ("estimate", float("nan")),
        ("lower", float("inf")),
        ("upper", -1.0),
    ],
)
def test_completed_callaway_santanna_rejects_invalid_finite_intervals(
    field: str, value: float
) -> None:
    """Completed group-time ATT must carry finite, non-inverted typed evidence."""
    estimate: dict[str, object] = {"group": 2012, "time": 2013, "estimate": 0.1}
    interval: dict[str, object] = {
        "group": 2012,
        "time": 2013,
        "lower": 0.0,
        "upper": 0.2,
    }
    if field == "estimate":
        estimate[field] = value
    else:
        interval[field] = value

    with pytest.raises(ValueError):
        parse_derived_report(
            {
                "schema_version": "derived-did-event-study-v1",
                "treatment_timing": {},
                "support": {},
                "balance": {},
                "event_time": {},
                "twfe_event_study": {"estimates": []},
                "callaway_santanna": {
                    "status": "completed",
                    "estimates": [estimate],
                    "confidence_intervals": [interval],
                    "configuration": {
                        "estimator": "did::att_gt",
                        "control_group": "nevertreated",
                    },
                },
                "configuration": {},
            }
        )


@pytest.mark.parametrize("configuration", [None, {}])
def test_completed_callaway_santanna_rejects_missing_estimator_configuration(
    configuration: object,
) -> None:
    """Completed output cannot replace fixed estimator keys with null or empty data."""
    with pytest.raises(ValueError):
        parse_derived_report(
            {
                "schema_version": "derived-did-event-study-v1",
                "treatment_timing": {},
                "support": {},
                "balance": {},
                "event_time": {},
                "twfe_event_study": {"estimates": []},
                "callaway_santanna": {
                    "status": "completed",
                    "estimates": [{"group": 2012, "time": 2013, "estimate": 0.1}],
                    "confidence_intervals": [
                        {"group": 2012, "time": 2013, "lower": 0.0, "upper": 0.2}
                    ],
                    "configuration": configuration,
                },
                "configuration": {},
            }
        )


@pytest.mark.parametrize("duplicate_field", ["estimates", "confidence_intervals"])
def test_completed_callaway_santanna_rejects_duplicate_group_time_evidence(
    duplicate_field: str,
) -> None:
    """One completed ATT may not duplicate an otherwise identical group-time key."""
    estimate = {"group": 2012, "time": 2013, "estimate": 0.1}
    interval = {"group": 2012, "time": 2013, "lower": 0.0, "upper": 0.2}
    estimates = [estimate]
    confidence_intervals = [interval]
    if duplicate_field == "estimates":
        estimates.append(dict(estimate))
    else:
        confidence_intervals.append(dict(interval))

    with pytest.raises(ValueError, match="duplicate|align"):
        parse_derived_report(
            {
                "schema_version": "derived-did-event-study-v1",
                "treatment_timing": {},
                "support": {},
                "balance": {},
                "event_time": {},
                "twfe_event_study": {"estimates": []},
                "callaway_santanna": {
                    "status": "completed",
                    "estimates": estimates,
                    "confidence_intervals": confidence_intervals,
                    "configuration": {
                        "estimator": "did::att_gt",
                        "control_group": "nevertreated",
                    },
                },
                "configuration": {},
            }
        )


def test_expected_result_fixtures_are_validated() -> None:
    """Both fixture namespaces remain parseable and cannot imply a reproduction pass."""
    fixture_root = Path(__file__).parents[1] / "fixtures/replication/tiny-did-expected"
    author = json.loads((fixture_root / "author-results.json").read_text())
    derived = json.loads((fixture_root / "derived-results.json").read_text())
    derived_completed = json.loads(
        (fixture_root / "derived-completed-results.json").read_text()
    )

    for path, declaration in author.items():
        Tier2ExpectedOutput(path=path, **declaration)
    assert parse_derived_report(derived).reproduction_result is None
    assert (
        parse_derived_report(derived_completed).callaway_santanna.status == "completed"
    )
