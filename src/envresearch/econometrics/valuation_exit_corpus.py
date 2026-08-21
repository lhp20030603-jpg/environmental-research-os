"""Freeze checked Valuation Core fixtures into separated exact registries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from envresearch.econometrics.exit_corpus import _directory, _read_object, _read_regular
from envresearch.econometrics.exit_registry import ExitRegistry, validate_separate_roots
from envresearch.econometrics.valuation_authority import valuation_authority_lease
from envresearch.econometrics.valuation_exit_models import (
    ValuationCaseExpectation,
    ValuationExitCase,
    ValuationExitCaseInput,
    ValuationExitCatalogBinding,
    ValuationExitExpectationCatalog,
    ValuationExitManifest,
    ValuationFamily,
)
from envresearch.models.artifact import ArtifactRef

STRICT = ConfigDict(extra="forbid", frozen=True, strict=True)


class FrozenValuationExitCorpus(BaseModel):
    """Exact references published from one verified Valuation Core corpus."""

    model_config = STRICT
    manifest_ref: ArtifactRef
    catalog_ref: ArtifactRef


class _CaseDescriptor(BaseModel):
    model_config = STRICT
    case_id: str
    family: ValuationFamily
    role: Literal["green", "scientific-failure", "integrity-failure"]
    file: Path

    @field_validator("file")
    @classmethod
    def canonical_file(cls, value: Path) -> Path:
        if value.is_absolute() or value.suffix != ".yaml" or ".." in value.parts:
            raise ValueError(
                "valuation corpus case path must be canonical and relative"
            )
        return value


class _ManifestDescriptor(BaseModel):
    model_config = STRICT
    schema_version: Literal["econometrics.valuation-exit-corpus.v1"]
    manifest_id: str
    cases: tuple[_CaseDescriptor, ...]

    @model_validator(mode="after")
    def exact_cases(self) -> _ManifestDescriptor:
        if len(self.cases) != 9 or len({item.case_id for item in self.cases}) != 9:
            raise ValueError(
                "valuation corpus descriptor must contain nine unique cases"
            )
        return self


def freeze_valuation_exit_corpus(
    corpus_root: Path, runner: ExitRegistry, evaluator: ExitRegistry
) -> FrozenValuationExitCorpus:
    """Publish expectation-blind runner bytes and protected evaluator bytes."""
    with valuation_authority_lease(runner):
        return _freeze_valuation_exit_corpus(corpus_root, runner, evaluator)


def _freeze_valuation_exit_corpus(
    corpus_root: Path, runner: ExitRegistry, evaluator: ExitRegistry
) -> FrozenValuationExitCorpus:
    """Freeze the corpus while the caller owns the valuation authority lease."""
    root = _directory(corpus_root)
    validate_separate_roots(runner.root, evaluator.root)
    descriptor = _ManifestDescriptor.model_validate_json(
        json.dumps(_read_object(root, Path("manifest.yaml")))
    )
    catalog = ValuationExitExpectationCatalog(
        schema_version="econometrics.valuation-exit-expectations.v1",
        manifest_id=descriptor.manifest_id,
        cases=(
            *_expectations(root, Path("evaluator/expectations.json")),
            *_expectations(root, Path("evaluator/failures.json")),
        ),
    )
    catalog_ref = evaluator.publish(
        f"valuation-catalog-{descriptor.manifest_id}", catalog
    )
    manifest = ValuationExitManifest(
        schema_version="econometrics.valuation-exit-manifest.v1",
        manifest_id=descriptor.manifest_id,
        cases=tuple(_freeze_case(root, runner, item) for item in descriptor.cases),
    )
    manifest_ref = runner.publish(
        f"valuation-manifest-{descriptor.manifest_id}", manifest
    )
    binding = ValuationExitCatalogBinding(
        schema_version="econometrics.valuation-exit-catalog-binding.v1",
        manifest_ref=manifest_ref,
        catalog_ref=catalog_ref,
    )
    binding_ref = evaluator.publish(
        f"valuation-catalog-binding-{descriptor.manifest_id}", binding
    )
    evaluator.set_current(f"valuation-catalog-{descriptor.manifest_id}", binding_ref)
    return FrozenValuationExitCorpus(manifest_ref=manifest_ref, catalog_ref=catalog_ref)


def _freeze_case(
    root: Path, runner: ExitRegistry, descriptor: _CaseDescriptor
) -> ValuationExitCase:
    payload = _read_object(root, descriptor.file)
    spec = payload.get("spec")
    if not isinstance(spec, dict) or not isinstance(spec.get("data_path"), str):
        raise TypeError("valuation corpus case must declare one relative data path")
    relative = Path(spec["data_path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("valuation corpus data path must be canonical and relative")
    data_relative = Path("runner") / relative
    if not data_relative.is_relative_to(Path("runner/data")):
        raise ValueError("valuation corpus data path escapes the checked data root")
    data_ref = runner.publish_bytes(
        f"valuation-data-{descriptor.case_id}", _read_regular(root, data_relative)
    )
    spec["data_path"] = str(runner.materialize_data(data_ref, suffix=".csv"))
    payload["data_ref"] = data_ref.model_dump(mode="json")
    case_input = ValuationExitCaseInput.model_validate_json(json.dumps(payload))
    if (
        case_input.case_id != descriptor.case_id
        or case_input.family != descriptor.family
    ):
        raise ValueError("valuation descriptor does not match its case payload")
    case_ref = runner.publish(f"valuation-case-{descriptor.case_id}", case_input)
    return ValuationExitCase(
        case_id=descriptor.case_id,
        family=descriptor.family,
        role=descriptor.role,
        case_ref=case_ref,
        data_ref=data_ref,
    )


def _expectations(root: Path, relative: Path) -> tuple[ValuationCaseExpectation, ...]:
    payload: dict[str, Any] = _read_object(root, relative)
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise TypeError("valuation expectations must contain one case list")
    return tuple(
        ValuationCaseExpectation.model_validate_json(json.dumps(item)) for item in cases
    )
