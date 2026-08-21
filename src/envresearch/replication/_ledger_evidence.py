"""Private evidence authentication rules for replication ledger transitions."""

from __future__ import annotations

from pathlib import Path

from pydantic import TypeAdapter

from envresearch.models.artifact import ArtifactRef, ResearchArtifact
from envresearch.replication._ledger_models import OutputResult, ReplicationRun
from envresearch.replication._ledger_persistence import (
    LedgerPersistenceMixin,
    _from_json,
)
from envresearch.replication.contracts import (
    ApprovedTier2Intake,
    Tier2ExpectedOutput,
    Tier2IntakeProposal,
)


class LedgerEvidenceMixin(LedgerPersistenceMixin):
    def _resolve_admitted_inputs(
        self,
        approved_ref: ArtifactRef,
        acquired_ref: ArtifactRef,
        runtime_ref: ArtifactRef,
    ) -> None:
        """Authenticate the complete admitted intake chain before any state exists."""
        approved = self._read_checked(
            Path(f"artifacts/replication/approved/{approved_ref.content_hash}.json"),
            approved_ref,
            artifact_id="approved-tier2-intake",
            producer="tier2-intake",
        )
        admitted = _from_json(ApprovedTier2Intake, approved.payload)
        if approved.envelope.input_artifacts != (admitted.proposal_ref,):
            raise ValueError("approved intake inputs do not bind proposal")
        proposal = self._read_checked(
            Path(
                f"artifacts/replication/proposals/{admitted.proposal_ref.content_hash}.json"
            ),
            admitted.proposal_ref,
            artifact_id="tier2-intake-proposal",
            producer="tier2-intake",
        )
        if proposal.envelope.input_artifacts:
            raise ValueError("proposal must not have input artifacts")
        acquired = self._read_checked(
            Path(f"artifacts/replication/inventories/{acquired_ref.content_hash}.json"),
            acquired_ref,
            artifact_id="acquired-tier2-package-inventory",
            producer="tier2-intake",
        )
        if acquired.envelope.input_artifacts != (approved_ref,):
            raise ValueError("acquired inventory inputs do not bind approved intake")
        runtime = self._read_checked(
            Path(f"artifacts/replication/runtime/{runtime_ref.content_hash}.json"),
            runtime_ref,
            artifact_id="tier2-runtime-observation",
            producer="tier2-container",
        )
        if runtime.envelope.input_artifacts != (approved_ref, acquired_ref):
            raise ValueError("runtime observation inputs do not bind admitted run")

    def _read_checked(
        self, path: Path, reference: ArtifactRef, *, artifact_id: str, producer: str
    ) -> ResearchArtifact[object]:
        artifact = self._store.read_structured(
            path, TypeAdapter(ResearchArtifact[object])
        )
        if self._reference_untyped(artifact) != reference:
            raise ValueError("artifact reference does not match authoritative record")
        if artifact.envelope.artifact_id != artifact_id:
            raise ValueError("artifact ID is not admitted for replication")
        if artifact.envelope.producer.component != producer:
            raise ValueError("artifact producer is not admitted for replication")
        return artifact

    def _declared_outputs(self, approved_ref: ArtifactRef) -> tuple[str, ...]:
        approved_artifact = self._store.read_structured(
            Path(f"artifacts/replication/approved/{approved_ref.content_hash}.json"),
            TypeAdapter(ResearchArtifact[object]),
        )
        if self._reference_untyped(approved_artifact) != approved_ref:
            raise ValueError("approved intake artifact reference mismatch")
        approved = _from_json(ApprovedTier2Intake, approved_artifact.payload)
        proposal = self._proposal(approved.proposal_ref)
        return tuple(output.path for output in proposal.expected_outputs)

    def _proposal_from_approved(self, approved_ref: ArtifactRef) -> Tier2IntakeProposal:
        approved_artifact = self._store.read_structured(
            Path(f"artifacts/replication/approved/{approved_ref.content_hash}.json"),
            TypeAdapter(ResearchArtifact[object]),
        )
        approved = _from_json(ApprovedTier2Intake, approved_artifact.payload)
        return self._proposal(approved.proposal_ref)

    def _proposal(self, proposal_ref: ArtifactRef) -> Tier2IntakeProposal:
        proposal_artifact = self._store.read_structured(
            Path(f"artifacts/replication/proposals/{proposal_ref.content_hash}.json"),
            TypeAdapter(ResearchArtifact[object]),
        )
        if not isinstance(proposal_artifact.payload, dict):
            raise TypeError("proposal artifact payload must be an object")
        payload = dict(proposal_artifact.payload)
        inputs = payload.get("declared_inputs")
        outputs = payload.get("expected_outputs")
        if not isinstance(inputs, list) or not isinstance(outputs, list):
            raise TypeError("proposal declarations must be arrays")
        restored_inputs: list[dict[str, object]] = []
        for item in inputs:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise TypeError("proposal declared input must have a path")
            restored = dict(item)
            restored["path"] = Path(item["path"])
            restored_inputs.append(restored)
        payload["declared_inputs"] = tuple(restored_inputs)
        payload["expected_outputs"] = tuple(outputs)
        return Tier2IntakeProposal.model_validate(payload)

    def _require_declared_outputs(
        self,
        current: ResearchArtifact[ReplicationRun],
        declared: tuple[Tier2ExpectedOutput, ...],
        results: tuple[OutputResult, ...],
    ) -> None:
        observed = {result.path: result for result in results}
        expected = {output.path: output for output in declared}
        if len(observed) != len(results) or set(observed) != set(expected):
            raise ValueError("every declared output requires exactly one hash result")
        if any(not result.comparison_passed for result in results):
            raise ValueError("declared output comparison did not pass")
        if any(
            observed[path].comparator != output.comparator
            for path, output in expected.items()
        ):
            raise ValueError("declared output comparator differs from approval")
        admitted_inputs = (
            current.payload.approved_intake_ref,
            current.payload.acquired_inventory_ref,
            current.payload.runtime_ref,
        )
        for result in results:
            artifact = self._read_checked(
                Path(
                    f"artifacts/replication/outputs/{result.artifact_ref.content_hash}.json"
                ),
                result.artifact_ref,
                artifact_id="tier2-author-output",
                producer="tier2-replication",
            )
            self._require_log(result.log_ref, admitted_inputs)
            if artifact.envelope.input_artifacts != (
                *admitted_inputs,
                result.log_ref,
                result.raw_ref,
            ):
                raise ValueError("author output inputs do not bind admitted run")
            if not isinstance(artifact.payload, dict) or any(
                artifact.payload.get(field) != getattr(result, field)
                for field in ("path", "sha256", "comparator", "comparison_passed")
            ):
                raise ValueError(
                    "author output evidence does not match persisted result"
                )

    def _require_derived(
        self,
        current: ResearchArtifact[ReplicationRun],
        outputs: tuple[OutputResult, ...],
        derived_ref: ArtifactRef,
        derived_log_ref: ArtifactRef,
    ) -> None:
        artifact = self._read_checked(
            Path(f"artifacts/replication/derived/{derived_ref.content_hash}.json"),
            derived_ref,
            artifact_id="tier2-derived-output",
            producer="tier2-replication",
        )
        admitted_inputs = (
            current.payload.approved_intake_ref,
            current.payload.acquired_inventory_ref,
            current.payload.runtime_ref,
        )
        self._require_log(derived_log_ref, admitted_inputs)
        expected_inputs = (
            *admitted_inputs,
            *(result.artifact_ref for result in outputs),
            derived_log_ref,
        )
        if artifact.envelope.input_artifacts != expected_inputs:
            raise ValueError("derived output inputs do not bind author output evidence")

    def _require_log(
        self, log_ref: ArtifactRef, inputs: tuple[ArtifactRef, ...]
    ) -> None:
        artifact = self._read_checked(
            Path(f"artifacts/replication/logs/{log_ref.content_hash}.json"),
            log_ref,
            artifact_id="tier2-execution-log",
            producer="tier2-replication",
        )
        if artifact.envelope.input_artifacts != inputs:
            raise ValueError("execution log inputs do not bind admitted run")
        if not isinstance(artifact.payload, dict) or set(artifact.payload) != {
            "stage",
            "stdout_sha256",
            "stderr_sha256",
            "stdout_truncated",
            "stderr_truncated",
            "stdout",
            "stderr",
        }:
            raise ValueError("execution log payload is not bounded and redacted")
        if (
            artifact.payload["stdout"] != "[redacted]"
            or artifact.payload["stderr"] != "[redacted]"
        ):
            raise ValueError("execution log payload contains unredacted text")

    def _verification_refs(
        self, current: ResearchArtifact[ReplicationRun]
    ) -> tuple[ArtifactRef, ...]:
        approved = self._read_checked(
            Path(
                "artifacts/replication/approved/"
                f"{current.payload.approved_intake_ref.content_hash}.json"
            ),
            current.payload.approved_intake_ref,
            artifact_id="approved-tier2-intake",
            producer="tier2-intake",
        )
        admitted = _from_json(ApprovedTier2Intake, approved.payload)
        derived_ref = current.payload.derived_ref
        derived_log_ref = current.payload.derived_log_ref
        if derived_ref is None or derived_log_ref is None:
            raise ValueError("replication execution evidence is incomplete")
        return (
            admitted.proposal_ref,
            current.payload.attempt_ref,
            current.payload.approved_intake_ref,
            current.payload.acquired_inventory_ref,
            current.payload.runtime_ref,
            *(
                reference
                for item in current.payload.author_outputs
                for reference in (item.log_ref, item.raw_ref, item.artifact_ref)
            ),
            derived_ref,
            derived_log_ref,
            self._reference(current),
        )

    def _ledger_inputs(self, payload: ReplicationRun) -> tuple[ArtifactRef, ...]:
        return (
            payload.attempt_ref,
            payload.approved_intake_ref,
            payload.acquired_inventory_ref,
            payload.runtime_ref,
            *(
                reference
                for item in payload.author_outputs
                for reference in (item.log_ref, item.raw_ref, item.artifact_ref)
            ),
            *((payload.derived_ref,) if payload.derived_ref is not None else ()),
            *(
                (payload.derived_log_ref,)
                if payload.derived_log_ref is not None
                else ()
            ),
            *(
                (payload.verification_ref,)
                if payload.verification_ref is not None
                else ()
            ),
            *(
                (payload.exception.evidence_refs)
                if payload.exception is not None
                else ()
            ),
        )
