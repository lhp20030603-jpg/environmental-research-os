"""Final authority-window regression for the V0.3.1 transition."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from envresearch.econometrics.valuation_transition import (
    TRANSITION_SUBJECT,
    V031ExitHarness,
)
from envresearch.models.artifact import ArtifactRef


def _ref(name: str, digest: str) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=name,
        artifact_version=1,
        content_hash=digest * 64,
    )


def test_require_current_reauthenticates_after_exact_chain_check() -> None:
    marker_ref = _ref("valuation-transition-v031", "a")
    run_ref = _ref("valuation-run", "b")
    manifest_ref = _ref("valuation-manifest", "c")
    report_ref = _ref("valuation-report", "d")
    binding_ref = _ref("valuation-binding", "e")
    marker = SimpleNamespace(
        run_ref=run_ref,
        manifest_ref=manifest_ref,
        report_ref=report_ref,
        catalog_binding_ref=binding_ref,
    )
    authority = {"valid": True}

    class Runner:
        def load(self, reference: ArtifactRef, _model: Any) -> Any:
            if reference == run_ref:
                return SimpleNamespace(manifest_ref=manifest_ref)
            return SimpleNamespace(manifest_id="registered-manifest")

        def current(self, _subject: str) -> ArtifactRef:
            return run_ref

    class Evaluator:
        def load(self, _reference: ArtifactRef, _model: Any) -> Any:
            return marker

        def current(self, subject: str) -> ArtifactRef:
            if subject == TRANSITION_SUBJECT:
                return marker_ref
            if subject.startswith("valuation-report-"):
                return report_ref
            authority["valid"] = False
            return binding_ref

    def authenticate() -> None:
        if not authority["valid"]:
            raise ValueError("V0.3.1 reviewed runtime identity changed")

    harness = V031ExitHarness.__new__(V031ExitHarness)
    harness.marker_ref = marker_ref
    harness.marker = marker  # type: ignore[assignment]
    harness.runner = Runner()  # type: ignore[assignment]
    harness.evaluator = Evaluator()  # type: ignore[assignment]
    harness._authenticate_runtime_and_pack = authenticate  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="runtime identity changed"):
        harness._require_current()


def test_open_exact_rejects_transition_superseded_during_chain_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker_ref = _ref("valuation-transition-v031", "a")
    replacement_ref = _ref("valuation-transition-v031", "f")
    run_ref = _ref("valuation-run", "b")
    manifest_ref = _ref("valuation-manifest", "c")
    report_ref = _ref("valuation-report", "d")
    binding_ref = _ref("valuation-binding", "e")
    marker = SimpleNamespace(
        run_ref=run_ref,
        manifest_ref=manifest_ref,
        report_ref=report_ref,
        catalog_binding_ref=binding_ref,
    )
    state = {"transition_ref": marker_ref}

    class Runner:
        def load(self, reference: ArtifactRef, _model: Any) -> Any:
            if reference == run_ref:
                return SimpleNamespace(manifest_ref=manifest_ref)
            return SimpleNamespace(manifest_id="registered-manifest")

        def current(self, _subject: str) -> ArtifactRef:
            return run_ref

    class Evaluator:
        def load(self, _reference: ArtifactRef, _model: Any) -> Any:
            return marker

        def current(self, subject: str) -> ArtifactRef:
            if subject == TRANSITION_SUBJECT:
                return state["transition_ref"]
            if subject.startswith("valuation-report-"):
                return report_ref
            state["transition_ref"] = replacement_ref
            return binding_ref

    harness = V031ExitHarness.__new__(V031ExitHarness)
    harness.marker_ref = marker_ref
    harness.marker = marker  # type: ignore[assignment]
    harness.runner = Runner()  # type: ignore[assignment]
    harness.evaluator = Evaluator()  # type: ignore[assignment]
    harness._authenticate_runtime_and_pack = lambda: None  # type: ignore[method-assign]

    def candidate(
        _cls: type[V031ExitHarness], _run_root: Path, requested: ArtifactRef
    ) -> V031ExitHarness:
        assert requested == marker_ref
        return harness

    monkeypatch.setattr(V031ExitHarness, "_candidate", classmethod(candidate))

    with pytest.raises(ValueError, match="marker is not current"):
        V031ExitHarness.open_exact(Path("/unused"), marker_ref)
