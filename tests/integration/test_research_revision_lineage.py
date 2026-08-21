"""Live descendant lineage must never survive an ancestor revision."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from orchestrator_fixtures import (
    approve,
    broad_brief,
    candidate_payload,
    config,
    estimand,
    literature,
    memo_candidate,
    methods,
    plan,
    ready_for_final_gate,
    revision_capability,
    safe_feasibility,
    submit,
)

from envresearch.models.design import DesignReviewPayload
from envresearch.models.evidence import EvidenceRow
from envresearch.models.intake import ResearchIntakeMode
from envresearch.research.orchestrator import ResearchOrchestrator


def _ready_with_estimand_order(root: Path) -> ResearchOrchestrator:
    orchestrator = ResearchOrchestrator()
    orchestrator.initialize(config(root, ResearchIntakeMode.BROAD_TOPIC), broad_brief())
    submit(orchestrator, "frame-charters", candidate_payload())
    orchestrator.advance()
    approve(orchestrator, "gate-1", selected_candidate_id="charter-air")
    orchestrator.advance()
    submit(orchestrator, "map-literature", literature())
    submit(orchestrator, "inspect-data", safe_feasibility())
    orchestrator.advance()
    assert (root / "work-orders/define-estimand.json").exists()
    return orchestrator


def _candidate(root: Path) -> Path:
    path = root / "incoming/estimand-spec.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps(estimand().model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    return path


def test_revision_retires_issued_descendant_and_rejects_stale_order(
    tmp_path: Path,
) -> None:
    """Removing live-order traversal would let superseded inputs be accepted."""
    orchestrator = _ready_with_estimand_order(tmp_path)
    stale = orchestrator.queue.read_order("define-estimand")

    revision = orchestrator.request_revision(
        "map-literature",
        reason="Reverse the evidence finding",
        actor="researcher",
        principal_capability=revision_capability(orchestrator),
    )

    assert "define-estimand" in revision.affected_nodes
    assert not (tmp_path / "work-orders/define-estimand.json").exists()
    with pytest.raises((FileNotFoundError, ValueError), match="order|superseded"):
        orchestrator.queue.submit(
            "define-estimand",
            _candidate(tmp_path),
            expected_order_hash=stale.order_hash,
        )

    changed = literature().model_copy(
        update={
            "evidence_rows": (
                EvidenceRow(
                    evidence_id="evidence-1",
                    source_id="paper-1",
                    finding="Exposure increased after implementation.",
                    relevance="Reverses the prior finding for the same evidence ID.",
                    evidence_reason="Adversarial local fixture update.",
                ),
            )
        }
    )
    submit(orchestrator, "map-literature", changed)
    orchestrator.advance()
    current = orchestrator.queue.read_order("define-estimand")
    assert current.order_hash != stale.order_hash
    assert current.input_artifacts != stale.input_artifacts
    with pytest.raises(ValueError, match="superseded work order"):
        orchestrator.queue.submit(
            "define-estimand",
            _candidate(tmp_path),
            expected_order_hash=stale.order_hash,
        )
    assert orchestrator.advance().phase.value != "complete"

    orchestrator.queue.submit(
        "define-estimand",
        _candidate(tmp_path),
        expected_order_hash=current.order_hash,
    )
    orchestrator.accept_submission("define-estimand")
    current_estimand = orchestrator.lifecycle.current_envelope(
        Path("artifacts/estimand-spec.yaml")
    )
    assert current_estimand.input_artifacts == current.input_artifacts
    orchestrator.advance()
    ref = orchestrator.lifecycle.artifact_ref(Path("artifacts/estimand-spec.yaml"))
    token = (
        f"artifact:{ref.artifact_id}@{ref.artifact_version}#sha256:{ref.content_hash}"
    )
    submit(orchestrator, "rank-methods", methods(token))
    orchestrator.advance()
    submit(orchestrator, "draft-identification", memo_candidate(token))
    orchestrator.advance()
    submit(
        orchestrator,
        "review-design",
        DesignReviewPayload(review_id="review-recomputed", findings=()),
    )
    orchestrator.advance()
    submit(orchestrator, "compose-plan", plan(token))
    orchestrator.advance()
    approve(orchestrator, "final-gate", accepted_major_ids=[])
    assert orchestrator.advance().phase.value == "complete"


def test_acceptance_rechecks_sealed_order_inputs_against_current_dag(
    tmp_path: Path,
) -> None:
    """Dropping the order-input equality check would accept a forged stale order."""
    orchestrator = _ready_with_estimand_order(tmp_path)
    order = orchestrator.queue.read_order("define-estimand")
    orchestrator.queue.submit(
        "define-estimand",
        _candidate(tmp_path),
        expected_order_hash=order.order_hash,
    )
    changed = literature().model_copy(
        update={"synthesis": "The current producer generation changed."}
    )
    current = orchestrator.lifecycle.current_envelope(
        Path("artifacts/literature-map.json")
    )
    orchestrator.lifecycle.supersede(
        Path("artifacts/literature-map.json"),
        revision_id="test-upstream-generation",
        reason="Create a newer producer generation",
        actor="human-reviewer",
    )
    orchestrator.lifecycle.persist_structured(
        Path("artifacts/literature-map.json"),
        changed,
        current.producer,
        current.input_artifacts,
    )

    with pytest.raises(ValueError, match="sealed work order.*current.*inputs"):
        orchestrator.accept_submission("define-estimand")


@pytest.mark.parametrize("empty_transactions", (False, True))
def test_revision_cancels_receipt_persisted_before_public_transaction(
    tmp_path: Path,
    empty_transactions: bool,
) -> None:
    """Requiring a public submission would wedge a crash-persisted receipt."""
    orchestrator = _ready_with_estimand_order(tmp_path)
    order = orchestrator.queue.read_order("define-estimand")
    assert order.principal_assignment is not None
    candidate = estimand().model_dump_json().encode()
    filename = "estimand-spec.yaml"
    relative = Path(
        "worker-submissions/define-estimand/transactions/"
        "estimand-spec.yaml.submission/estimand-spec.yaml"
    )
    orchestrator.queue.control.ensure_receipt(
        order,
        relative,
        candidate,
        order.principal_assignment.producer,
        order.expected_output_schema,
        f"{filename}.submission",
    )
    if empty_transactions:
        partial = tmp_path / "worker-submissions/define-estimand"
        (partial / ".staging").mkdir(parents=True)
        (partial / "transactions").mkdir()

    revision = orchestrator.request_revision(
        "map-literature",
        reason="Cancel partial receipt",
        actor="operator",
        principal_capability=revision_capability(orchestrator),
    )

    cancellation = (
        orchestrator.queue.control.path
        / "revisions"
        / revision.revision_id
        / "cancelled"
        / "define-estimand.json"
    )
    assert cancellation.exists()
    assert not (tmp_path / "work-orders/define-estimand.json").exists()
    orchestrator.close()
    recovered = ResearchOrchestrator()
    recovered.initialize(
        config(tmp_path, ResearchIntakeMode.BROAD_TOPIC), broad_brief()
    )
    assert (tmp_path / "work-orders/map-literature.json").exists()


def test_final_validation_uses_current_revised_memo_lineage(tmp_path: Path) -> None:
    """A fully recomputed memo must replace its pre-revision history generation."""
    orchestrator = ready_for_final_gate(tmp_path)
    memo_path = Path("artifacts/identification-memo.md")
    old_memo = orchestrator.lifecycle.current_envelope(memo_path)
    orchestrator.request_revision(
        "define-estimand",
        reason="Recompute the completed design lineage",
        actor="human-reviewer",
        principal_capability=revision_capability(orchestrator),
    )
    submit(orchestrator, "define-estimand", estimand())
    orchestrator.advance()
    ref = orchestrator.lifecycle.artifact_ref(Path("artifacts/estimand-spec.yaml"))
    token = f"artifact:{ref.artifact_id}@{ref.artifact_version}#sha256:{ref.content_hash}"
    submit(orchestrator, "rank-methods", methods(token))
    orchestrator.advance()
    submit(orchestrator, "draft-identification", memo_candidate(token))
    orchestrator.advance()
    submit(
        orchestrator,
        "review-design",
        DesignReviewPayload(review_id="review-revised-lineage", findings=()),
    )
    orchestrator.advance()
    submit(orchestrator, "compose-plan", plan(token))
    renewed = orchestrator.advance()
    assert renewed.pending_gate_ids == ("final-gate-r2",)
    current_memo = orchestrator.lifecycle.current_envelope(memo_path)
    assert current_memo.artifact_version > old_memo.artifact_version
    assert current_memo.input_artifacts == orchestrator.lifecycle.input_refs(
        orchestrator.semantics.nodes["draft-identification"]
    )

    approve(orchestrator, "final-gate", accepted_major_ids=[])
    assert orchestrator.advance().phase.value == "complete"
    orchestrator.semantics.validate_final()


def test_final_validation_binds_current_memo_to_immutable_history(
    tmp_path: Path,
) -> None:
    """Self-consistent current front matter cannot replace immutable metadata."""
    orchestrator = ready_for_final_gate(tmp_path)
    memo_path = Path("artifacts/identification-memo.md")
    envelope, body = orchestrator.lifecycle.store.read_markdown(memo_path)
    identification = dict(envelope.provenance["identification"])
    identification["estimand_ref"] = "artifact:forged@9#sha256:" + "f" * 64
    forged = envelope.model_copy(
        update={
            "provenance": {**envelope.provenance, "identification": identification}
        }
    )
    orchestrator.lifecycle.store.write_markdown(memo_path, forged, body)
    orchestrator.semantics.validate_lineage()

    with pytest.raises(ValueError, match="memo.*history|history.*memo"):
        orchestrator.semantics.validate_final()
