"""Fresh registry loading for pre-authorized citation catalogs."""

from __future__ import annotations

from pathlib import Path

from envresearch.benchmarks.blind_registry import (
    BlindBenchmarkRegistry,
    LoadedBlindCase,
)


def canonical_catalog_roots(case_roots: tuple[Path, ...]) -> tuple[Path, ...]:
    """Resolve a nonempty unique catalog-root identity."""
    if not case_roots:
        raise ValueError("citation registry roots must be nonempty")
    roots = tuple(sorted((root.resolve(strict=True) for root in case_roots), key=str))
    if len(roots) != len(set(roots)):
        raise ValueError("citation registry roots must be unique")
    return roots


def load_registry_cases(
    case_roots: tuple[Path, ...],
) -> tuple[tuple[LoadedBlindCase, ...], tuple[str, ...]]:
    """Fresh-discover every case beneath the exact authorized roots."""
    canonical_roots = canonical_catalog_roots(case_roots)
    roots = tuple(str(root) for root in canonical_roots)
    loaded: dict[str, LoadedBlindCase] = {}
    for root in roots:
        for case_id, manifest in BlindBenchmarkRegistry.discover(Path(root)).items():
            if case_id in loaded:
                raise ValueError("citation registry case IDs must be unique")
            loaded[case_id] = BlindBenchmarkRegistry.load_case(manifest)
    if not loaded:
        raise ValueError("citation registry coverage must be nonempty")
    return tuple(loaded[key] for key in sorted(loaded)), roots
