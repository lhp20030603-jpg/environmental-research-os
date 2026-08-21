"""Trusted cross-artifact semantic validation for research submissions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from envresearch.kernel.artifact_graph import ArtifactNode
from envresearch.methods.models import MethodProfile
from envresearch.methods.registry import MethodProfileRegistry
from envresearch.models.artifact import ArtifactRef
from envresearch.models.design import (
    AnalysisPlanPayload,
    DesignReviewPayload,
    EstimandSpecPayload,
    IdentificationMemoMetadata,
    MethodCandidate,
    MethodCandidatesPayload,
)
from envresearch.models.enums import ArtifactLifecycle
from envresearch.models.evidence import (
    DataFeasibilityPayload,
    DatasetCandidate,
    LiteratureMapPayload,
)
from envresearch.models.method_screening import MethodRequirementKind
from envresearch.research.artifact_lifecycle import ResearchArtifactLifecycle
from envresearch.research.citation_attestations import ProtectedCitationAttestations
from envresearch.research.citation_gate import require_current_citation_report

_BUILTIN_METHODS = Path(__file__).resolve().parents[3] / "packs" / "methods"


def artifact_ref_token(ref: ArtifactRef) -> str:
    """Serialize an exact artifact/version/hash reference for scientific payloads."""
    return (
        f"artifact:{ref.artifact_id}@{ref.artifact_version}#sha256:{ref.content_hash}"
    )


def method_profile_token(profile: MethodProfile) -> str:
    """Serialize one exact installed methodology profile reference."""
    return f"{profile.profile_id}@{profile.version}"


class SemanticSubmissionValidator:
    """Resolve scientific references against the current trusted run state."""

    def __init__(
        self,
        lifecycle: ResearchArtifactLifecycle,
        nodes: dict[str, ArtifactNode],
        registry: MethodProfileRegistry | None = None,
        require_claim_verified_citations: bool = False,
        citation_attestations: ProtectedCitationAttestations | None = None,
    ) -> None:
        self.lifecycle = lifecycle
        self.nodes = nodes
        self.registry = registry or MethodProfileRegistry.discover(_BUILTIN_METHODS)
        self.require_claim_verified_citations = require_claim_verified_citations
        self.citation_attestations = citation_attestations

    def replace_nodes(self, nodes: dict[str, ArtifactNode]) -> None:
        """Adopt the orchestrator's rebound graph after trusted input augmentation."""
        self.nodes = nodes

    def validate(
        self,
        node_id: str,
        payload: BaseModel | dict[str, Any],
        current_refs: tuple[ArtifactRef, ...],
    ) -> None:
        """Fail closed unless payload semantics match exact current dependencies."""
        node = self.nodes[node_id]
        if current_refs != self.lifecycle.input_refs(node):
            raise ValueError(
                "submission references are not the current artifact inputs"
            )
        if node_id == "map-literature":
            self._validate_literature(payload)
        elif node_id == "define-estimand":
            self._validate_estimand(payload)
        elif node_id == "rank-methods":
            self._validate_methods(payload)
        elif node_id == "draft-identification":
            self._validate_memo(payload)
        elif node_id == "review-design":
            self._validate_review(payload)
        elif node_id == "compose-plan":
            self._validate_plan(payload)

    def validate_final(self) -> None:
        """Recheck terminal scientific continuity immediately before approval."""
        self._require_current_citation_report()
        self.validate_lineage()
        literature = self.lifecycle.read_payload(
            Path("artifacts/literature-map.json"), LiteratureMapPayload
        )
        self._validate_literature(literature)
        estimand = self.lifecycle.read_payload(
            Path("artifacts/estimand-spec.yaml"), EstimandSpecPayload
        )
        self._validate_estimand(estimand)
        methods = self.lifecycle.read_payload(
            Path("artifacts/method-candidates.json"), MethodCandidatesPayload
        )
        self._validate_methods(methods)
        memo = self._current_memo_payload()
        self._validate_memo(memo)
        plan = self.lifecycle.read_payload(
            Path("artifacts/analysis-plan.yaml"), AnalysisPlanPayload
        )
        self._validate_plan(plan)
        review = self.lifecycle.read_payload(
            Path("artifacts/design-review-findings.json"), DesignReviewPayload
        )
        self._validate_review(review)

    def _require_current_citation_report(self) -> None:
        if not self.require_claim_verified_citations:
            return
        if self.citation_attestations is None:
            raise ValueError("citation report authentication is unavailable")
        require_current_citation_report(self.lifecycle, self.citation_attestations)

    def _current_memo_payload(self) -> object:
        """Bind current Markdown bytes and envelope to immutable validated history."""
        path = Path("artifacts/identification-memo.md")
        current, body = self.lifecycle.store.read_markdown(path)
        if current.validation_status is not ArtifactLifecycle.VALIDATED:
            raise ValueError("identification memo is not currently validated")
        history = self.lifecycle.read_history(path, current.artifact_version)
        payload = history.payload
        if not isinstance(payload, dict):
            raise TypeError("identification memo history payload is invalid")
        expected = history.envelope.model_copy(
            update={
                "content_hash": payload.get("authoritative_content_hash"),
                "provenance": {
                    "node": history.envelope.producer.component,
                    "artifact_path": path.as_posix(),
                    "identification": payload.get("metadata"),
                },
            }
        )
        if current != expected or body != payload.get("body"):
            raise ValueError("current identification memo does not match its history")
        return payload

    def validate_lineage(self) -> None:
        """Require every current envelope edge to name current producer generations."""
        for node in self.nodes.values():
            existing = tuple(
                path
                for path in node.output_paths
                if (self.lifecycle.workspace / path).exists()
                and path.suffix in {".json", ".yaml", ".csv", ".md"}
                and not path.name.endswith(".meta.json")
            )
            if not existing:
                continue
            if node.node_id == "validate-citations":
                if self.citation_attestations is None:
                    raise ValueError("citation report authentication is unavailable")
                require_current_citation_report(
                    self.lifecycle, self.citation_attestations
                )
                continue
            expected = self.lifecycle.input_refs(node)
            for path in existing:
                envelope = self.lifecycle.current_envelope(path)
                if envelope.input_artifacts != expected:
                    raise ValueError(
                        "artifact envelope lineage is not the current producer generation"
                    )

    def _validate_literature(self, payload: object) -> None:
        if not isinstance(payload, LiteratureMapPayload):
            raise TypeError("map-literature requires a literature map")
        source_ids = tuple(source.source_id for source in payload.sources)
        evidence_ids = tuple(row.evidence_id for row in payload.evidence_rows)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("literature source IDs must be unique")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("literature evidence IDs must be unique")
        unknown = {row.source_id for row in payload.evidence_rows} - set(source_ids)
        if unknown:
            raise ValueError("literature evidence row source is not current")

    def _validate_estimand(self, payload: object) -> None:
        if not isinstance(payload, EstimandSpecPayload):
            raise TypeError("define-estimand requires an estimand specification")
        self._require_evidence(payload.evidence_refs)

    def _validate_methods(self, payload: object) -> None:
        if not isinstance(payload, MethodCandidatesPayload):
            raise TypeError("rank-methods requires method candidates")
        estimand_ref = self.lifecycle.artifact_ref(Path("artifacts/estimand-spec.yaml"))
        if payload.estimand_ref != artifact_ref_token(estimand_ref):
            raise ValueError(
                "method candidates estimand_ref is not the current artifact"
            )
        estimand = self.lifecycle.read_payload(
            Path("artifacts/estimand-spec.yaml"), EstimandSpecPayload
        )
        feasibility = self.lifecycle.read_payload(
            Path("artifacts/data-feasibility.yaml"), DataFeasibilityPayload
        )
        suitable = tuple(
            item for item in feasibility.candidates if item.suitable_for_design
        )
        if not suitable:
            raise ValueError("method compatibility requires a suitable current dataset")
        for candidate in payload.candidates:
            profile = self._resolve_profile(candidate.method_profile_ref)
            compatible = any(
                profile.is_compatible(
                    estimand.estimand_type.value,
                    structure,
                    frozenset(dataset.available_features),
                )
                for dataset in suitable
                for structure in dataset.data_structures
            )
            if compatible is not candidate.estimand_compatible:
                raise ValueError(
                    "method estimand_compatible does not match installed profile "
                    "and current data capabilities"
                )
            if not compatible:
                self._validate_rejection_evidence(
                    candidate, profile, estimand.estimand_type.value, suitable
                )

    @staticmethod
    def _validate_rejection_evidence(
        candidate: MethodCandidate,
        profile: MethodProfile,
        estimand_type: str,
        suitable: tuple[DatasetCandidate, ...],
    ) -> None:
        evidence = candidate.rejection_evidence
        if evidence is None:
            raise ValueError("rejected method lacks structured rejection evidence")
        requirements = frozenset(evidence.requirement_refs)
        if evidence.requirement_kind is MethodRequirementKind.ESTIMAND_TYPE:
            valid = (
                requirements == {estimand_type}
                and estimand_type not in profile.compatible_estimands
            )
        elif evidence.requirement_kind is MethodRequirementKind.DATA_STRUCTURE_SET:
            available = {
                structure
                for dataset in suitable
                for structure in dataset.data_structures
            }
            valid = (
                requirements == profile.required_data_structures
                and not requirements.intersection(available)
            )
        else:
            eligible = tuple(
                dataset
                for dataset in suitable
                if profile.required_data_structures.intersection(
                    dataset.data_structures
                )
            )
            valid = (
                requirements == profile.required_features
                and bool(eligible)
                and all(
                    not requirements.issubset(dataset.available_features)
                    for dataset in eligible
                )
            )
        if not valid:
            raise ValueError(
                "method rejection evidence does not identify an unmet current "
                "profile requirement"
            )

    def _validate_memo(self, payload: object) -> None:
        if not isinstance(payload, dict):
            raise TypeError("draft-identification requires memo metadata")
        metadata = IdentificationMemoMetadata.model_validate(payload.get("metadata"))
        self._require_selection_continuity(
            metadata.estimand_ref,
            metadata.primary_method_profile_ref,
            metadata.alternative_method_profile_refs,
        )
        self._require_evidence(metadata.evidence_refs)

    def _validate_plan(self, payload: object) -> None:
        if not isinstance(payload, AnalysisPlanPayload):
            raise TypeError("compose-plan requires an analysis plan")
        current = self.lifecycle.read_payload(
            Path("artifacts/estimand-spec.yaml"), EstimandSpecPayload
        )
        self._require_selection_continuity(
            payload.estimand_ref,
            payload.primary_method_profile_ref,
            payload.alternative_method_profile_refs,
        )
        if (
            payload.estimand_type is not None
            and payload.estimand_type is not current.estimand_type
        ):
            raise ValueError("plan estimand_type does not match the current estimand")
        if payload.estimand is not None and payload.estimand != current:
            raise ValueError("embedded plan estimand is not the current estimand")

    def _validate_review(self, payload: object) -> None:
        if not isinstance(payload, DesignReviewPayload):
            raise TypeError("review-design requires design findings")
        for finding in payload.findings:
            self._require_evidence(finding.evidence_refs)

    def _require_selection_continuity(
        self,
        estimand_ref: str,
        primary: str,
        alternatives: tuple[str, ...],
    ) -> None:
        current_estimand = artifact_ref_token(
            self.lifecycle.artifact_ref(Path("artifacts/estimand-spec.yaml"))
        )
        methods = self.lifecycle.read_payload(
            Path("artifacts/method-candidates.json"), MethodCandidatesPayload
        )
        if estimand_ref != current_estimand:
            raise ValueError("estimand selection is not the current artifact reference")
        if primary != methods.primary.method_profile_ref:
            raise ValueError("primary method selection is not current")
        expected_alternatives = tuple(
            item.method_profile_ref for item in methods.alternatives
        )
        if alternatives != expected_alternatives:
            raise ValueError("alternative method selections are not current")

    def _resolve_profile(self, reference: str) -> MethodProfile:
        try:
            profile_id, version = reference.rsplit("@", 1)
        except ValueError as error:
            raise ValueError(
                "method profile reference must include exact version"
            ) from error
        profile = self.registry.profiles.get(profile_id)
        if profile is None or method_profile_token(profile) != reference:
            raise ValueError(
                "method profile reference is not installed at that version"
            )
        if version != profile.version:
            raise ValueError("method profile version is not installed")
        return profile

    def _require_evidence(self, references: tuple[str, ...]) -> None:
        literature = self.lifecycle.read_payload(
            Path("artifacts/literature-map.json"), LiteratureMapPayload
        )
        known = {row.evidence_id for row in literature.evidence_rows}
        unknown = set(references) - known
        if unknown:
            raise ValueError(
                "evidence reference does not resolve to current literature"
            )
