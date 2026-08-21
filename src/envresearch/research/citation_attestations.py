"""Protected HMAC attestations for trusted citation and source generations."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

from envresearch.benchmarks.claim_integrity import CitationIntegrityValidator
from envresearch.benchmarks.claim_report import (
    CitationIntegrityReport,
    report_binding_is_valid,
    report_input_refs,
    report_payload,
)
from envresearch.models.artifact import ArtifactRef
from envresearch.research.citation_identity import (
    case_ref_sha256 as _case_ref_sha256,
)
from envresearch.research.citation_identity import ref_payload as _ref
from envresearch.research.citation_sources import (
    canonical_catalog_roots,
    load_registry_cases,
)
from envresearch.research.workflow import ResearchRunConfig
from envresearch.workers.queue import FilesystemWorkerQueue
from envresearch.workers.read_only import prepare_directories

if TYPE_CHECKING:
    from envresearch.benchmarks.blind_registry import LoadedBlindCase
    from envresearch.models.benchmark_evaluation import AcceptedArtifactClaims
    from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle

_HASH = hashlib.sha256
_CONFIG = ConfigDict(extra="forbid", frozen=True, strict=True)
_REPORT_PATH = Path("artifacts/citation-integrity-report.json")


class SourceGenerationAnchor(BaseModel):
    """One append-only authoritative blind-source generation."""

    model_config = _CONFIG

    generation: int = Field(ge=1)
    catalog_roots: tuple[str, ...]
    case_ids: tuple[str, ...]
    source_generations: tuple[int, ...]
    case_ref_sha256s: tuple[str, ...]
    source_sheet_refs: tuple[ArtifactRef, ...]
    claim_fact_map_refs: tuple[ArtifactRef, ...]
    blinded_brief_refs: tuple[ArtifactRef, ...]
    mac: str

    @field_validator("mac")
    @classmethod
    def require_mac(cls, value: str) -> str:
        return _require_digest(value)

    @field_validator("case_ref_sha256s")
    @classmethod
    def require_case_ref_sha256s(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_require_digest(item) for item in value)


class CitationReportAnchor(BaseModel):
    """Protected identity of one lifecycle-sealed citation report."""

    model_config = _CONFIG

    report_ref: ArtifactRef
    source_generation: int = Field(ge=1)
    report_payload_sha256: str
    binding_sha256: str
    mac: str

    @field_validator("report_payload_sha256", "binding_sha256", "mac")
    @classmethod
    def require_digest(cls, value: str) -> str:
        return _require_digest(value)


class CatalogAuthorityAnchor(BaseModel):
    """Protected identity of the immutable run-config catalog authority."""

    model_config = _CONFIG

    config_sha256: str
    catalog_roots: tuple[str, ...]
    mac: str

    @field_validator("config_sha256", "mac")
    @classmethod
    def require_digest(cls, value: str) -> str:
        return _require_digest(value)


class ProtectedCitationAttestations:
    """Authenticate validator output against the current registered sources."""

    def __init__(self, queue: FilesystemWorkerQueue, *, create: bool = True) -> None:
        self.queue = queue
        self.storage = queue.control.storage
        prepare_directories(
            self.storage,
            (
                "citation-attestations",
                "citation-attestations/sources",
                "citation-attestations/reports",
            ),
            create=create,
        )
        config_data = queue.exchange.read_file(
            Path("research-run-config.json"),
            description="authenticated research run config",
        )
        config = ResearchRunConfig.model_validate_json(config_data)
        unsigned = {
            "config_sha256": _HASH(config_data).hexdigest(),
            "catalog_roots": [str(item) for item in config.citation_catalog_roots],
        }
        expected = CatalogAuthorityAnchor(
            config_sha256=str(unsigned["config_sha256"]),
            catalog_roots=tuple(str(item) for item in config.citation_catalog_roots),
            mac=_mac(unsigned, self.queue.control.key),
        )
        path = Path("citation-attestations/catalog-authority.json")
        if self.storage.exists(path):
            data = self.storage.read_file(
                path, description="citation catalog authority", required_mode=0o600
            )
            actual = CatalogAuthorityAnchor.model_validate_json(data)
            self._require_anchor(data, actual)
            if actual != expected:
                raise ValueError("citation catalog authority changed")
        elif create:
            self.storage.write_file_noreplace(
                path, _canonical(_dump(expected)), mode=0o600
            )
        else:
            raise ValueError("citation catalog authority is missing")
        self.authorized_catalog_roots = tuple(
            Path(item) for item in expected.catalog_roots
        )

    @classmethod
    def open_existing(
        cls, queue: FilesystemWorkerQueue
    ) -> ProtectedCitationAttestations:
        """Authenticate existing citation controls without creating state."""
        return cls(queue, create=False)

    def validate_and_seal(
        self,
        *,
        lifecycle: ResearchArtifactLifecycle,
        case_roots: tuple[Path, ...],
        artifacts: tuple[AcceptedArtifactClaims, ...],
        before_persist: Callable[
            [SourceGenerationAnchor, CitationIntegrityReport, bool], None
        ],
    ) -> tuple[ArtifactRef, CitationIntegrityReport]:
        """Validate internally, then inseparably persist and attest that result."""
        requested = canonical_catalog_roots(case_roots)
        if requested != self.authorized_catalog_roots:
            raise ValueError("citation catalog roots are not authorized for this run")
        cases, catalog_roots = load_registry_cases(self.authorized_catalog_roots)
        source_generation, source_changed = self._bind_loaded_sources(
            cases, catalog_roots
        )
        report = CitationIntegrityValidator().validate_loaded_cases(
            cases=cases, artifacts=artifacts
        )
        if not report.passed or not report_binding_is_valid(report):
            raise ValueError("citation integrity validation did not pass exactly")
        before_persist(source_generation, report, source_changed)
        lifecycle.persist_structured(
            _REPORT_PATH,
            report_payload(report),
            "citation-integrity-validator",
            report_input_refs(report),
        )
        report_ref = lifecycle.artifact_ref(_REPORT_PATH)
        unsigned = {
            "report_ref": _ref(report_ref),
            "source_generation": source_generation.generation,
            "report_payload_sha256": _HASH(
                _canonical(report_payload(report))
            ).hexdigest(),
            "binding_sha256": report.binding_sha256,
        }
        anchor = CitationReportAnchor(
            report_ref=report_ref,
            source_generation=source_generation.generation,
            report_payload_sha256=str(unsigned["report_payload_sha256"]),
            binding_sha256=report.binding_sha256,
            mac=_mac(unsigned, self.queue.control.key),
        )
        path = self._report_path(report_ref)
        data = _canonical(_dump(anchor))
        if self.storage.exists(path):
            if self._read_report(report_ref) != anchor:
                raise RuntimeError("citation report attestation collision")
        else:
            try:
                self.storage.write_file_noreplace(path, data, mode=0o600)
            except FileExistsError:
                if self._read_report(report_ref) != anchor:
                    raise RuntimeError("citation report attestation collision")
        return report_ref, report

    def _bind_loaded_sources(
        self,
        cases: tuple[LoadedBlindCase, ...],
        catalog_roots: tuple[str, ...],
    ) -> tuple[SourceGenerationAnchor, bool]:
        cases = tuple(sorted(cases, key=lambda item: item.source_sheet.case_id))
        case_ids = tuple(item.source_sheet.case_id for item in cases)
        source_generations = tuple(
            item.source_sheet.source_generation for item in cases
        )
        case_ref_sha256s = tuple(_case_ref_sha256(item) for item in cases)
        source_refs = tuple(sorted((item.source_ref for item in cases), key=str))
        map_refs = tuple(sorted((item.claim_fact_map_ref for item in cases), key=str))
        brief_refs = tuple(sorted((item.brief_ref for item in cases), key=str))
        if not source_refs or any(
            len(refs) != len(set(refs)) for refs in (source_refs, map_refs, brief_refs)
        ):
            raise ValueError("citation source generation must be nonempty and unique")
        chain = self._source_chain(required=False)
        latest = chain[-1] if chain else None
        if latest is not None and (
            latest.source_sheet_refs,
            latest.claim_fact_map_refs,
            latest.blinded_brief_refs,
        ) == (source_refs, map_refs, brief_refs):
            if (
                latest.catalog_roots != catalog_roots
                or latest.case_ids != case_ids
                or latest.source_generations != source_generations
                or latest.case_ref_sha256s != case_ref_sha256s
            ):
                raise ValueError("citation source generation identity mismatch")
            return latest, False
        if latest is not None:
            if latest.catalog_roots != catalog_roots or latest.case_ids != case_ids:
                raise ValueError(
                    "citation registry identity cannot change within a run"
                )
            for index, current in enumerate(source_generations):
                if (
                    case_ref_sha256s[index] != latest.case_ref_sha256s[index]
                    and current <= latest.source_generations[index]
                ):
                    raise ValueError("citation source generation must advance")
            signature = (source_refs, map_refs, brief_refs)
            if any(
                (
                    item.source_sheet_refs,
                    item.claim_fact_map_refs,
                    item.blinded_brief_refs,
                )
                == signature
                for item in chain[:-1]
            ):
                raise ValueError("superseded citation source generation replay")
        generation = 1 if latest is None else latest.generation + 1
        unsigned = {
            "generation": generation,
            "catalog_roots": list(catalog_roots),
            "case_ids": list(case_ids),
            "source_generations": list(source_generations),
            "case_ref_sha256s": list(case_ref_sha256s),
            "source_sheet_refs": [_ref(item) for item in source_refs],
            "claim_fact_map_refs": [_ref(item) for item in map_refs],
            "blinded_brief_refs": [_ref(item) for item in brief_refs],
        }
        anchor = SourceGenerationAnchor(
            generation=generation,
            catalog_roots=catalog_roots,
            case_ids=case_ids,
            source_generations=source_generations,
            case_ref_sha256s=case_ref_sha256s,
            source_sheet_refs=source_refs,
            claim_fact_map_refs=map_refs,
            blinded_brief_refs=brief_refs,
            mac=_mac(unsigned, self.queue.control.key),
        )
        path = self._source_path(generation)
        self.storage.write_file_noreplace(path, _canonical(_dump(anchor)), mode=0o600)
        return self._read_source(generation), True

    def require_current_report(
        self, report_ref: ArtifactRef, report: CitationIntegrityReport
    ) -> None:
        """Require the report's protected anchor and latest source generation."""
        try:
            anchor = self._read_report(report_ref)
            sources = self.latest_sources(required=True)
        except FileNotFoundError as error:
            raise ValueError("citation report authentication is missing") from error
        assert sources is not None
        expected_payload_hash = _HASH(_canonical(report_payload(report))).hexdigest()
        if (
            anchor.report_ref != report_ref
            or anchor.source_generation != sources.generation
            or anchor.report_payload_sha256 != expected_payload_hash
            or anchor.binding_sha256 != report.binding_sha256
            or report.source_sheet_refs != sources.source_sheet_refs
            or report.claim_fact_map_refs != sources.claim_fact_map_refs
            or report.blinded_brief_refs != sources.blinded_brief_refs
        ):
            raise ValueError("citation report authentication is not current")

    def latest_sources(self, *, required: bool) -> SourceGenerationAnchor | None:
        """Read and authenticate the complete append-only source generation chain."""
        anchors = self._source_chain(required=required)
        return anchors[-1] if anchors else None

    def _source_chain(self, *, required: bool) -> tuple[SourceGenerationAnchor, ...]:
        names = self.storage.list_directory(Path("citation-attestations/sources"))
        if not names:
            if required:
                raise FileNotFoundError("citation source authentication is missing")
            return ()
        anchors: list[SourceGenerationAnchor] = []
        for generation, name in enumerate(sorted(names), 1):
            if name != f"{generation:08d}.json":
                raise ValueError("citation source generation chain is invalid")
            anchors.append(self._read_source(generation))
        return tuple(anchors)

    def _read_source(self, generation: int) -> SourceGenerationAnchor:
        data = self.storage.read_file(
            self._source_path(generation),
            description="citation source attestation",
            required_mode=0o600,
        )
        anchor = SourceGenerationAnchor.model_validate_json(data)
        self._require_anchor(data, anchor)
        if anchor.generation != generation:
            raise ValueError("citation source generation identity mismatch")
        return anchor

    def _read_report(self, report_ref: ArtifactRef) -> CitationReportAnchor:
        data = self.storage.read_file(
            self._report_path(report_ref),
            description="citation report attestation",
            required_mode=0o600,
        )
        anchor = CitationReportAnchor.model_validate_json(data)
        self._require_anchor(data, anchor)
        return anchor

    def _require_anchor(self, data: bytes, anchor: BaseModel) -> None:
        payload = _dump(anchor)
        unsigned = {key: value for key, value in payload.items() if key != "mac"}
        mac = payload.get("mac")
        if (
            data != _canonical(payload)
            or not isinstance(mac, str)
            or not hmac.compare_digest(mac, _mac(unsigned, self.queue.control.key))
        ):
            raise ValueError("citation attestation authentication failed")

    @staticmethod
    def _source_path(generation: int) -> Path:
        return Path("citation-attestations/sources") / f"{generation:08d}.json"

    @staticmethod
    def _report_path(report_ref: ArtifactRef) -> Path:
        return Path("citation-attestations/reports") / (
            f"{report_ref.artifact_version:08d}-{report_ref.content_hash}.json"
        )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()


def _dump(model: BaseModel) -> dict[str, object]:
    return model.model_dump(mode="json")


def _mac(value: object, key: bytes) -> str:
    return hmac.new(key, _canonical(value), _HASH).hexdigest()


def _require_digest(value: str) -> str:
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError("citation attestation digest must be lowercase SHA-256")
    return value
