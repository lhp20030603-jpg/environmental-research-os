"""Tests for benchmark manifest validation and deterministic discovery."""

from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from envresearch.benchmarks.registry import BenchmarkRegistry
from envresearch.models.benchmark import BenchmarkManifest, CommandSpec, ExpectedOutput

VALID_MANIFEST: dict[str, object] = {
    "id": "synthetic-exact-file",
    "title": "Synthetic exact-file benchmark",
    "method_family": "fixture",
    "topic": "deterministic replay",
    "public": False,
    "source_url": "https://example.org/archive/v1",
    "source_version": None,
    "source_archive": None,
    "source_sha256": None,
    "doi": None,
    "license_name": None,
    "license_url": None,
    "commands": [{"argv": ["python", "run.py"]}],
    "expected_outputs": [
        {
            "path": "result.txt",
            "comparator": "exact",
            "expected_path": "expected/result.txt",
        }
    ],
}


def test_public_benchmark_requires_field_identifiable_metadata() -> None:
    """Public sources without provenance must identify every missing field."""
    payload = {**VALID_MANIFEST, "public": True}

    with pytest.raises(ValidationError) as captured:
        BenchmarkManifest.model_validate(payload)

    errors = captured.value.errors()
    assert {error["loc"] for error in errors} == {
        ("source_version",),
        ("source_archive",),
        ("source_sha256",),
        ("doi",),
        ("license_name",),
        ("license_url",),
    }
    assert {
        error["msg"] for error in errors
    } == {"Value error, public benchmark requires DOI and license metadata"}


def test_command_spec_rejects_empty_argv() -> None:
    """A runner cannot safely invoke a command without an executable."""
    with pytest.raises(ValidationError, match="at least 1 item"):
        CommandSpec(argv=[])


@pytest.mark.parametrize("name", ["DECLARED_VALUE", "api_key", "Db_PaSsWoRd"])
def test_command_spec_default_denies_non_deterministic_environment_names(
    name: str,
) -> None:
    """A manifest cannot declare arbitrary or secret-shaped process variables."""
    secret = "not-for-validation-output"

    with pytest.raises(ValidationError, match="is not allowed") as raised:
        CommandSpec(argv=["python", "run.py"], env={name: secret})

    assert secret not in str(raised.value)
    assert secret not in str(raised.value.errors())
    assert secret not in raised.value.json()


@pytest.mark.parametrize("cwd", ["/tmp", "../outside", "work/../../outside"])
def test_command_spec_rejects_unsafe_relative_cwd(cwd: str) -> None:
    """A manifest command must not select a directory outside its case."""
    with pytest.raises(ValidationError, match="safe relative path"):
        CommandSpec(argv=["python", "run.py"], cwd=cast(Path, cwd))


@pytest.mark.parametrize("source_archive", ["/tmp/package.zip", "raw/../package.zip"])
def test_manifest_rejects_unsafe_source_archive(source_archive: str) -> None:
    """Source archives must remain inside the immutable case raw directory."""
    with pytest.raises(ValidationError, match="safe relative path"):
        BenchmarkManifest.model_validate(
            {**VALID_MANIFEST, "source_archive": source_archive}
        )


def test_manifest_rejects_source_archive_outside_raw_directory() -> None:
    """A relative archive outside raw would not be protected as source input."""
    with pytest.raises(ValidationError, match="raw directory"):
        BenchmarkManifest.model_validate(
            {**VALID_MANIFEST, "source_archive": "inputs/package.zip"}
        )


@pytest.mark.parametrize("path", ["../result.txt", "reports/../../result.txt"])
def test_expected_output_rejects_unsafe_actual_output_path(path: str) -> None:
    """Comparators must not read actual output paths outside the run workspace."""
    with pytest.raises(ValidationError, match="safe relative path"):
        ExpectedOutput(
            path=cast(Path, path),
            comparator="exact",
            expected_path=Path("result.txt"),
        )


@pytest.mark.parametrize("expected_path", ["/tmp/result.txt", "../result.txt"])
def test_expected_output_rejects_unsafe_expected_output_path(
    expected_path: str,
) -> None:
    """Comparators must not read expected files outside the manifest root."""
    with pytest.raises(ValidationError, match="safe relative path"):
        ExpectedOutput(
            path=Path("result.txt"),
            comparator="exact",
            expected_path=cast(Path, expected_path),
        )


@pytest.mark.parametrize("value", ["0" * 63, "A" * 64, "g" * 64])
def test_manifest_rejects_noncanonical_sha256(value: str) -> None:
    """Source integrity hashes must be canonical lowercase SHA-256 values."""
    with pytest.raises(ValidationError, match="64-character lowercase SHA-256"):
        BenchmarkManifest.model_validate({**VALID_MANIFEST, "source_sha256": value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("absolute_tolerance", -0.001), ("relative_tolerance", -0.001)],
)
def test_expected_output_rejects_negative_tolerance(
    field_name: str, value: float
) -> None:
    """Negative tolerances would invert comparison semantics."""
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        ExpectedOutput(
            path=Path("result.txt"),
            comparator="exact",
            expected_path=Path("expected/result.txt"),
            **{field_name: value},
        )


def test_models_forbid_unknown_fields() -> None:
    """Manifest typos must not silently change a benchmark contract."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        BenchmarkManifest.model_validate({**VALID_MANIFEST, "unknown": "value"})


def test_registry_rejects_duplicate_ids(tmp_path: Path) -> None:
    """Duplicate IDs would make a selected benchmark ambiguous."""
    for directory_name in ("a", "b"):
        directory = tmp_path / directory_name
        directory.mkdir()
        (directory / "benchmark.yaml").write_text(
            _manifest_yaml("duplicate"), encoding="utf-8"
        )

    with pytest.raises(ValueError, match="duplicate benchmark id: duplicate"):
        BenchmarkRegistry.discover(tmp_path)


def test_registry_discovers_manifests_in_path_order(tmp_path: Path) -> None:
    """Stable discovery order makes catalog output reproducible."""
    for directory_name, benchmark_id in (("z-last", "z"), ("a-first", "a")):
        directory = tmp_path / directory_name
        directory.mkdir()
        (directory / "benchmark.yaml").write_text(
            _manifest_yaml(benchmark_id), encoding="utf-8"
        )

    catalog = BenchmarkRegistry.discover(tmp_path)

    assert list(catalog) == ["a", "z"]


def test_registry_discovers_direct_catalog_yaml_in_path_order(
    tmp_path: Path,
) -> None:
    """Flat catalog manifests must not require one directory per benchmark."""
    for file_name, benchmark_id in (("z-last.yaml", "z"), ("a-first.yaml", "a")):
        (tmp_path / file_name).write_text(
            _manifest_yaml(benchmark_id), encoding="utf-8"
        )

    catalog = BenchmarkRegistry.discover(tmp_path)

    assert list(catalog) == ["a", "z"]


def test_registry_rejects_duplicate_ids_across_flat_and_nested_manifests(
    tmp_path: Path,
) -> None:
    """A flat catalog entry cannot silently shadow a benchmark package."""
    (tmp_path / "catalog.yaml").write_text(
        _manifest_yaml("duplicate"), encoding="utf-8"
    )
    package = tmp_path / "package"
    package.mkdir()
    (package / "benchmark.yaml").write_text(
        _manifest_yaml("duplicate"), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="duplicate benchmark id: duplicate"):
        BenchmarkRegistry.discover(tmp_path)


def test_registry_ignores_non_manifest_yaml_below_catalog_root(
    tmp_path: Path,
) -> None:
    """Recursive discovery must not ingest unrelated nested YAML files."""
    nested = tmp_path / "notes"
    nested.mkdir()
    (nested / "metadata.yaml").write_text("owner: researcher\n", encoding="utf-8")

    assert BenchmarkRegistry.discover(tmp_path) == {}


def test_exact_file_fixture_is_a_valid_manifest() -> None:
    """The checked-in exact comparator fixture remains a runnable manifest."""
    fixture_root = Path("benchmarks/fixtures/exact-file")

    catalog = BenchmarkRegistry.discover(fixture_root)

    assert set(catalog) == {"synthetic-exact-file"}
    manifest = catalog["synthetic-exact-file"]
    assert manifest.expected_outputs[0].comparator == "exact"
    assert all(
        (fixture_root / output.expected_path).is_file()
        for output in manifest.expected_outputs
    )


def _manifest_yaml(benchmark_id: str) -> str:
    """Return a complete minimal YAML manifest with a caller-supplied ID."""
    return f"""\
id: {benchmark_id}
title: Synthetic exact-file benchmark
method_family: fixture
topic: deterministic replay
public: false
source_url: https://example.org/archive/v1
commands:
  - argv: [python, run.py]
expected_outputs: []
"""
