"""Publication and read-only reopening for exact V0.2 approved designs."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from envresearch.econometrics.exit_registry import ExitRegistry, validate_separate_roots
from envresearch.factory.design_contracts import (
    ApprovedDesignHandoff,
    ResearchFileEvidence,
    approved_design_id,
)
from envresearch.factory.errors import (
    FactoryAuthorityInvalid,
    FactoryIntegrityInvalid,
    FactoryScopeExceeded,
    FactorySupportInvalid,
)
from envresearch.models.artifact import ArtifactRef
from envresearch.research.audit_state import (
    DECISION_LOG_PATH,
    MANIFEST_PATH,
    ResearchRunManifest,
)
from envresearch.research.final_integrity import (
    FinalApprovalState,
    reopen_complete_final_exact,
)
from envresearch.research.orchestrator import ResearchOrchestrator
from envresearch.workers.filesystem import PinnedRoot

_HANDOFF_ID = "approved-design-handoff"
_HANDOFF_VERSION = 1


class V02ApprovedDesignResolver:
    """Bind one completed V0.2 Final Gate to an immutable factory handoff."""

    def __init__(self, orchestrator: ResearchOrchestrator, factory_root: Path) -> None:
        self.orchestrator = orchestrator
        self.workspace = orchestrator.workspace
        try:
            if not factory_root.is_absolute():
                raise ValueError("factory root must be absolute")
            validate_separate_roots(self.workspace, factory_root)
        except ValueError as error:
            raise FactoryScopeExceeded(
                "factory root must be physically separate from the research root",
                finding_kind="scope",
            ) from error
        self.factory_root = factory_root

    @contextmanager
    def authority_lease(self) -> Iterator[None]:
        """Hold the V0.2 mutation lease while publishing a factory handoff."""
        try:
            with self.orchestrator.queue.control.transaction_lock("mutation"):
                yield
        except FactoryAuthorityInvalid:
            raise
        except OSError as error:
            raise FactoryAuthorityInvalid(
                "factory authority lease is unavailable", finding_kind="authority"
            ) from error

    def build(self, plan_ref: ArtifactRef, context_ref: ArtifactRef) -> ArtifactRef:
        """Publish one immutable handoff only while its source Final Gate is stable."""
        try:
            design_id = approved_design_id(plan_ref, context_ref)
            with self.authority_lease():
                registry = self._registry(create=True)
                with registry.lock(self._subject(design_id)):
                    return self._publish(registry, plan_ref, context_ref, design_id)
        except FactoryAuthorityInvalid:
            raise
        except (OSError, ValidationError) as error:
            raise FactorySupportInvalid(
                "approved design source evidence is unavailable", finding_kind="support"
            ) from error
        except (TypeError, ValueError, FileExistsError) as error:
            raise FactoryIntegrityInvalid(
                "approved design handoff cannot be authenticated",
                finding_kind="integrity",
            ) from error

    def resolve(self, handoff_ref: ArtifactRef) -> ApprovedDesignHandoff:
        """Read and authenticate one current handoff, then reconstruct its V0.2 state."""
        try:
            reference = ArtifactRef.model_validate(handoff_ref.model_dump(mode="json"))
            if (
                reference.artifact_id != _HANDOFF_ID
                or reference.artifact_version != _HANDOFF_VERSION
            ):
                raise FactoryScopeExceeded(
                    "handoff reference is outside the V0.2 approved-design scope",
                    finding_kind="scope",
                )
            registry = self._registry(create=False)
            handoff = self._load_once(registry, reference)
            self._require_pointer(registry, self._subject(handoff.design_id), reference)
            self._require_pointer(
                registry, self._prepared_subject(handoff.design_id), reference
            )
            if handoff.design_id != approved_design_id(
                handoff.plan_ref, handoff.final_context_ref
            ):
                raise ValueError("handoff design identity is inconsistent")
            reconstructed = self._reopen(handoff.plan_ref, handoff.final_context_ref)
            if self._handoff(reconstructed) != handoff:
                raise ValueError("approved design source no longer matches its handoff")
            self._require_pointer(registry, self._subject(handoff.design_id), reference)
            self._require_pointer(
                registry, self._prepared_subject(handoff.design_id), reference
            )
            return handoff
        except FactoryScopeExceeded:
            raise
        except (OSError, ValidationError) as error:
            raise FactorySupportInvalid(
                "approved design handoff bytes are unavailable", finding_kind="support"
            ) from error
        except (TypeError, ValueError, FileExistsError) as error:
            raise FactoryIntegrityInvalid(
                "approved design handoff is stale or corrupt", finding_kind="integrity"
            ) from error

    def require_current(self, handoff_ref: ArtifactRef) -> None:
        """Require that a handoff remains the exact current factory pointer."""
        handoff = self.resolve(handoff_ref)
        try:
            self._require_pointer(
                self._registry(create=False),
                self._subject(handoff.design_id),
                handoff_ref,
            )
        except (OSError, TypeError, ValueError) as error:
            raise FactoryIntegrityInvalid(
                "approved design current pointer is invalid", finding_kind="integrity"
            ) from error

    def _reopen(
        self, plan_ref: ArtifactRef, context_ref: ArtifactRef
    ) -> FinalApprovalState:
        return reopen_complete_final_exact(
            lifecycle=self.orchestrator.lifecycle,
            gates=self.orchestrator.bound_gates,
            checkpoints=self.orchestrator.checkpoints,
            nodes=self.orchestrator._nodes,
            semantics=self.orchestrator.semantics,
            audit=self.orchestrator.audit,
            plan_ref=plan_ref,
            context_ref=context_ref,
        )

    def _handoff(self, state: FinalApprovalState) -> ApprovedDesignHandoff:
        manifest_bytes = self._read_evidence(MANIFEST_PATH)
        decision_log_bytes = self._read_evidence(DECISION_LOG_PATH)
        manifest = ResearchRunManifest.model_validate_json(manifest_bytes, strict=True)
        return ApprovedDesignHandoff(
            schema_version="factory.approved-design.v1",
            design_id=approved_design_id(state.plan_ref, state.context_ref),
            producer="research-factory-design-adapter-v1",
            manifest=manifest,
            manifest_evidence=self._evidence(MANIFEST_PATH, manifest_bytes),
            plan_ref=state.plan_ref,
            plan=state.plan,
            final_context_ref=state.context_ref,
            final_context=state.context,
            final_gate=state.gate,
            terminal_checkpoint=state.checkpoint,
            decision_log_evidence=self._evidence(DECISION_LOG_PATH, decision_log_bytes),
            method_profile_sha256=manifest.method_profile_sha256,
        )

    def _read_evidence(self, relative: Path) -> bytes:
        root = PinnedRoot(self.workspace, create=False)
        try:
            return root.read_file(relative, description="approved design evidence")
        finally:
            root.close()

    @staticmethod
    def _evidence(relative: Path, data: bytes) -> ResearchFileEvidence:
        return ResearchFileEvidence(
            relative_path=relative.as_posix(),
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )

    def _publish(
        self,
        registry: ExitRegistry,
        plan_ref: ArtifactRef,
        context_ref: ArtifactRef,
        design_id: str,
    ) -> ArtifactRef:
        prior_prepared, prior_current = self._probe_recovery_intent(
            registry, plan_ref, context_ref, design_id
        )
        before = self._reopen(plan_ref, context_ref)
        handoff = self._handoff(before)
        handoff_ref = registry.publish(_HANDOFF_ID, handoff, version=_HANDOFF_VERSION)
        prepared_subject = self._prepared_subject(design_id)
        current_subject = self._subject(design_id)
        prepared_installed = False
        current_installed = False
        try:
            prepared_installed = True
            registry.set_current(prepared_subject, handoff_ref)
            after = self._reopen(plan_ref, context_ref)
            if self._handoff(after) != handoff:
                raise ValueError("approved design changed during immutable publication")
            current_installed = True
            registry.set_current(current_subject, handoff_ref)
            final = self._reopen(plan_ref, context_ref)
            if (
                self._handoff(final) != handoff
                or registry.current(prepared_subject) != handoff_ref
                or registry.current(current_subject) != handoff_ref
            ):
                raise ValueError("approved design changed during final linearization")
            return handoff_ref
        except BaseException:
            if current_installed:
                registry.restore_current_if_unchanged(
                    current_subject, installed=handoff_ref, previous=prior_current
                )
            if prepared_installed:
                registry.restore_current_if_unchanged(
                    prepared_subject, installed=handoff_ref, previous=prior_prepared
                )
            raise

    def _probe_recovery_intent(
        self,
        registry: ExitRegistry,
        plan_ref: ArtifactRef,
        context_ref: ArtifactRef,
        design_id: str,
    ) -> tuple[ArtifactRef | None, ArtifactRef | None]:
        """Authenticate any installed intent before mutable source reconstruction."""
        prepared = registry.current(self._prepared_subject(design_id))
        current = registry.current(self._subject(design_id))
        if current is not None and prepared != current:
            raise ValueError("approved design recovery pointers conflict")
        intent = prepared or current
        if intent is None:
            return prepared, current
        handoff = self._load_once(registry, intent)
        if (
            handoff.design_id != design_id
            or handoff.plan_ref != plan_ref
            or handoff.final_context_ref != context_ref
        ):
            raise ValueError("approved design recovery intent conflicts with caller")
        evidence = (
            (handoff.manifest_evidence, self._read_evidence(MANIFEST_PATH)),
            (handoff.decision_log_evidence, self._read_evidence(DECISION_LOG_PATH)),
        )
        if any(
            item.size_bytes != len(data)
            or item.sha256 != hashlib.sha256(data).hexdigest()
            for item, data in evidence
        ):
            raise ValueError("approved design recovery anchors changed")
        return prepared, current

    def _registry(self, *, create: bool) -> ExitRegistry:
        return ExitRegistry(self.factory_root, create=create)

    def _load_once(
        self, registry: ExitRegistry, reference: ArtifactRef
    ) -> ApprovedDesignHandoff:
        if (
            reference.artifact_id != _HANDOFF_ID
            or reference.artifact_version != _HANDOFF_VERSION
        ):
            raise ValueError("approved design handoff reference identity is invalid")
        path = (
            Path("exit/objects")
            / reference.artifact_id
            / f"v{reference.artifact_version}-{reference.content_hash}.json"
        )
        data = registry.files.read(path)
        if hashlib.sha256(data).hexdigest() != reference.content_hash:
            raise ValueError("approved design handoff content hash mismatch")
        handoff = ApprovedDesignHandoff.model_validate_json(data)
        if data != handoff.model_dump_json(indent=None).encode("utf-8"):
            raise ValueError("approved design handoff bytes are not canonical")
        return handoff

    def _require_pointer(
        self, registry: ExitRegistry, subject: str, reference: ArtifactRef
    ) -> None:
        data = registry.files.read(Path("exit/current") / f"{subject}.json")
        if ArtifactRef.model_validate_json(data, strict=True) != reference:
            raise ValueError("approved design current pointer changed")

    @staticmethod
    def _subject(design_id: str) -> str:
        return f"approved-design-{design_id}"

    @staticmethod
    def _prepared_subject(design_id: str) -> str:
        return f"approved-design-{design_id}-prepared"

    @staticmethod
    def _design_id(plan_ref: ArtifactRef, context_ref: ArtifactRef) -> str:
        return approved_design_id(plan_ref, context_ref)
