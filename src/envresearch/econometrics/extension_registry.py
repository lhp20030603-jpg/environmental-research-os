"""Frozen post-V0.3.1 econometrics capability boundary."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict

STRICT = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExtensionGate(BaseModel):
    """One named extension seam that remains deliberately non-executable."""

    model_config = STRICT
    extension_id: Literal["spatial", "exposure", "forecasting-wave3", "stata"]
    status: Literal["capability-gated"] = "capability-gated"
    method_ids: tuple[str, ...]


class FrozenExtensionRegistry(BaseModel):
    """Closed executable set plus explicit non-executable future seams."""

    model_config = STRICT
    is_frozen: Literal[True] = True
    executable_methods: frozenset[str]
    gates: tuple[ExtensionGate, ...]

    @property
    def reserved_extensions(self) -> Mapping[str, ExtensionGate]:
        """Expose immutable extension metadata without registering recipes."""
        return MappingProxyType({item.extension_id: item for item in self.gates})

    def can_execute(self, method_id: str) -> bool:
        """Return true only for a method frozen into the reviewed recipe set."""
        return method_id in self.executable_methods


FROZEN_EXTENSION_REGISTRY = FrozenExtensionRegistry(
    executable_methods=frozenset(
        {
            "did-event-study",
            "panel-fe",
            "iv-2sls",
            "rdd-local-linear",
            "rct-itt",
            "environmental-measurement",
            "synthetic-control",
            "meta-analysis",
            "hedonic-pricing",
            "travel-cost",
            "contingent-valuation",
            "dce-clogit",
        }
    ),
    gates=(
        ExtensionGate(
            extension_id="spatial",
            method_ids=("spatial-lag", "spatial-error"),
        ),
        ExtensionGate(
            extension_id="exposure",
            method_ids=("exposure-assignment",),
        ),
        ExtensionGate(
            extension_id="forecasting-wave3",
            method_ids=("environmental-forecasting", "wave3-structural"),
        ),
        ExtensionGate(extension_id="stata", method_ids=("stata-adapter",)),
    ),
)
