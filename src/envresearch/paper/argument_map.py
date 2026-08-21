"""Validation and publication for the V0.4 typed argument map."""

from __future__ import annotations

import hashlib
import heapq
from pathlib import Path

from pydantic import ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.paper.argument_contracts import ArgumentMap, ArgumentMapCandidate
from envresearch.paper.contracts import ClaimEvidenceLedger
from envresearch.paper.errors import (
    PaperAuthorityInvalid,
    PaperBuilderError,
    PaperIntegrityInvalid,
    PaperSupportInvalid,
)
from envresearch.paper.ledger import CLAIM_LEDGER_SUBJECT, ClaimLedgerService

ARGUMENT_MAP_SUBJECT = "paper-argument-map"


def validate_argument_map(
    argument_map: ArgumentMap | ArgumentMapCandidate,
    *,
    ledger_claim_ids: frozenset[str],
) -> tuple[str, ...]:
    """Validate exact claim support and return one deterministic Kahn order."""
    nodes = argument_map.nodes
    edges = argument_map.edges
    node_ids = tuple(node.node_id for node in nodes)
    if len(node_ids) != len(set(node_ids)):
        raise PaperSupportInvalid(
            "argument node ids must be unique",
            finding_kind="argument-node-duplicate",
        )
    edge_keys = tuple(
        (edge.source_id, edge.target_id, edge.edge_type) for edge in edges
    )
    if len(edge_keys) != len(set(edge_keys)):
        raise PaperSupportInvalid(
            "argument edges must be unique",
            finding_kind="argument-edge-duplicate",
        )

    by_id = {node.node_id: node for node in nodes}
    if any(
        edge.source_id not in by_id or edge.target_id not in by_id for edge in edges
    ):
        raise PaperSupportInvalid(
            "argument edge endpoint is unknown",
            finding_kind="argument-edge-dangling",
        )
    if any(
        claim_id not in ledger_claim_ids
        for node in nodes
        for claim_id in node.claim_ids
    ):
        raise PaperSupportInvalid(
            "argument references an unknown ledger claim",
            finding_kind="argument-claim-dangling",
        )

    incoming = {
        node_id: tuple(edge for edge in edges if edge.target_id == node_id)
        for node_id in node_ids
    }
    for node in nodes:
        if node.node_type == "contribution" and not any(
            edge.edge_type == "evidence-backed"
            and by_id[edge.source_id].node_type == "empirical-claim"
            for edge in incoming[node.node_id]
        ):
            raise PaperSupportInvalid(
                "contribution requires direct evidence-backed empirical input",
                finding_kind="argument-contribution-unsupported",
            )
        if node.node_type == "policy-implication" and not any(
            edge.edge_type in {"conditional", "evidence-backed"}
            and by_id[edge.source_id].node_type == "empirical-claim"
            for edge in incoming[node.node_id]
        ):
            raise PaperSupportInvalid(
                "policy implication requires direct accepted input",
                finding_kind="argument-policy-unsupported",
            )

    indegree = {node_id: 0 for node_id in node_ids}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        outgoing[edge.source_id].append(edge.target_id)
        indegree[edge.target_id] += 1
    ready = [node_id for node_id, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        node_id = heapq.heappop(ready)
        order.append(node_id)
        for target_id in sorted(outgoing[node_id]):
            indegree[target_id] -= 1
            if indegree[target_id] == 0:
                heapq.heappush(ready, target_id)
    if len(order) != len(nodes):
        raise PaperSupportInvalid(
            "argument map must be acyclic",
            finding_kind="argument-cycle",
        )
    return tuple(order)


class ArgumentMapService:
    """Publish and reopen one exact argument map over a current claim ledger."""

    def __init__(self, *, ledger_service: ClaimLedgerService) -> None:
        self.ledger_service = ledger_service
        self.registry = ledger_service.registry

    def build(
        self, ledger_ref: ArtifactRef, candidate: ArgumentMapCandidate
    ) -> ArtifactRef:
        """Canonicalize and publish caller graph content over one exact ledger."""
        with self.ledger_service.resolver.authority_lease():
            return self._build(ledger_ref, candidate)

    def _build(
        self, ledger_ref: ArtifactRef, candidate: ArgumentMapCandidate
    ) -> ArtifactRef:
        """Build while the caller owns the accepted-evidence authority lease."""
        candidate = self._validate_candidate(candidate)
        ledger = self._ledger_status(ledger_ref)
        argument_map = self._materialize(ledger_ref, ledger, candidate)
        with self.registry.lock(ARGUMENT_MAP_SUBJECT):
            self._require_same_ledger(ledger_ref, ledger)
            prior = self._current()
            if prior is not None:
                existing = self._load(prior)
                if existing != argument_map:
                    raise PaperAuthorityInvalid(
                        "a different argument map is already current",
                        finding_kind="argument-map-current-conflict",
                    )
                self._require_same_ledger(ledger_ref, ledger)
                if self._current() != prior:
                    raise PaperAuthorityInvalid(
                        "argument map changed during publication",
                        finding_kind="argument-map-not-current",
                    )
                self._require_same_map(prior, argument_map)
                return prior
            try:
                reference = self.registry.publish(argument_map.map_id, argument_map)
            except (OSError, ValueError) as exc:
                raise PaperIntegrityInvalid(
                    "argument map immutable publication failed",
                    finding_kind="argument-map-publication-failed",
                ) from exc
            self._require_same_ledger(ledger_ref, ledger)
            try:
                self.registry.set_current(ARGUMENT_MAP_SUBJECT, reference)
            except (OSError, ValueError) as exc:
                self._restore_current(previous=prior, installed=reference)
                raise PaperIntegrityInvalid(
                    "argument map current publication failed",
                    finding_kind="argument-map-publication-failed",
                ) from exc
            try:
                reopened = self._load(reference)
                if reopened != argument_map:
                    raise PaperIntegrityInvalid(
                        "argument map changed during publication",
                        finding_kind="argument-map-reconstruction-mismatch",
                    )
                if self._current() != reference:
                    raise PaperAuthorityInvalid(
                        "argument map changed during publication",
                        finding_kind="argument-map-not-current",
                    )
                current_ledger = self._require_same_ledger(ledger_ref, ledger)
                validate_argument_map(
                    reopened,
                    ledger_claim_ids=frozenset(
                        claim.claim_id for claim in current_ledger.claims
                    ),
                )
                if self._current() != reference:
                    raise PaperAuthorityInvalid(
                        "argument map changed during publication",
                        finding_kind="argument-map-not-current",
                    )
                self._require_same_map(reference, argument_map)
            except PaperBuilderError:
                self._restore_current(previous=prior, installed=reference)
                raise
            return reference

    def status(self, map_ref: ArtifactRef, ledger_ref: ArtifactRef) -> ArgumentMap:
        """Reopen one current map and its exact current ledger authority."""
        with self.ledger_service.resolver.authority_lease():
            return self._status(map_ref, ledger_ref)

    def _status(self, map_ref: ArtifactRef, ledger_ref: ArtifactRef) -> ArgumentMap:
        """Reopen while the caller owns the accepted-evidence authority lease."""
        if self._current() != map_ref:
            raise PaperAuthorityInvalid(
                "argument map reference is not current",
                finding_kind="argument-map-not-current",
            )
        argument_map = self._load(map_ref)
        if argument_map.ledger_ref != ledger_ref:
            raise PaperAuthorityInvalid(
                "argument map binds another claim ledger",
                finding_kind="argument-ledger-reference-mismatch",
            )
        ledger = self.ledger_service.status(ledger_ref, argument_map.transition_ref)
        validate_argument_map(
            argument_map,
            ledger_claim_ids=frozenset(claim.claim_id for claim in ledger.claims),
        )
        if self._current() != map_ref:
            raise PaperAuthorityInvalid(
                "argument map changed during status",
                finding_kind="argument-map-not-current",
            )
        return self._require_same_map(map_ref, argument_map)

    def _ledger_status(self, ledger_ref: ArtifactRef) -> ClaimEvidenceLedger:
        try:
            current = self.registry.current(CLAIM_LEDGER_SUBJECT)
        except (OSError, ValueError, ValidationError) as exc:
            raise PaperIntegrityInvalid(
                "claim ledger current pointer is invalid",
                finding_kind="ledger-current-invalid",
            ) from exc
        if current != ledger_ref:
            raise PaperAuthorityInvalid(
                "claim ledger reference is not current",
                finding_kind="ledger-not-current",
            )
        try:
            preview = self.registry.load(ledger_ref, ClaimEvidenceLedger)
        except (OSError, ValueError, ValidationError) as exc:
            raise PaperIntegrityInvalid(
                "claim ledger preview is invalid",
                finding_kind="ledger-bytes-invalid",
            ) from exc
        return self.ledger_service.status(ledger_ref, preview.transition_ref)

    def _require_same_ledger(
        self, ledger_ref: ArtifactRef, expected: ClaimEvidenceLedger
    ) -> ClaimEvidenceLedger:
        current = self.ledger_service.status(ledger_ref, expected.transition_ref)
        if current != expected:
            raise PaperAuthorityInvalid(
                "claim ledger changed during publication",
                finding_kind="argument-ledger-changed",
            )
        return current

    def _require_same_map(
        self, map_ref: ArtifactRef, expected: ArgumentMap
    ) -> ArgumentMap:
        current = self._load(map_ref)
        if current != expected:
            raise PaperIntegrityInvalid(
                "argument map changed during validation",
                finding_kind="argument-map-reconstruction-mismatch",
            )
        return current

    @staticmethod
    def _validate_candidate(candidate: ArgumentMapCandidate) -> ArgumentMapCandidate:
        try:
            return ArgumentMapCandidate.model_validate(
                candidate.model_dump(mode="python")
            )
        except ValidationError as exc:
            raise PaperSupportInvalid(
                "argument map candidate is invalid",
                finding_kind="argument-candidate-invalid",
            ) from exc

    @staticmethod
    def _materialize(
        ledger_ref: ArtifactRef,
        ledger: ClaimEvidenceLedger,
        candidate: ArgumentMapCandidate,
    ) -> ArgumentMap:
        nodes = tuple(
            sorted(
                (
                    node.model_copy(update={"claim_ids": tuple(sorted(node.claim_ids))})
                    if node.node_type == "empirical-claim"
                    else node
                    for node in candidate.nodes
                ),
                key=lambda node: node.node_id,
            )
        )
        edges = tuple(
            sorted(
                candidate.edges,
                key=lambda edge: (edge.source_id, edge.target_id, edge.edge_type),
            )
        )
        argument_map = ArgumentMap(
            schema_version="paper.argument-map.v1",
            map_id=f"argument-map-{ledger_ref.content_hash[:12]}",
            producer="paper-builder-argument-map-v1",
            ledger_ref=ledger_ref,
            transition_ref=ledger.transition_ref,
            nodes=nodes,
            edges=edges,
        )
        validate_argument_map(
            argument_map,
            ledger_claim_ids=frozenset(claim.claim_id for claim in ledger.claims),
        )
        return argument_map

    def _current(self) -> ArtifactRef | None:
        try:
            return self.registry.current(ARGUMENT_MAP_SUBJECT)
        except (OSError, ValueError, ValidationError) as exc:
            raise PaperIntegrityInvalid(
                "argument map current pointer is invalid",
                finding_kind="argument-map-current-invalid",
            ) from exc

    def _load(self, reference: ArtifactRef) -> ArgumentMap:
        relative = (
            Path("exit/objects")
            / reference.artifact_id
            / f"v{reference.artifact_version}-{reference.content_hash}.json"
        )
        try:
            data = self.registry.files.read(relative)
            if hashlib.sha256(data).hexdigest() != reference.content_hash:
                raise ValueError("argument map content hash mismatch")
            argument_map = ArgumentMap.model_validate_json(data)
        except (OSError, ValueError, ValidationError) as exc:
            raise PaperIntegrityInvalid(
                "argument map bytes are invalid",
                finding_kind="argument-map-bytes-invalid",
            ) from exc
        expected_id = f"argument-map-{argument_map.ledger_ref.content_hash[:12]}"
        if (
            reference.artifact_version != 1
            or reference.artifact_id != argument_map.map_id
            or argument_map.map_id != expected_id
        ):
            raise PaperIntegrityInvalid(
                "argument map reference identity is invalid",
                finding_kind="argument-map-identity-invalid",
            )
        if data != argument_map.model_dump_json().encode():
            raise PaperIntegrityInvalid(
                "argument map bytes are not canonical",
                finding_kind="argument-map-bytes-noncanonical",
            )
        return argument_map

    def _restore_current(
        self, *, previous: ArtifactRef | None, installed: ArtifactRef
    ) -> None:
        try:
            self.registry.restore_current_if_unchanged(
                ARGUMENT_MAP_SUBJECT,
                installed=installed,
                previous=previous,
            )
        except (OSError, ValueError) as exc:
            raise PaperIntegrityInvalid(
                "argument map current rollback failed",
                finding_kind="argument-map-rollback-failed",
            ) from exc


__all__ = [
    "ARGUMENT_MAP_SUBJECT",
    "ArgumentMapService",
    "validate_argument_map",
]
