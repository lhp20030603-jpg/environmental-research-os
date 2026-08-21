"""Exact canonical protocol loading and disposable real-service dispatch."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from envresearch.models.artifact import ArtifactRef
from envresearch.personal_validation import canonical_handoff
from envresearch.personal_validation._strict import ReviewerBehavioralContract
from envresearch.personal_validation.case_challenge import (
    PaperCaseServices,
    run_evidence_challenge_case,
)
from envresearch.personal_validation.case_stops import (
    ProtocolPolicyArtifacts,
    case_namespace,
    load_policies,
    physical_attempt_roots,
    require_disposable_roots,
    require_no_oracle_leak,
    run_correct_stop_case,
    snapshot_exclusion_trees,
    verify_input_entries,
)
from envresearch.personal_validation.case_success import (
    ResearchCaseServices,
    run_incompatibility_case,
    run_success_case,
)
from envresearch.personal_validation.contracts import (
    CASE_ORDER,
    PERSONAL_ATTEMPT_ROOTS_V1,
    ExpectedBehaviorContract,
    InputSnapshot,
    PersonalCanonicalCase,
    PersonalValidationProtocol,
)
from envresearch.personal_validation.errors import PersonalValidationIntegrityInvalid
from envresearch.personal_validation.private_store import PersonalValidationStore
from envresearch.personal_validation.roots import PersonalPinnedRoot, RootExclusionSet
from envresearch.personal_validation.service import (
    PersonalValidationService,
    PreparedAttempt,
)
from envresearch.personal_validation.snapshots import snapshot_roots
from envresearch.personal_validation.targets import model_ref, personal_session

DEFAULT_PROTOCOL_ROOT = (
    Path(__file__).resolve().parents[3] / "benchmarks/personal-validation/v1"
)
DEFAULT_REPOSITORY_ROOT = DEFAULT_PROTOCOL_ROOT.parents[2]
CANONICAL_CASE_ORDER = CASE_ORDER
_ExpectedAdapter: TypeAdapter[ExpectedBehaviorContract] = TypeAdapter(
    ExpectedBehaviorContract
)
_PINNED_PROTOCOL_V1 = ArtifactRef(
    artifact_id=(
        "personal-protocol-"
        "547c557dac90a854583f041e4aae4a2e84af49baced2331c5187eab947f9f19b"
    ),
    artifact_version=1,
    content_hash="7c21ab93bfd8d172fb64a0ab82949e9be5ab5a72c7b89a131f61adc42229531d",
)
_CANONICAL_TIME = datetime(2026, 8, 21, tzinfo=UTC)


def _canonical_clock() -> datetime:
    return _CANONICAL_TIME


@dataclass(frozen=True, slots=True)
class LoadedProtocol:
    protocol_ref: ArtifactRef
    protocol: PersonalValidationProtocol
    cases: tuple[PersonalCanonicalCase, ...]
    input_snapshots: tuple[InputSnapshot, ...]
    expected_behaviors: tuple[ExpectedBehaviorContract, ...]
    reviewer_contracts: tuple[ReviewerBehavioralContract, ...]
    policy_artifacts: ProtocolPolicyArtifacts

    def input_ref(self, kind: str) -> ArtifactRef:
        try:
            return self.cases[CANONICAL_CASE_ORDER.index(kind)].input_snapshot_ref
        except ValueError as error:
            raise KeyError(kind) from error


@dataclass(frozen=True, slots=True)
class DisposableAttemptRoots:
    """All writers and exact roots for one disposable case execution."""

    case_root: Path
    repository_root: Path
    protocol_ref: ArtifactRef
    store: PersonalValidationStore
    exclusions: RootExclusionSet
    session_nonce: str
    system_snapshot_ref: ArtifactRef
    case_namespace: Path
    research_design: Path
    research_citation: Path
    v03: Path
    v031: Path
    paper_root: Path
    factory_root: Path
    local_analysis: Path
    citation_control: Path
    valuation_control: Path
    clock: Callable[[], datetime]
    research: ResearchCaseServices
    paper: PaperCaseServices
    _case_pin: PersonalPinnedRoot
    _root_pins: tuple[tuple[str, PersonalPinnedRoot], ...]

    @classmethod
    def for_case(
        cls,
        *,
        case_ref: ArtifactRef,
        repository_root: Path,
        protocol_ref: ArtifactRef,
        store: PersonalValidationStore,
        exclusions: RootExclusionSet,
        session_nonce: str,
        system_snapshot_ref: ArtifactRef,
        clock: Callable[[], datetime] = _canonical_clock,
    ) -> DisposableAttemptRoots:
        namespace = case_namespace(session_nonce, protocol_ref, case_ref)
        case_pin = store.root.open_child_root(namespace, private=True, create=True)
        physical = physical_attempt_roots()
        pins: list[tuple[str, PersonalPinnedRoot]] = []
        try:
            for name in PERSONAL_ATTEMPT_ROOTS_V1:
                pins.append(
                    (
                        name,
                        case_pin.open_child_root(
                            physical[name], private=True, create=True
                        ),
                    )
                )
            paths = {name: pin.lexical_path for name, pin in pins}
            research = ResearchCaseServices(
                paths["research-design"], paths["factory"], clock
            )
            paper = PaperCaseServices(
                repository_root=repository_root.resolve(strict=True),
                analysis_root=paths["local-analysis"],
                paper_root=paths["paper"],
                citation_root=paths["research-citation"],
                citation_control_root=paths["citation-control"],
            )
        except BaseException:
            for _, pin in reversed(pins):
                pin.close()
            case_pin.close()
            raise
        return cls(
            case_root=case_pin.lexical_path,
            repository_root=repository_root.resolve(strict=True),
            protocol_ref=protocol_ref,
            store=store,
            exclusions=exclusions,
            session_nonce=session_nonce,
            system_snapshot_ref=system_snapshot_ref,
            case_namespace=namespace,
            research_design=paths["research-design"],
            research_citation=paths["research-citation"],
            v03=paths["v03"],
            v031=paths["v031"],
            paper_root=paths["paper"],
            factory_root=paths["factory"],
            local_analysis=paths["local-analysis"],
            citation_control=paths["citation-control"],
            valuation_control=paths["valuation-control"],
            clock=clock,
            research=research,
            paper=paper,
            _case_pin=case_pin,
            _root_pins=tuple(pins),
        )

    @property
    def logical_roots(self) -> dict[str, Path]:
        return {
            "research-design": self.research_design,
            "research-citation": self.research_citation,
            "v03": self.v03,
            "v031": self.v031,
            "paper": self.paper_root,
            "factory": self.factory_root,
            "local-analysis": self.local_analysis,
            "citation-control": self.citation_control,
            "valuation-control": self.valuation_control,
        }

    def close(self) -> None:
        self.research.close()
        for _, pin in reversed(self._root_pins):
            pin.close()
        self._case_pin.close()


@dataclass(frozen=True, slots=True)
class CaseExecutionContext:
    session_ref: ArtifactRef
    case: PersonalCanonicalCase
    roots: DisposableAttemptRoots
    paper: PaperCaseServices
    research: ResearchCaseServices


def load_protocol_v1(
    root: Path = DEFAULT_PROTOCOL_ROOT,
    repository_root: Path = DEFAULT_REPOSITORY_ROOT,
) -> LoadedProtocol:
    """Reopen every protocol and declared source byte before accepting v1."""
    try:
        protocol_bytes, protocol = _read_model(
            root / "protocol.json", PersonalValidationProtocol
        )
        protocol_ref = _content_ref(protocol.protocol_id, protocol_bytes)
        if protocol_ref != _PINNED_PROTOCOL_V1:
            raise ValueError("personal protocol v1 bytes differ from pinned semantics")
        cases: list[PersonalCanonicalCase] = []
        inputs: list[InputSnapshot] = []
        expected: list[ExpectedBehaviorContract] = []
        reviewers: list[ReviewerBehavioralContract] = []
        for binding in protocol.cases:
            case_root = root / binding.kind
            case_bytes, case = _read_model(
                case_root / "case.json", PersonalCanonicalCase
            )
            _require_ref(binding.case_ref, case.case_id, case_bytes)
            if case.kind != binding.kind:
                raise ValueError("protocol case binding kind differs from case")
            input_bytes, snapshot = _read_model(case_root / "input.json", InputSnapshot)
            _require_ref(case.input_snapshot_ref, snapshot.snapshot_id, input_bytes)
            verify_input_entries(snapshot, root, repository_root)
            expected_bytes, behavior = _read_expected(
                case_root / "expected-behavior.json"
            )
            _require_ref(
                case.expected_behavior_ref, behavior.behavior_id, expected_bytes
            )
            reviewer_bytes, reviewer = _read_model(
                case_root / "reviewer-contract.json", ReviewerBehavioralContract
            )
            _require_ref(
                case.reviewer_contract_ref, reviewer.contract_id, reviewer_bytes
            )
            if behavior.case_kind != case.kind or reviewer.case_kind != case.kind:
                raise ValueError("case transitive contract kind differs")
            require_no_oracle_leak(reviewer_bytes, behavior, case.expected_behavior_ref)
            cases.append(case)
            inputs.append(snapshot)
            expected.append(behavior)
            reviewers.append(reviewer)
        if tuple(case.kind for case in cases) != CANONICAL_CASE_ORDER:
            raise ValueError("personal protocol case order is invalid")
        return LoadedProtocol(
            protocol_ref,
            protocol,
            tuple(cases),
            tuple(inputs),
            tuple(expected),
            tuple(reviewers),
            load_policies(root, protocol),
        )
    except PersonalValidationIntegrityInvalid:
        raise
    except (OSError, TypeError, ValueError, ValidationError) as error:
        raise PersonalValidationIntegrityInvalid(
            "personal protocol byte closure is invalid", finding_kind="protocol-invalid"
        ) from error


def run_case(case_ref: ArtifactRef, roots: DisposableAttemptRoots) -> PreparedAttempt:
    """Execute real services under the exact Personal session lock, then prepare."""
    loaded = load_protocol_v1(repository_root=roots.repository_root)
    if roots.protocol_ref != loaded.protocol_ref:
        raise PersonalValidationIntegrityInvalid(
            "personal protocol reference differs from canonical v1",
            finding_kind="protocol-invalid",
        )
    canonical_handoff.publish_protocol_handoff(roots.store, loaded)
    bindings = {item.case_ref: item.kind for item in loaded.protocol.cases}
    if case_ref not in bindings:
        raise PersonalValidationIntegrityInvalid(
            "personal case reference is not canonical v1",
            finding_kind="protocol-invalid",
        )
    case = roots.store.load(case_ref, PersonalCanonicalCase)
    if case.kind != bindings[case_ref]:
        raise PersonalValidationIntegrityInvalid(
            "personal case object differs from its protocol binding",
            finding_kind="protocol-invalid",
        )
    require_disposable_roots(roots)
    excluded_before = snapshot_exclusion_trees(roots.exclusions)
    session = personal_session(roots.session_nonce, roots.protocol_ref, loaded.protocol)
    session_ref = model_ref(session.session_id, session)
    context = CaseExecutionContext(
        session_ref, case, roots, roots.paper, roots.research
    )
    inspection = None
    run_ref = None
    with roots.store.session_lock(session.session_id):
        if case.kind == "correct-stop":
            inspection = run_correct_stop_case(context)
        else:
            runners = {
                "successful-end-to-end": run_success_case,
                "data-method-incompatibility": run_incompatibility_case,
                "evidence-citation-challenge": run_evidence_challenge_case,
            }
            run_ref = runners[case.kind](context)
        require_disposable_roots(roots)
        if snapshot_exclusion_trees(roots.exclusions) != excluded_before:
            raise ValueError("authoritative exclusion tree changed during case")
        inventory = snapshot_roots(roots.logical_roots)
        inventory_ref = roots.store.publish(inventory.inventory_id, inventory)
        inspection_ref = (
            roots.store.publish(_inspection_id(inspection), inspection)
            if inspection is not None
            else None
        )
    factory = (
        None
        if run_ref is None
        else roots.research.factory_service(roots.paper.release_service)
    )
    validation = PersonalValidationService(
        store=roots.store,
        factory_service=factory,
        session_nonce=roots.session_nonce,
        system_snapshot_ref=roots.system_snapshot_ref,
        attempt_inventory_ref=inventory_ref,
    )
    if inspection is not None and inspection_ref is not None:
        prepared = validation.prepare_correct_stop(
            roots.protocol_ref, case_ref, inspection_ref, inspection
        )
    else:
        if run_ref is None:
            raise RuntimeError("completed canonical case produced no Factory run")
        prepared = validation.prepare_existing_run(
            roots.protocol_ref, case_ref, run_ref
        )
    canonical_handoff.publish_case_source_handoff(
        roots.store,
        roots,
        prepared.attempt_ref,
    )
    return prepared


def _read_model(path: Path, model: type[BaseModel]) -> tuple[bytes, Any]:
    data = path.read_bytes()
    payload = model.model_validate_json(data)
    if data != payload.model_dump_json().encode():
        raise ValueError(f"{path.name} is not canonical model JSON")
    return data, payload


def _read_expected(path: Path) -> tuple[bytes, ExpectedBehaviorContract]:
    data = path.read_bytes()
    payload = _ExpectedAdapter.validate_json(data, strict=False)
    if data != payload.model_dump_json().encode():
        raise ValueError("expected behavior is not canonical model JSON")
    strict = _ExpectedAdapter.validate_python(
        payload.model_dump(mode="python", round_trip=True), strict=True
    )
    return data, strict


def _content_ref(identity: str, data: bytes) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=identity,
        artifact_version=1,
        content_hash=hashlib.sha256(data).hexdigest(),
    )


def _require_ref(reference: ArtifactRef, identity: str, data: bytes) -> None:
    if reference != _content_ref(identity, data):
        raise ValueError("protocol artifact reference does not bind exact bytes")


def _inspection_id(inspection: Any) -> str:
    data = inspection.model_dump_json().encode()
    return f"personal-stop-inspection-{hashlib.sha256(data).hexdigest()}"
