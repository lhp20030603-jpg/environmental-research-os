"""Tests for strict, planning-only methodology profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from envresearch.methods.models import MethodProfile
from envresearch.methods.registry import MethodProfileRegistry

BUILTIN_ROOT = Path("packs/methods")
EXPECTED_MATRIX = {
    "rct": (
        {"individual", "cluster_panel"},
        {"randomized_assignment"},
    ),
    "did-event-study": (
        {"panel", "repeated_cross_section"},
        {"treatment_timing_variation", "untreated_comparison"},
    ),
    "rdd": (
        {"cross_section", "panel"},
        {"known_cutoff", "running_variable"},
    ),
    "iv": (
        {"cross_section", "panel"},
        {"excluded_instrument"},
    ),
    "synthetic-control": (
        {"panel"},
        {"one_or_few_treated_units", "donor_pool", "pre_treatment_periods"},
    ),
    "hedonic": (
        {"cross_section", "repeated_cross_section", "panel"},
        {"market_prices", "environmental_attribute"},
    ),
    "spatiotemporal": (
        {"spatial", "panel", "raster"},
        {"georeferenced_measurement"},
    ),
    "meta-analysis": (
        {"study_level"},
        {"comparable_effect_sizes", "documented_search"},
    ),
}


def _valid_profile(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "profile_id": "example-method",
        "version": "0.2.0",
        "family": "example_method",
        "compatible_estimands": ["causal"],
        "required_data_structures": ["panel"],
        "required_features": ["untreated_comparison"],
        "identifying_assumptions": ["First assumption", "Second assumption"],
        "incompatibility_rules": ["No defensible comparison group is available"],
        "mandatory_diagnostics": ["First diagnostic", "Second diagnostic"],
        "falsification_checks": ["A negative-control outcome check"],
        "fallback_profiles": [],
        "analysis_plan_fields": ["Treatment definition"],
        "methodological_references": ["DOI:10.1016/j.jeconom.2020.12.001"],
        "estimator_entrypoint": None,
    }
    payload.update(overrides)
    return payload


def _write_method_pack(
    root: Path,
    pack_id: str,
    *,
    profile: dict[str, Any] | None = None,
    manifest_overrides: dict[str, Any] | None = None,
) -> Path:
    pack_dir = root / pack_id
    pack_dir.mkdir(parents=True)
    manifest: dict[str, Any] = {
        "id": pack_id,
        "kind": "method",
        "version": "0.2.0",
        "kernel": ">=0.2,<0.3",
        "schema": ">=1,<2",
        "entrypoint": "profile.yaml",
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)
    (pack_dir / "pack.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    if profile is None:
        profile = _valid_profile(profile_id=pack_id)
    (pack_dir / "profile.yaml").write_text(
        yaml.safe_dump(profile, sort_keys=False), encoding="utf-8"
    )
    return pack_dir


def test_registry_loads_exactly_eight_builtin_planning_profiles() -> None:
    registry = MethodProfileRegistry.discover(BUILTIN_ROOT)

    assert set(registry.profiles) == set(EXPECTED_MATRIX)
    assert list(registry.profiles) == sorted(EXPECTED_MATRIX)
    assert all(
        profile.estimator_entrypoint is None
        for profile in registry.profiles.values()
    )


@pytest.mark.parametrize(
    ("profile_id", "data_structure", "features"),
    [
        ("rct", "individual", frozenset({"randomized_assignment"})),
        (
            "did-event-study",
            "panel",
            frozenset({"treatment_timing_variation", "untreated_comparison"}),
        ),
        (
            "rdd",
            "cross_section",
            frozenset({"known_cutoff", "running_variable"}),
        ),
        ("iv", "panel", frozenset({"excluded_instrument"})),
        (
            "synthetic-control",
            "panel",
            frozenset(
                {"one_or_few_treated_units", "donor_pool", "pre_treatment_periods"}
            ),
        ),
        (
            "hedonic",
            "repeated_cross_section",
            frozenset({"market_prices", "environmental_attribute"}),
        ),
        (
            "spatiotemporal",
            "raster",
            frozenset({"georeferenced_measurement"}),
        ),
        (
            "meta-analysis",
            "study_level",
            frozenset({"comparable_effect_sizes", "documented_search"}),
        ),
    ],
)
def test_each_builtin_matches_its_complete_required_feature_conjunction(
    profile_id: str,
    data_structure: str,
    features: frozenset[str],
) -> None:
    profile = MethodProfileRegistry.discover(BUILTIN_ROOT).profiles[profile_id]

    assert profile.is_compatible("causal", data_structure, features)


@pytest.mark.parametrize("profile_id", sorted(EXPECTED_MATRIX))
def test_each_builtin_rejects_every_individually_missing_required_feature(
    profile_id: str,
) -> None:
    profile = MethodProfileRegistry.discover(BUILTIN_ROOT).profiles[profile_id]
    data_structures, required_features = EXPECTED_MATRIX[profile_id]

    for missing_feature in required_features:
        available = frozenset(required_features - {missing_feature})
        assert not profile.is_compatible(
            "causal", min(data_structures), available
        ), missing_feature


@pytest.mark.parametrize("profile_id", sorted(EXPECTED_MATRIX))
def test_each_builtin_rejects_wrong_data_structure(profile_id: str) -> None:
    profile = MethodProfileRegistry.discover(BUILTIN_ROOT).profiles[profile_id]
    _, required_features = EXPECTED_MATRIX[profile_id]

    assert not profile.is_compatible(
        "causal", "time_series", frozenset(required_features)
    )


def test_causal_only_profile_rejects_descriptive_estimand() -> None:
    profile = MethodProfileRegistry.discover(BUILTIN_ROOT).profiles["rct"]

    assert not profile.is_compatible(
        "descriptive", "individual", frozenset({"randomized_assignment"})
    )


def test_registry_compatible_preserves_profile_id_order_and_and_semantics() -> None:
    registry = MethodProfileRegistry.discover(BUILTIN_ROOT)

    matches = registry.compatible(
        "causal",
        "panel",
        frozenset(
            {
                "environmental_attribute",
                "excluded_instrument",
                "market_prices",
                "untreated_comparison",
                "treatment_timing_variation",
            }
        ),
    )

    assert tuple(profile.profile_id for profile in matches) == (
        "did-event-study",
        "hedonic",
        "iv",
    )


def test_builtin_matrix_and_planning_content_are_complete() -> None:
    registry = MethodProfileRegistry.discover(BUILTIN_ROOT)

    for profile_id, profile in registry.profiles.items():
        expected_structures, expected_features = EXPECTED_MATRIX[profile_id]
        assert profile.required_data_structures == frozenset(expected_structures)
        assert profile.required_features == frozenset(expected_features)
        assert len(profile.identifying_assumptions) >= 2
        assert len(profile.mandatory_diagnostics) >= 2
        assert profile.falsification_checks
        assert profile.analysis_plan_fields
        assert profile.methodological_references


def test_builtin_manifests_are_planning_only_and_versioned_for_v02() -> None:
    for path in sorted(BUILTIN_ROOT.glob("*/pack.yaml")):
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert manifest == {
            "id": path.parent.name,
            "kind": "method",
            "version": "0.2.0",
            "kernel": ">=0.2,<0.3",
            "schema": ">=1,<2",
            "entrypoint": "profile.yaml",
        }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("profile_id", "../unsafe"),
        ("version", "0.2"),
        ("family", "Not Canonical"),
        ("compatible_estimands", ["unknown"]),
        ("required_data_structures", ["Panel"]),
        ("required_features", ["feature with spaces"]),
    ],
)
def test_profile_rejects_unsafe_identifiers_and_enum_like_values(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError, match=field):
        MethodProfile.model_validate(_valid_profile(**{field: value}))


@pytest.mark.parametrize(
    "field",
    [
        "compatible_estimands",
        "required_data_structures",
        "required_features",
        "identifying_assumptions",
        "incompatibility_rules",
        "mandatory_diagnostics",
        "falsification_checks",
        "fallback_profiles",
        "analysis_plan_fields",
        "methodological_references",
    ],
)
def test_profile_rejects_blank_and_duplicate_list_values(field: str) -> None:
    blank = _valid_profile()
    blank[field] = [" "]
    duplicate = _valid_profile()
    duplicate[field] = ["same", "same"]

    with pytest.raises(ValidationError, match="blank"):
        MethodProfile.model_validate(blank)
    with pytest.raises(ValidationError, match="duplicate"):
        MethodProfile.model_validate(duplicate)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("profile_id", 12),
        ("version", 0.2),
        ("compatible_estimands", "causal"),
        ("required_features", 1),
        ("identifying_assumptions", "one assumption"),
    ],
)
def test_profile_rejects_scalar_coercion(field: str, value: object) -> None:
    with pytest.raises(ValidationError, match=field):
        MethodProfile.model_validate(_valid_profile(**{field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("identifying_assumptions", ["Only one"]),
        ("mandatory_diagnostics", ["Only one"]),
        ("falsification_checks", []),
        ("analysis_plan_fields", []),
        ("methodological_references", []),
    ],
)
def test_profile_requires_complete_planning_content(
    field: str, value: list[str]
) -> None:
    with pytest.raises(ValidationError, match=field):
        MethodProfile.model_validate(_valid_profile(**{field: value}))


def test_profile_requires_null_estimator_and_forbids_execution_fields() -> None:
    with pytest.raises(ValidationError, match="estimator_entrypoint"):
        MethodProfile.model_validate(
            _valid_profile(estimator_entrypoint="estimators.did:fit")
        )
    with pytest.raises(ValidationError, match="commands"):
        MethodProfile.model_validate(_valid_profile(commands=["python run.py"]))


def test_profile_rejects_unstable_methodological_reference() -> None:
    with pytest.raises(ValidationError, match="methodological_references"):
        MethodProfile.model_validate(
            _valid_profile(methodological_references=["Some paper I remember"])
        )


def test_profile_is_frozen_and_serializes_immutable_containers_as_json_arrays() -> None:
    profile = MethodProfile.model_validate(_valid_profile())

    with pytest.raises(ValidationError, match="frozen"):
        profile.version = "0.3.0"  # type: ignore[misc]
    dumped = profile.model_dump(mode="json")
    assert isinstance(dumped["required_features"], list)
    assert isinstance(dumped["identifying_assumptions"], list)
    assert MethodProfile.model_validate(dumped) == profile


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("compatible_estimands", [["causal"]]),
        ("compatible_estimands", [True]),
        ("required_data_structures", [{"nested": "panel"}]),
        ("required_data_structures", [1]),
        ("required_features", [["untreated_comparison"]]),
        ("required_features", [False]),
        ("analysis_plan_fields", [{"nested": "field"}]),
        ("analysis_plan_fields", [2]),
    ],
)
def test_profile_wraps_non_string_collection_values_as_validation_errors(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError, match=field):
        MethodProfile.model_validate(_valid_profile(**{field: value}))


@pytest.mark.parametrize(
    ("estimand_type", "data_structure", "features"),
    [
        (1, "panel", frozenset()),
        ("causal", 1, frozenset()),
        ("causal", "panel", {"untreated_comparison"}),
        ("causal", "panel", frozenset({1})),
        ("unknown", "panel", frozenset()),
        ("causal", "Panel", frozenset()),
    ],
)
def test_compatibility_input_validation_is_strict(
    estimand_type: object, data_structure: object, features: object
) -> None:
    profile = MethodProfile.model_validate(_valid_profile())

    with pytest.raises((TypeError, ValueError)):
        profile.is_compatible(estimand_type, data_structure, features)  # type: ignore[arg-type]


def test_registry_rejects_missing_or_invalid_entrypoint(tmp_path: Path) -> None:
    missing = _write_method_pack(tmp_path, "missing")
    (missing / "profile.yaml").unlink()

    with pytest.raises(ValueError, match="entrypoint"):
        MethodProfileRegistry.discover(tmp_path)

    bad_root = tmp_path / "bad"
    _write_method_pack(
        bad_root,
        "bad-entrypoint",
        manifest_overrides={"entrypoint": "module:run"},
    )
    with pytest.raises(ValueError, match="entrypoint"):
        MethodProfileRegistry.discover(bad_root)


@pytest.mark.parametrize(
    ("manifest_overrides", "profile_overrides", "match"),
    [
        ({"kind": "paper"}, {}, "kind"),
        ({"version": "0.2.1"}, {}, "version"),
        ({}, {"profile_id": "other"}, "profile_id"),
    ],
)
def test_registry_rejects_manifest_profile_inconsistency(
    tmp_path: Path,
    manifest_overrides: dict[str, Any],
    profile_overrides: dict[str, Any],
    match: str,
) -> None:
    profile = _valid_profile(**{"profile_id": "example", **profile_overrides})
    _write_method_pack(
        tmp_path,
        "example",
        profile=profile,
        manifest_overrides=manifest_overrides,
    )

    with pytest.raises(ValueError, match=match):
        MethodProfileRegistry.discover(tmp_path)


def test_registry_rejects_pack_id_that_differs_from_directory(tmp_path: Path) -> None:
    _write_method_pack(
        tmp_path,
        "directory-name",
        profile=_valid_profile(profile_id="manifest-name"),
        manifest_overrides={"id": "manifest-name"},
    )

    with pytest.raises(ValueError, match="directory"):
        MethodProfileRegistry.discover(tmp_path)


def test_registry_rejects_duplicate_profiles_via_pack_registry(tmp_path: Path) -> None:
    _write_method_pack(tmp_path / "a", "duplicate")
    _write_method_pack(tmp_path / "b", "duplicate")

    with pytest.raises(ValueError, match="duplicate pack id"):
        MethodProfileRegistry.discover(tmp_path)


def test_registry_rejects_unknown_fallback_profile(tmp_path: Path) -> None:
    _write_method_pack(
        tmp_path,
        "example",
        profile=_valid_profile(
            profile_id="example", fallback_profiles=["not-installed"]
        ),
    )

    with pytest.raises(ValueError, match="unknown fallback"):
        MethodProfileRegistry.discover(tmp_path)


def test_registry_rejects_malformed_profile_yaml(tmp_path: Path) -> None:
    pack_dir = _write_method_pack(tmp_path, "malformed")
    (pack_dir / "profile.yaml").write_text("profile_id: [unterminated", encoding="utf-8")

    with pytest.raises(ValueError, match="profile.yaml"):
        MethodProfileRegistry.discover(tmp_path)


@pytest.mark.parametrize(
    "duplicate_field",
    [
        "profile_id: duplicate-profile",
        "required_features: []",
        "estimator_entrypoint: null",
    ],
)
def test_registry_rejects_duplicate_top_level_profile_keys(
    tmp_path: Path, duplicate_field: str
) -> None:
    pack_dir = _write_method_pack(tmp_path, "duplicate-profile")
    profile_path = pack_dir / "profile.yaml"
    profile_path.write_text(
        f"{profile_path.read_text(encoding='utf-8')}\n{duplicate_field}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"(?s)profile\.yaml.*duplicate key"):
        MethodProfileRegistry.discover(tmp_path)


def test_registry_rejects_duplicate_nested_profile_keys(tmp_path: Path) -> None:
    pack_dir = _write_method_pack(tmp_path, "nested-duplicate")
    profile_path = pack_dir / "profile.yaml"
    profile_path.write_text(
        f"{profile_path.read_text(encoding='utf-8')}\n"
        "review_metadata:\n"
        "  risk: first\n"
        "  risk: second\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"(?s)profile\.yaml.*duplicate key"):
        MethodProfileRegistry.discover(tmp_path)


def test_registry_rejects_duplicate_manifest_keys_without_bypassing_pack_registry(
    tmp_path: Path,
) -> None:
    pack_dir = _write_method_pack(tmp_path, "duplicate-manifest")
    manifest_path = pack_dir / "pack.yaml"
    manifest_path.write_text(
        f"{manifest_path.read_text(encoding='utf-8')}\nentrypoint: profile.yaml\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"(?s)pack\.yaml.*duplicate key"):
        MethodProfileRegistry.discover(tmp_path)


def test_registry_normalizes_nested_collection_errors_with_profile_path(
    tmp_path: Path,
) -> None:
    _write_method_pack(
        tmp_path,
        "nested-feature",
        profile=_valid_profile(
            profile_id="nested-feature",
            required_features=[{"nested": "feature"}],
        ),
    )

    with pytest.raises(
        ValueError,
        match=r"(?s)profile\.yaml.*required_features.*strings only",
    ):
        MethodProfileRegistry.discover(tmp_path)


def test_registry_rejects_profile_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside.yaml"
    outside.write_text(
        yaml.safe_dump(_valid_profile(profile_id="escaped")), encoding="utf-8"
    )
    pack_dir = _write_method_pack(tmp_path / "root", "escaped")
    (pack_dir / "profile.yaml").unlink()
    (pack_dir / "profile.yaml").symlink_to(outside)

    with pytest.raises(ValueError, match="symlink|escape"):
        MethodProfileRegistry.discover(tmp_path / "root")


def test_registry_rejects_manifest_symlink(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real_pack = _write_method_pack(real_dir, "linked")
    link_dir = tmp_path / "root" / "linked"
    link_dir.mkdir(parents=True)
    (link_dir / "pack.yaml").symlink_to(real_pack / "pack.yaml")
    (link_dir / "profile.yaml").write_text(
        yaml.safe_dump(_valid_profile(profile_id="linked")), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="symlink|escape"):
        MethodProfileRegistry.discover(tmp_path / "root")
