"""Freeze the checked local V0.3 exit corpus into separated registries."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from envresearch.econometrics.exit_models import (
    CaseExpectation,
    ExitCase,
    ExitCaseInput,
    ExitExpectationCatalog,
    Family,
    V03ExitManifest,
)
from envresearch.econometrics.exit_registry import ExitRegistry, validate_separate_roots
from envresearch.models.artifact import ArtifactRef

STRICT = ConfigDict(extra="forbid", frozen=True, strict=True)
MAX_CORPUS_FILE_BYTES = 32 * 1024 * 1024


class FrozenExitCorpus(BaseModel):
    """Exact references produced from one checked corpus generation."""

    model_config = STRICT
    manifest_ref: ArtifactRef
    catalog_ref: ArtifactRef


class _CaseDescriptor(BaseModel):
    model_config = STRICT
    case_id: str
    family: Family
    role: Literal["green", "assumption-failure", "integrity-failure"]
    file: Path

    @field_validator("file")
    @classmethod
    def relative_case_file(cls, value: Path) -> Path:
        if (
            value.is_absolute()
            or value.suffix != ".yaml"
            or any(part in {"", ".", ".."} for part in value.parts)
        ):
            raise ValueError("corpus case path must be canonical and relative")
        return value


class _ManifestDescriptor(BaseModel):
    model_config = STRICT
    schema_version: Literal["econometrics.v03-exit-corpus.v1"]
    manifest_id: str
    cases: tuple[_CaseDescriptor, ...]

    @model_validator(mode="after")
    def unique_descriptors(self) -> _ManifestDescriptor:
        if len(self.cases) != 16 or len({item.case_id for item in self.cases}) != 16:
            raise ValueError("corpus descriptor must contain 16 unique cases")
        return self


def freeze_exit_corpus(
    corpus_root: Path,
    runner: ExitRegistry,
    evaluator: ExitRegistry,
) -> FrozenExitCorpus:
    """Validate checked bytes and publish an expectation-blind exact manifest."""
    root = _directory(corpus_root)
    validate_separate_roots(runner.root, evaluator.root)
    descriptor = _ManifestDescriptor.model_validate_json(
        json.dumps(_read_object(root, Path("manifest.yaml")))
    )
    green = _expectations(root, Path("evaluator/expectations.json"))
    failures = _expectations(root, Path("evaluator/failures.json"))
    catalog = ExitExpectationCatalog(
        schema_version="econometrics.v03-exit-expectations.v1",
        manifest_id=descriptor.manifest_id,
        cases=(*green, *failures),
    )
    catalog_ref = evaluator.publish(f"exit-catalog-{descriptor.manifest_id}", catalog)
    cases = tuple(_freeze_case(root, runner, item) for item in descriptor.cases)
    manifest = V03ExitManifest(
        schema_version="econometrics.v03-exit-manifest.v1",
        manifest_id=descriptor.manifest_id,
        cases=cases,
        expectation_catalog_ref=catalog_ref,
    )
    manifest_ref = runner.publish(f"exit-manifest-{descriptor.manifest_id}", manifest)
    return FrozenExitCorpus(manifest_ref=manifest_ref, catalog_ref=catalog_ref)


def _freeze_case(
    root: Path, runner: ExitRegistry, descriptor: _CaseDescriptor
) -> ExitCase:
    payload = _read_object(root, descriptor.file)
    spec = payload.get("spec")
    if not isinstance(spec, dict) or not isinstance(spec.get("data_path"), str):
        raise TypeError("corpus case must declare one relative data path")
    relative = Path(spec["data_path"])
    if relative.is_absolute() or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise ValueError("corpus data path must be canonical and relative")
    data_relative = Path("runner") / relative
    if not data_relative.is_relative_to(Path("runner/data")):
        raise ValueError("corpus data path escapes the checked data root")
    data = _read_regular(root, data_relative)
    data_ref = runner.publish_bytes(f"exit-data-{descriptor.case_id}", data)
    data_path = runner.materialize_data(data_ref)
    spec["data_path"] = str(data_path)
    payload["data_ref"] = data_ref.model_dump(mode="json")
    case_input = ExitCaseInput.model_validate_json(json.dumps(payload))
    if (
        case_input.case_id != descriptor.case_id
        or case_input.family != descriptor.family
    ):
        raise ValueError("corpus case descriptor does not match its payload")
    case_ref = runner.publish(f"exit-case-{descriptor.case_id}", case_input)
    return ExitCase(
        case_id=descriptor.case_id,
        family=descriptor.family,
        role=descriptor.role,
        case_ref=case_ref,
        data_ref=data_ref,
    )


def _expectations(root: Path, relative: Path) -> tuple[CaseExpectation, ...]:
    payload = _read_object(root, relative)
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise TypeError("expectation file must contain one case list")
    return tuple(
        CaseExpectation.model_validate_json(json.dumps(item)) for item in cases
    )


def _read_object(root: Path, relative: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            _read_regular(root, relative).decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: _raise_constant(value),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(
            "corpus document is not strict JSON-compatible YAML"
        ) from error
    if not isinstance(payload, dict):
        raise TypeError("corpus document must contain one mapping")
    return payload


def _unique_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate corpus key: {key}")
        result[key] = value
    return result


def _raise_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _directory(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("corpus root must be absolute and non-symlink")
    root = path.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("corpus root must be a directory")
    return root


def _read_regular(root: Path, relative: Path) -> bytes:
    if relative.is_absolute() or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise ValueError("corpus input path must be canonical and relative")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    directory_flags = flags | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(root, directory_flags)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_descriptor = os.open(relative.name, flags, dir_fd=descriptor)
        try:
            opened = os.fstat(file_descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise ValueError("corpus input must be one regular non-symlink file")
            if opened.st_size > MAX_CORPUS_FILE_BYTES:
                raise ValueError("corpus input exceeds its byte limit")
            data = bytearray()
            while chunk := os.read(file_descriptor, 1024 * 1024):
                data.extend(chunk)
                if len(data) > MAX_CORPUS_FILE_BYTES:
                    raise ValueError("corpus input exceeds its byte limit")
            return bytes(data)
        finally:
            os.close(file_descriptor)
    except OSError as error:
        raise ValueError("corpus input must be one regular non-symlink file") from error
    finally:
        os.close(descriptor)
