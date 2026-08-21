"""Resumable immutable lifecycle promotion for research DAG artifacts."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter

from envresearch.kernel.artifact_graph import ArtifactNode
from envresearch.models.artifact import (
    ArtifactEnvelope,
    ArtifactRef,
    ProducerIdentity,
    ResearchArtifact,
    seal_artifact,
)
from envresearch.models.design import (
    AnalysisPlanPayload,
    DesignReviewPayload,
    EstimandSpecPayload,
    IdentificationMemoMetadata,
    MethodCandidatesPayload,
)
from envresearch.models.enums import ArtifactLifecycle
from envresearch.models.evidence import DataFeasibilityPayload, LiteratureMapPayload
from envresearch.models.intake import CandidateChartersPayload, ResearchCharterPayload
from envresearch.research.artifact_identity import (
    approved_provenance,
    make_envelope,
    require_identity,
    utc_now,
)
from envresearch.research.artifact_lifecycle_support import (
    artifact_ref,
    history_path,
    producer_identity,
)
from envresearch.storage.artifacts import ArtifactStore
from envresearch.storage.research_artifacts import ResearchArtifactStore

__all__ = ["ResearchArtifactLifecycle"]
_MODEL_BY_NODE: dict[str, type[BaseModel]] = {
    "frame-charters": CandidateChartersPayload,
    "normalize-brief": ResearchCharterPayload,
    "map-literature": LiteratureMapPayload,
    "inspect-data": DataFeasibilityPayload,
    "define-estimand": EstimandSpecPayload,
    "rank-methods": MethodCandidatesPayload,
    "review-design": DesignReviewPayload,
    "compose-plan": AnalysisPlanPayload,
}


class ResearchArtifactLifecycle:
    """Resume lifecycle writes while validating every durable identity field."""

    def __init__(
        self,
        workspace: Path,
        run_id: str,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.workspace = workspace
        self.run_id = run_id
        self.clock = clock
        self.raw = ArtifactStore(workspace)
        self.store = ResearchArtifactStore(workspace)

    def validate_candidate(
        self, node_id: str, data: bytes
    ) -> BaseModel | dict[str, Any]:
        try:
            value = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("worker candidate must be a JSON object") from error
        if not isinstance(value, dict):
            raise TypeError("worker candidate must contain an object")
        if node_id == "draft-identification":
            metadata = IdentificationMemoMetadata.model_validate_json(
                json.dumps(value.get("metadata"))
            )
            body = value.get("body")
            if not isinstance(body, str) or not body.strip():
                raise ValueError("identification memo body must not be blank")
            return {"metadata": metadata.model_dump(mode="json"), "body": body}
        return _MODEL_BY_NODE[node_id].model_validate_json(data)

    def promote_submission(
        self,
        node: ArtifactNode,
        payload: BaseModel | dict[str, Any],
        inputs: tuple[ArtifactRef, ...],
        producer: ProducerIdentity | None = None,
    ) -> None:
        identity = producer or producer_identity(node.worker_role or "worker")
        if node.node_id == "map-literature":
            assert isinstance(payload, LiteratureMapPayload)
            self.persist_structured(node.output_paths[0], payload, identity, inputs)
            self._persist_csv(payload, identity, inputs)
        elif node.node_id == "draft-identification":
            assert isinstance(payload, dict)
            self._persist_markdown(node.output_paths[0], payload, identity, inputs)
        else:
            assert isinstance(payload, BaseModel)
            self.persist_structured(node.output_paths[0], payload, identity, inputs)

    def persist_structured(
        self,
        path: Path,
        payload: object,
        component: str | ProducerIdentity,
        inputs: tuple[ArtifactRef, ...],
        *,
        final: ArtifactLifecycle = ArtifactLifecycle.VALIDATED,
    ) -> ResearchArtifact[object]:
        from envresearch.research.artifact_revision import persist_structured

        return persist_structured(self, path, payload, component, inputs, final)

    def supersede(
        self,
        path: Path,
        *,
        revision_id: str,
        reason: str,
        actor: str,
    ) -> ResearchArtifact[object]:
        from envresearch.research.artifact_revision import supersede

        return supersede(
            self, path, revision_id=revision_id, reason=reason, actor=actor
        )

    def promote_status(
        self,
        path: Path,
        status: ArtifactLifecycle,
        component: str,
        *,
        predecessor_ref: ArtifactRef,
        predecessor_component: str | ProducerIdentity,
        expected_inputs: tuple[ArtifactRef, ...],
        gate_context_hash: str,
    ) -> ResearchArtifact[object]:
        producer = producer_identity(component)
        predecessor_producer = producer_identity(predecessor_component)
        target_provenance = approved_provenance(
            path, component, predecessor_ref, gate_context_hash
        )
        predecessor = self.read_history(path, predecessor_ref.artifact_version)
        self._require_identity(
            predecessor,
            path,
            predecessor.payload,
            predecessor_producer,
            expected_inputs,
            predecessor_ref.artifact_version,
            ArtifactLifecycle.VALIDATED,
        )
        if artifact_ref(predecessor.envelope) != predecessor_ref:
            raise FileExistsError("reviewed predecessor reference mismatch")
        current = self.read_artifact(path)
        if current.envelope.validation_status is status:
            current = self._require_history_matches_current(path, current)
            self._require_identity(
                current,
                path,
                predecessor.payload,
                producer,
                expected_inputs,
                predecessor_ref.artifact_version + 1,
                status,
                provenance=target_provenance,
            )
            return current
        if current != predecessor:
            raise FileExistsError(
                "current artifact does not match reviewed predecessor"
            )
        promoted = self._history(
            path,
            predecessor.payload,
            producer,
            expected_inputs,
            predecessor_ref.artifact_version + 1,
            status,
            provenance=target_provenance,
        )
        self._publish_upgrade(path, predecessor, promoted)
        return promoted

    def input_refs(self, node: ArtifactNode) -> tuple[ArtifactRef, ...]:
        return tuple(self._artifact_ref(path) for path in node.input_paths)

    def artifact_ref(self, path: Path) -> ArtifactRef:
        return self._artifact_ref(path)

    def artifact_producer(self, path: Path) -> ProducerIdentity:
        """Return the authenticated producer sealed into a current artifact."""
        if path.suffix == ".csv":
            envelope = ArtifactEnvelope.model_validate(
                self.raw.read_json(path.with_suffix(".meta.json"))
            )
        elif path.suffix == ".md":
            envelope, _ = self.store.read_markdown(path)
        else:
            envelope = self.read_artifact(path).envelope
        return envelope.producer

    def current_envelope(self, path: Path) -> ArtifactEnvelope:
        """Return current metadata for structured, CSV, or Markdown artifacts."""
        from envresearch.research.artifact_revision import current_envelope

        return current_envelope(self, path)

    def history_ref(self, path: Path, version: int) -> ArtifactRef:
        artifact = self.read_history(path, version)
        return artifact_ref(artifact.envelope)

    def validated_history_ref(self, path: Path) -> ArtifactRef:
        from envresearch.research.artifact_revision import validated_history_ref

        return validated_history_ref(self, path)

    def require_validated(
        self,
        path: Path,
        *,
        producer: ProducerIdentity,
        inputs: tuple[ArtifactRef, ...],
    ) -> ResearchArtifact[object]:
        """Authenticate one current validated generation against immutable history."""
        current = self.read_artifact(path)
        if current.envelope.validation_status is not ArtifactLifecycle.VALIDATED:
            raise ValueError("artifact is not currently validated")
        current = self._require_history_matches_current(path, current)
        self._require_identity(
            current,
            path,
            current.payload,
            producer,
            inputs,
            current.envelope.artifact_version,
            ArtifactLifecycle.VALIDATED,
        )
        if self.validated_history_ref(path) != artifact_ref(current.envelope):
            raise FileExistsError("current artifact does not match validated history")
        return current

    def read_artifact(self, path: Path) -> ResearchArtifact[object]:
        return self.store.read_structured(path, TypeAdapter(ResearchArtifact[object]))

    def read_history(self, path: Path, version: int) -> ResearchArtifact[object]:
        artifact = self.read_artifact(history_path(path, version))
        if artifact.envelope.provenance.get("artifact_path") != path.as_posix():
            raise FileExistsError("artifact history path identity mismatch")
        return artifact

    def read_payload(self, path: Path, model: type[BaseModel]) -> Any:
        return model.model_validate_json(json.dumps(self.read_artifact(path).payload))

    def _persist_csv(
        self,
        payload: LiteratureMapPayload,
        component: str | ProducerIdentity,
        inputs: tuple[ArtifactRef, ...],
    ) -> None:
        from envresearch.research.artifact_special import persist_csv

        persist_csv(self, payload, component, inputs)

    def _persist_markdown(
        self,
        path: Path,
        payload: dict[str, Any],
        component: str | ProducerIdentity,
        inputs: tuple[ArtifactRef, ...],
    ) -> None:
        from envresearch.research.artifact_special import persist_markdown

        persist_markdown(self, path, payload, component, inputs)

    def _history(
        self,
        path: Path,
        payload: object,
        producer: ProducerIdentity,
        inputs: tuple[ArtifactRef, ...],
        version: int,
        status: ArtifactLifecycle,
        *,
        provenance: dict[str, object] | None = None,
    ) -> ResearchArtifact[object]:
        history = history_path(path, version)
        if (self.workspace / history).exists():
            artifact = self.read_artifact(history)
            self._require_identity(
                artifact,
                path,
                payload,
                producer,
                inputs,
                version,
                status,
                provenance=provenance,
            )
            return artifact
        artifact = seal_artifact(
            ResearchArtifact(
                envelope=make_envelope(
                    path=path,
                    run_id=self.run_id,
                    producer=producer,
                    inputs=inputs,
                    version=version,
                    status=status,
                    created_at=self.clock(),
                    provenance=provenance,
                ),
                payload=payload,
            )
        )
        self.store.write_structured(history, artifact)
        return artifact

    def _publish_structured(
        self, path: Path, artifact: ResearchArtifact[object]
    ) -> None:
        from envresearch.research.artifact_revision import publish_structured

        publish_structured(self, path, artifact)

    def _publish_upgrade(
        self,
        path: Path,
        previous: ResearchArtifact[object],
        promoted: ResearchArtifact[object],
    ) -> None:
        from envresearch.research.artifact_revision import publish_upgrade

        publish_upgrade(self, path, previous, promoted)

    def _require_history_matches_current(
        self, path: Path, current: ResearchArtifact[object]
    ) -> ResearchArtifact[object]:
        history = self.read_history(path, current.envelope.artifact_version)
        if history != current:
            raise FileExistsError("current artifact does not match immutable history")
        return current

    def _require_identity(
        self,
        artifact: ResearchArtifact[object],
        path: Path,
        payload: object,
        producer: ProducerIdentity,
        inputs: tuple[ArtifactRef, ...],
        version: int,
        status: ArtifactLifecycle,
        *,
        provenance: dict[str, object] | None = None,
    ) -> None:
        require_identity(
            artifact,
            path=path,
            payload=payload,
            run_id=self.run_id,
            producer=producer,
            inputs=inputs,
            version=version,
            status=status,
            provenance=provenance,
        )

    def _artifact_ref(self, path: Path) -> ArtifactRef:
        from envresearch.research.artifact_revision import current_artifact_ref

        return current_artifact_ref(self, path)
