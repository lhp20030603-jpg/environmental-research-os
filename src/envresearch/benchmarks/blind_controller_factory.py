"""Pinned construction of one blind evaluation controller."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from envresearch.benchmarks.blind_authority import canonical_json
from envresearch.benchmarks.blind_registry import BlindBenchmarkRegistry

if TYPE_CHECKING:
    from envresearch.benchmarks.blind_workflow import BlindEvaluationController

ControllerT = TypeVar("ControllerT", bound="BlindEvaluationController")


def controller_from_case(
    controller_type: type[ControllerT], case_root: Path, run_root: Path
) -> ControllerT:
    cases = BlindBenchmarkRegistry.discover(case_root)
    if len(cases) != 1:
        raise ValueError("blind controller requires exactly one case")
    manifest = next(iter(cases.values()))
    loaded = BlindBenchmarkRegistry.load_case(manifest)
    case = case_root.resolve(strict=True)
    run = run_root.resolve(strict=False)
    if run == case or run.is_relative_to(case) or case.is_relative_to(run):
        raise ValueError("case and run roots must be separate")
    controller = controller_type(loaded, run)
    controller._descriptor_sha256 = hashlib.sha256(
        canonical_json(manifest.model_dump(mode="json"))
    ).hexdigest()
    return controller
