"""Late authority, coherent forgery, and rollback tests for paper drafts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from paper_draft_integration_fixtures import (
    DraftStack,
    build_stack,
)

from envresearch.models.artifact import ArtifactRef
from envresearch.paper.argument_map import ARGUMENT_MAP_SUBJECT
from envresearch.paper.draft_builder import PAPER_DRAFT_SUBJECT
from envresearch.paper.errors import (
    PaperAuthorityInvalid,
    PaperIntegrityInvalid,
    PaperScopeExceeded,
)
from envresearch.paper.ledger import CLAIM_LEDGER_SUBJECT


def _publish(stack: DraftStack) -> ArtifactRef:
    return stack.draft_service.publish(
        stack.candidate,
        map_ref=stack.map_ref,
        ledger_ref=stack.ledger_ref,
        citation_report_ref=stack.report_ref,
    )


def _status(stack: DraftStack, reference: ArtifactRef):  # type: ignore[no-untyped-def]
    return stack.draft_service.status(
        reference, map_ref=stack.map_ref, ledger_ref=stack.ledger_ref
    )


@pytest.mark.parametrize(
    "subject", (PAPER_DRAFT_SUBJECT, ARGUMENT_MAP_SUBJECT, CLAIM_LEDGER_SUBJECT)
)
def test_status_rejects_stale_draft_map_or_ledger_current(
    tmp_path: Path, subject: str
) -> None:
    stack = build_stack(tmp_path)
    try:
        reference = _publish(stack)
        stack.draft_service.registry.files.unlink(
            Path("exit/current") / f"{subject}.json"
        )

        with pytest.raises(PaperAuthorityInvalid, match="current"):
            _status(stack, reference)
    finally:
        stack.orchestrator.close()


def test_status_fresh_load_rejects_stale_citation_source_generation(
    tmp_path: Path,
) -> None:
    stack = build_stack(tmp_path)
    try:
        reference = _publish(stack)
        source_path = stack.case_root / "curator-source-sheet.yaml"
        payload = yaml.safe_load(source_path.read_text(encoding="utf-8"))
        payload["source_generation"] = 2
        source_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

        with pytest.raises(PaperAuthorityInvalid, match="citation|source"):
            _status(stack, reference)
    finally:
        stack.orchestrator.close()


@pytest.mark.parametrize("anchor", ("source", "report"))
@pytest.mark.parametrize("fault", ("mac", "canonical"))
def test_protected_attestation_byte_corruption_is_integrity(
    tmp_path: Path, anchor: str, fault: str
) -> None:
    stack = build_stack(tmp_path)
    try:
        reference = _publish(stack)
        if anchor == "source":
            path = (
                stack.orchestrator.queue.control_root
                / "citation-attestations/sources/00000001.json"
            )
        else:
            path = (
                stack.orchestrator.queue.control_root
                / "citation-attestations/reports"
                / (
                    f"{stack.report_ref.artifact_version:08d}-"
                    f"{stack.report_ref.content_hash}.json"
                )
            )
        data = path.read_bytes()
        path.chmod(0o600)
        if fault == "canonical":
            path.write_bytes(b" " + data)
        else:
            payload = json.loads(data)
            payload["mac"] = ("0" if payload["mac"][0] != "0" else "1") + payload[
                "mac"
            ][1:]
            path.write_text(
                json.dumps(payload, separators=(",", ":"), sort_keys=True),
                encoding="utf-8",
            )

        with pytest.raises(PaperIntegrityInvalid) as raised:
            _status(stack, reference)

        assert raised.value.code == "PAPER_INTEGRITY_INVALID"
    finally:
        stack.orchestrator.close()


def test_status_rejects_mutated_immutable_draft_bytes(tmp_path: Path) -> None:
    stack = build_stack(tmp_path)
    try:
        reference = _publish(stack)
        path = (
            stack.draft_service.registry.root
            / "exit/objects"
            / reference.artifact_id
            / f"v1-{reference.content_hash}.json"
        )
        data = path.read_bytes()
        path.chmod(0o600)
        path.write_bytes(
            data.replace(b"Registered environmental", b"Tampered environmental")
        )

        with pytest.raises(PaperIntegrityInvalid, match="current pointer|bytes"):
            _status(stack, reference)
    finally:
        stack.orchestrator.close()


@pytest.mark.parametrize("fault", ("number", "strength", "policy"))
def test_status_rejects_coherently_resealed_scope_forgery(
    tmp_path: Path, fault: str
) -> None:
    stack = build_stack(tmp_path)
    try:
        reference = _publish(stack)
        draft = _status(stack, reference)
        if fault == "number":
            result = draft.paragraphs[3]
            changed = result.model_copy(
                update={"text": result.text.replace("20 USD", "21 USD")}
            )
            forged = draft.model_copy(
                update={
                    "paragraphs": (
                        *draft.paragraphs[:3],
                        changed,
                        *draft.paragraphs[4:],
                    )
                }
            )
        elif fault == "strength":
            binding = next(
                item for item in draft.claim_bindings if item.purpose == "finding"
            )
            changed = binding.model_copy(update={"allowed_strength": "descriptive"})
            forged = draft.model_copy(
                update={
                    "claim_bindings": tuple(
                        changed if item == binding else item
                        for item in draft.claim_bindings
                    )
                }
            )
        else:
            title = draft.paragraphs[0].model_copy(
                update={"text": "Policymakers must adopt this valuation."}
            )
            forged = draft.model_copy(
                update={"paragraphs": (title, *draft.paragraphs[1:])}
            )
        forged_ref = stack.draft_service.registry.publish(draft.draft_id, forged)
        stack.draft_service.registry.set_current(PAPER_DRAFT_SUBJECT, forged_ref)

        with pytest.raises(PaperScopeExceeded):
            _status(stack, forged_ref)
    finally:
        stack.orchestrator.close()


def test_after_promotion_authority_failure_rolls_back_and_retry_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = build_stack(tmp_path)
    original = stack.citation_authority.reopen
    calls = 0

    def fail_after_promotion(reference: ArtifactRef):  # type: ignore[no-untyped-def]
        nonlocal calls
        snapshot = original(reference)
        calls += 1
        if calls == 4:
            raise PaperAuthorityInvalid(
                "injected late citation mutation", finding_kind="citation-race"
            )
        return snapshot

    try:
        monkeypatch.setattr(stack.citation_authority, "reopen", fail_after_promotion)
        with pytest.raises(PaperAuthorityInvalid, match="late citation"):
            _publish(stack)
        assert stack.draft_service.registry.current(PAPER_DRAFT_SUBJECT) is None

        monkeypatch.setattr(stack.citation_authority, "reopen", original)
        reference = _publish(stack)
        assert _status(stack, reference).draft_id == reference.artifact_id
    finally:
        stack.orchestrator.close()


def test_rollback_never_overwrites_a_newer_current_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = build_stack(tmp_path)
    original = stack.citation_authority.reopen
    calls = 0
    replacement_ref: ArtifactRef | None = None

    def replace_then_fail(reference: ArtifactRef):  # type: ignore[no-untyped-def]
        nonlocal calls, replacement_ref
        snapshot = original(reference)
        calls += 1
        if calls == 4:
            installed = stack.draft_service.registry.current(PAPER_DRAFT_SUBJECT)
            assert installed is not None
            draft = stack.draft_service.store.load(installed)
            replacement_ref = stack.draft_service.registry.publish(
                "external-draft-pointer", draft
            )
            stack.draft_service.registry.set_current(
                PAPER_DRAFT_SUBJECT, replacement_ref
            )
            raise PaperAuthorityInvalid(
                "injected newer pointer", finding_kind="citation-race"
            )
        return snapshot

    try:
        monkeypatch.setattr(stack.citation_authority, "reopen", replace_then_fail)
        with pytest.raises(PaperAuthorityInvalid, match="newer pointer"):
            _publish(stack)

        assert replacement_ref is not None
        assert (
            stack.draft_service.registry.current(PAPER_DRAFT_SUBJECT) == replacement_ref
        )
    finally:
        stack.orchestrator.close()
