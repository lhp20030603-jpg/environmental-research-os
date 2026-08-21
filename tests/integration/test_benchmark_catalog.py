"""Integration contract for the environmental benchmark seed catalog."""

from pathlib import Path

from envresearch.benchmarks.registry import BenchmarkRegistry

EXPECTED_PROVENANCE = {
    "clean-identification": {
        "doi": "10.3886/E192280V1",
        "method_family": "difference_in_differences",
        "source_url": "https://www.openicpsr.org/openicpsr/project/192280/view",
        "source_version": "V1",
        "title": (
            "Clean Identification? The Effects of the Clean Air Act on Air "
            "Pollution, Exposure Disparities and House Prices"
        ),
    },
    "energy-efficient-stoves-rct": {
        "doi": "10.3886/E166661V1",
        "method_family": "randomized_controlled_trial",
        "source_url": (
            "https://www.openicpsr.org/openicpsr/project/166661/version/V1/view"
        ),
        "source_version": "V1",
        "title": (
            "Credit, attention, and externalities in the adoption of energy "
            "efficient technologies by low-income household"
        ),
    },
    "flood-buyout-hedonic": {
        "doi": "10.3886/E189021V1",
        "method_family": "hedonic_valuation",
        "source_url": (
            "https://www.openicpsr.org/openicpsr/project/189021/version/V1/view"
        ),
        "source_version": "V1",
        "title": "Racial Gaps in Federal Flood Buyout Compensations",
    },
}


def test_catalog_spans_three_environmental_method_families() -> None:
    """Seed entries are private, metadata-only references across three methods."""
    catalog = BenchmarkRegistry.discover(Path("benchmarks/catalog"))

    assert set(catalog) == set(EXPECTED_PROVENANCE)
    assert {
        item.method_family for item in catalog.values()
    } == {
        "difference_in_differences",
        "randomized_controlled_trial",
        "hedonic_valuation",
    }
    assert not any(item.public for item in catalog.values())
    assert all(item.source_version for item in catalog.values())
    assert all(item.source_archive is None for item in catalog.values())
    assert all(item.source_sha256 is None for item in catalog.values())
    assert all(item.license_name is None for item in catalog.values())
    assert all(item.license_url is None for item in catalog.values())
    assert all(not item.commands for item in catalog.values())
    assert all(not item.expected_outputs for item in catalog.values())


def test_catalog_records_verified_official_provenance() -> None:
    """A swapped DOI, title, source, or method must fail independently."""
    catalog = BenchmarkRegistry.discover(Path("benchmarks/catalog"))

    for benchmark_id, expected in EXPECTED_PROVENANCE.items():
        manifest = catalog[benchmark_id]
        assert manifest.title == expected["title"]
        assert manifest.method_family == expected["method_family"]
        assert manifest.doi == expected["doi"]
        assert str(manifest.source_url) == expected["source_url"]
        assert manifest.source_version == expected["source_version"]
