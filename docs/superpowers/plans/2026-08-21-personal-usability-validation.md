# Personal Usability Validation Implementation Plan

> **Execution status (2026-08-21):** Tasks 1–6 are implemented. Task 6 closed
> the known review-authority defects through commit `e54e3a1`; final independent
> scoped review returned C0/I0/M0 and APPROVE. The project owner intentionally stopped
> Tasks 7–10 to publish the already usable Research, Econometrics, Paper, and
> Factory workflow sooner. Those tasks are archived future enhancements, not
> requirements for the Personal Pilot public preview.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a nonblocking, four-case Personal Usability Validation workflow that produces exact three-Agent advisory reports and owner-approved repair evidence without changing formal product authority or claiming scientific release.

**Architecture:** Add a separate `envresearch.personal_validation` adapter over exact Factory and Research read interfaces. Existing governed targets are reopened read-only; new canonical runs execute only inside disposable roots beneath an explicit owner-private validation root. Descriptor-pinned canonical objects and authenticated events persist attempts, assignments, reviews, reports, approvals, and closures while `status` and `report` remain strictly zero-write.

**Tech Stack:** Python 3.12, Pydantic v2 strict/frozen models, Typer deterministic JSON CLI, `uv`, descriptor-relative filesystem primitives, `ExitRegistry` canonical object identities, `SecureJournal`, Pytest/TDD, Ruff, Mypy.

**Spec:** `docs/superpowers/specs/2026-08-21-personal-usability-validation-design.md`

## Global Constraints

- Scope is literal `personal-advisory-only`; validation never disables the system or a method.
- `blocks=()`, `hidden_evaluation_status="not-run"`, and `product_release_status="scientific_release_pending"` are immutable in every protocol/report.
- Do not call, modify, or weaken the formal blind `ReleaseEvaluator`.
- Do not acquire a real dataset, execute external writes, upload unpublished material, pay, message, or mutate Zotero.
- Existing governed targets use `create=False` and strictly read-only status/inspection; they never assemble, recover, or publish.
- New canonical run/rerun writes are confined to disposable per-attempt roots beneath the private validation root and can never become product authority roots.
- Scientific and Evidence reviewers receive neither the full oracle nor seeded defect locations, prior reviews/findings, or predecessor repair answers.
- Findings are immutable and non-compensatory; no aggregate score or release verdict exists.
- Only a service-authenticated `RepairClosure` over a successor attempt, its complete three-Agent report, a typed case evaluation, and the required four-case regression evidence can resolve an Important/Critical finding.
- Network/Zotero access is local-first, read-only, allowlisted, provenance-bound, and may be invoked by Reviewer Agents only when necessary.
- Every changed Python or R file remains at or below 400 lines.
- Use TDD: witness each RED before production edits, run focused GREEN, request independent review, then commit.

## File and responsibility map

- `src/envresearch/storage/secure_journal.py`: backward-compatible create/recovery journal plus strict existing/read-only construction.
- `src/envresearch/storage/secure_journal_open.py`: existing key/directory/lock/head opening policy, kept out of the near-cap main journal file.
- `src/envresearch/storage/secure_journal_verify.py`: never-reconcile head verification and incomplete/corrupt classification.
- `src/envresearch/econometrics/_store_files.py`: optional borrowed `PinnedRoot` backend for descriptor-relative canonical objects.
- `src/envresearch/econometrics/exit_registry.py`: `from_pinned()` construction and descriptor-relative lock use without changing current Factory semantics.
- `src/envresearch/research/stop_contracts.py`: strict `ResearchStopInspection` contracts.
- `src/envresearch/research/stop_inspection.py`: research-owned read-only terminal reconstruction.
- `src/envresearch/personal_validation/_strict.py`: shared strict nested-model and canonical-ID helpers.
- `src/envresearch/personal_validation/contracts.py`: protocol, snapshots, case, inventory, targets, and attempts.
- `src/envresearch/personal_validation/review_contracts.py`: typed role projections, assignments, dispatch receipts, raw finding responses, reviews, review publications, findings, external access, case evaluations, and report.
- `src/envresearch/personal_validation/repair_contracts.py`: executable change operations, protected scientific invariants, owner decision, proposal, approval, verified finding resolution, protocol regression, and closure.
- `src/envresearch/personal_validation/errors.py`: stable typed public error boundary.
- `src/envresearch/personal_validation/private_store.py`: pinned object registry, explicit journal roots, event DAG, and zero-write reopen.
- `src/envresearch/personal_validation/snapshots.py`: exact input/system/root inventory construction and comparison.
- `src/envresearch/personal_validation/targets.py`: completed Factory and correct-stop target reconstruction.
- `src/envresearch/personal_validation/review_bundle.py`: role-safe bundle projections and oracle withholding.
- `src/envresearch/personal_validation/reviews.py`: assignment issuance and strict returned-review ingestion.
- `src/envresearch/personal_validation/report.py`: deterministic case evaluation, authenticated complete-union reduction, and immutable report publication.
- `src/envresearch/personal_validation/repairs.py`: proposal/approval/application/closure ordering.
- `src/envresearch/personal_validation/canonical_cases.py`: four versioned built-in case manifests and runners.
- `src/envresearch/personal_validation/service.py`: public orchestration facade over injected services.
- `src/envresearch/personal_validation/cli.py`: reference-only deterministic JSON commands.
- `src/envresearch/cli_groups.py`: root Typer group registration extracted from the current 400-line `cli.py`.
- `benchmarks/personal-validation/v1/**`: immutable inputs and expected-behavior manifests for exactly four cases.
- `tests/unit/test_secure_journal_read_only.py`: strict zero-write journal tests.
- `tests/unit/test_exit_registry_pinned.py`: descriptor-pinned object/lock tests.
- `tests/unit/test_personal_validation_contracts.py`: protocol/snapshot/target/attempt tests.
- `tests/unit/test_personal_validation_review_reducer.py`: assignment/review/report reducer tests; the unique basename keeps the repository-wide default Pytest collection valid.
- `tests/unit/test_personal_validation_repairs.py`: approval/closure tests.
- `tests/integration/test_research_stop_inspection.py`: zero-write correct-stop reconstruction.
- `tests/integration/personal_validation_fixtures.py`: private-root and connected canonical fixture composition.
- `tests/integration/test_personal_validation_cases.py`: four canonical behaviors.
- `tests/integration/test_personal_validation_authority.py`: product-root preservation and root-swap attacks.
- `tests/integration/test_personal_validation_recovery.py`: crash/orphan/event replay.
- `tests/integration/test_personal_validation_reviews.py`: three-role review lifecycle and oracle leakage.
- `tests/integration/test_personal_validation_repairs.py`: explicit approval and successor closure.
- `tests/integration/test_personal_validation_cli.py`: deterministic reference-only CLI.
- `docs/personal-usability-validation-operator-guide.md`: one-command operator and recovery guide.

---

### Task 1: Descriptor-pinned canonical objects and strict read-only journals

**Files:**
- Modify: `src/envresearch/workers/filesystem.py`
- Modify: `src/envresearch/econometrics/_store_files.py`
- Modify: `src/envresearch/econometrics/exit_registry.py`
- Modify: `src/envresearch/storage/secure_journal.py`
- Create: `src/envresearch/storage/secure_journal_open.py`
- Create: `src/envresearch/storage/secure_journal_verify.py`
- Modify: `src/envresearch/storage/secure_journal_lock.py`
- Modify: `src/envresearch/storage/secure_journal_files.py`
- Create: `tests/unit/test_exit_registry_pinned.py`
- Create: `tests/unit/test_secure_journal_read_only.py`

**Interfaces:**
- Consumes: existing `PinnedRoot`, `StoreFiles`, `ExitRegistry`, and `SecureJournal` canonical formats.
- Produces: `PinnedRoot.require_attached()`, descriptor-derived `PinnedRoot.open_child_root(...)`, `StoreFiles.from_pinned(root)`, `ExitRegistry.from_pinned(root, create=...)`, `SecureJournal.create_from_pinned(...)`, strict `SecureJournal.open_existing(..., reconcile=False)`, and the separate writer-only `SecureJournal.open_for_recovery(...)`.

- [ ] **Step 1: Write descriptor-pinned registry REDs**

```python
def test_pinned_registry_does_not_follow_replaced_lexical_root(tmp_path: Path) -> None:
    lexical = tmp_path / "objects"
    pinned = PinnedRoot(lexical, private=True)
    registry = ExitRegistry.from_pinned(pinned, create=True)
    ref = registry.publish("example", Example(value="before"))
    lexical.rename(tmp_path / "original")
    lexical.mkdir(mode=0o700)

    with pytest.raises(ValueError, match="root identity changed"):
        registry.load(ref, Example)
```

```python
def test_pinned_registry_lock_never_reopens_lexical_root(tmp_path: Path) -> None:
    pinned = PinnedRoot(tmp_path / "objects", private=True)
    registry = ExitRegistry.from_pinned(pinned, create=True)
    with registry.lock("personal-session"):
        (tmp_path / "objects").rename(tmp_path / "moved")
        (tmp_path / "objects").mkdir(mode=0o700)
    with pytest.raises(ValueError, match="root identity changed"):
        pinned.require_attached()
```

- [ ] **Step 2: Run the pinned registry REDs**

Run: `uv run pytest tests/unit/test_exit_registry_pinned.py -q`

Expected: collection or attribute failures for `from_pinned()` / `require_attached()`.

- [ ] **Step 3: Implement the borrowed pinned backend without changing legacy semantics**

```python
class PinnedRoot:
    def __enter__(self) -> PinnedRoot:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def require_attached(self) -> None:
        current = os.stat(self.lexical_path, follow_symlinks=False)
        opened = os.fstat(self.fd)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("pinned root identity changed")

    def open_child_root(self, relative: Path, *, private: bool, create: bool) -> PinnedRoot:
        """Open/pin a child strictly relative to this retained descriptor."""
        self.require_attached()
        child = PinnedRoot.from_parent_fd(self.fd, relative, private=private, create=create)
        try:
            self.require_attached()
            child.require_parent(self.fd, relative)
            return child
        except BaseException:
            child.close()
            raise


class StoreFiles:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._pinned: PinnedRoot | None = None

    @classmethod
    def from_pinned(cls, root: PinnedRoot) -> StoreFiles:
        instance = cls(root.path)
        instance._pinned = root
        return instance

    def _parent(self, relative: Path, *, create: bool) -> tuple[int, str]:
        parts = _parts(relative)
        if self._pinned is not None:
            self._pinned.require_attached()
            return self._pinned.open_directory(Path(*parts[:-1]), create=create), parts[-1]
        return self._lexical_parent(parts, create=create)
```

```python
class ExitRegistry:
    @classmethod
    def from_pinned(cls, root: PinnedRoot, *, create: bool) -> ExitRegistry:
        instance = cls.__new__(cls)
        instance.root = root.path
        instance.create = create
        instance.files = StoreFiles.from_pinned(root)
        instance._pinned = root
        if create:
            for path in (Path("exit/objects"), Path("exit/current"), Path("exit/locks")):
                instance.files.ensure_directory(path)
        return instance
```

Use `StoreFiles.open_lock(Path("exit/locks") / f"{subject}.lock", create=self.create)` in `ExitRegistry.lock()` so locks are descriptor-relative. Preserve ordinary `ExitRegistry(root, create=False)` behavior for Factory callers.

- [ ] **Step 4: Write strict journal REDs**

```python
@pytest.mark.parametrize("missing", ["key", "head", "lock", "anchor"])
def test_open_existing_read_all_never_recreates_control_state(
    journal_case: JournalCase, missing: str
) -> None:
    journal_case.remove(missing)
    before = journal_case.tree_state()
    with pytest.raises((FileNotFoundError, ValueError)):
        with SecureJournal.open_existing(
            journal_case.path,
            storage_root=journal_case.storage_pin,
            control_root=journal_case.control_pin,
            reconcile=False,
        ) as journal:
            journal.read_all()
    assert journal_case.tree_state() == before
```

```python
def test_lagging_head_is_reported_without_reconciliation(journal_case: JournalCase) -> None:
    journal_case.append_record_without_head({"event_id": "session.00000002"})
    before = journal_case.tree_state()
    with SecureJournal.open_existing(
        journal_case.path,
        storage_root=journal_case.storage_pin,
        control_root=journal_case.control_pin,
        reconcile=False,
    ) as journal:
        with pytest.raises(ValueError, match="recovery required"):
            journal.read_all()
    assert journal_case.tree_state() == before
```

- [ ] **Step 5: Run journal REDs**

Run: `uv run pytest tests/unit/test_secure_journal_read_only.py -q`

Expected: failures because the current constructor creates control state and `read_all()` reconciles a lagging head.

- [ ] **Step 6: Implement strict existing journal construction and verification**

```python
class SecureJournal:
    @classmethod
    def open_existing(
        cls,
        path: Path,
        *,
        storage_root: PinnedRoot,
        control_root: PinnedRoot,
        reconcile: Literal[False] = False,
    ) -> SecureJournal:
        return open_existing_journal(
            cls,
            path=path,
            storage_root=storage_root,
            control_root=control_root,
            reconcile=reconcile,
        )

    def read_all(self) -> list[dict[str, Any]]:
        with self._locked(create_control=False):
            payloads, records, size = self._read_existing_descriptor()
            verify_journal_head_strict(
                expected=self._read_head(),
                actual=self._head_for(records, size),
                records=records,
            )
            return payloads
```

`open_existing_journal()` accepts already-pinned, descriptor-derived storage/control roots; it never reopens either lexical path. It rejects `reconcile=True`, loads an existing single-link owner/0600 key, requires existing control directories/lock/anchor/head, uses an `O_RDONLY` journal descriptor, and sets `_writable=False`. `append`, `append_unique`, and `ensure` raise in read-only mode. The separate `open_for_recovery(...)` has distinct capabilities `can_create_control=False, can_reconcile_head=True`; under an existing writer lock it calls `repair_lagging_head(...)`, never the strict verifier, and may perform only the exact lagging-head repair—never key/lock/anchor creation.

Pinned journal constructors are borrow-only (`_owns_roots=False`); their `close()` releases only journal-owned descriptors. Legacy path construction remains `_owns_roots=True`. The Store is the sole owner of injected top/objects/journals/control pins. Tests prove journal close leaves caller pins usable, Store close invalidates all pins exactly once, and partial failures do not leak/double-close.

Add race REDs that replace the lexical top root between opening each child and between child pinning and journal construction. All operations must continue on the retained descriptors or fail `root identity changed`; no child may be rebound to the replacement tree. `PinnedRoot.__enter__/__exit__` wrap its existing idempotent `close()`. Add double-close, partial-construction cleanup, and repeated context-manager open/close tests proving bounded descriptor count and reverse-order cleanup.

Add `SecureJournal.__enter__() -> SecureJournal` and `__exit__()` delegating to idempotent `close()` so every borrowed descriptor has an explicit bounded lifetime.

- [ ] **Step 7: Run storage GREEN and affected regressions**

Run:

```bash
uv run pytest tests/unit/test_exit_registry_pinned.py tests/unit/test_secure_journal_read_only.py tests/unit/test_exit_registry_current.py tests/integration/test_research_journal_security.py -q
uv run ruff check src/envresearch/workers/filesystem.py src/envresearch/econometrics/_store_files.py src/envresearch/econometrics/exit_registry.py src/envresearch/storage tests/unit/test_exit_registry_pinned.py tests/unit/test_secure_journal_read_only.py
uv run mypy src/envresearch/workers/filesystem.py src/envresearch/econometrics/_store_files.py src/envresearch/econometrics/exit_registry.py src/envresearch/storage
```

Expected: all pass; existing crash-recovery writer behavior remains unchanged.

- [ ] **Step 8: Request independent storage review and commit**

Review must specifically probe root replacement, missing key/head/lock/anchor, lagging head, borrowed-FD lifetime, and unchanged Factory `create=False` behavior.

Commit: `feat(storage): add strict pinned read interfaces`

---

### Task 2: Strict Personal Validation contracts and immutable status reducer

**Files:**
- Create: `src/envresearch/research/stop_contracts.py`
- Create: `src/envresearch/personal_validation/__init__.py`
- Create: `src/envresearch/personal_validation/_strict.py`
- Create: `src/envresearch/personal_validation/contracts.py`
- Create: `src/envresearch/personal_validation/review_contracts.py`
- Create: `src/envresearch/personal_validation/repair_contracts.py`
- Create: `src/envresearch/personal_validation/errors.py`
- Create: `tests/unit/test_personal_validation_contracts.py`
- Create: `tests/unit/test_personal_validation_review_reducer.py`
- Create: `tests/unit/test_personal_validation_repairs.py`

**Interfaces:**
- Consumes: `ArtifactRef`, `ResearchFactoryRun`, and the strict nested-model pattern from `paper.audit_contracts` / `factory.contracts`.
- Produces: research-owned `ResearchStopInspection` contracts; all Personal durable model types; authenticated `reduce_case_state(...)`; deterministic identity/materialization helpers; and stable `PersonalValidationError` subclasses.

- [ ] **Step 1: Write strict contract REDs**

```python
def test_protocol_is_personal_only_and_exactly_four_cases() -> None:
    protocol = protocol_fixture()
    assert protocol.scope == "personal-advisory-only"
    assert protocol.blocks == ()
    assert protocol.hidden_evaluation_status == "not-run"
    assert protocol.product_release_status == "scientific_release_pending"
    assert tuple(case.kind for case in protocol.cases) == (
        "successful-end-to-end",
        "correct-stop",
        "data-method-incompatibility",
        "evidence-citation-challenge",
    )
```

```python
def test_attempt_target_is_strict_discriminated_union() -> None:
    with pytest.raises(ValidationError):
        PersonalValidationAttempt.model_validate(
            {**attempt_payload(), "target": {"target_type": "correct-stop", "no_result": True}}
        )
```

```python
def test_attempt_has_no_forward_bundle_or_completion_reference() -> None:
    fields = PersonalValidationAttempt.model_fields
    assert "review_bundle_ref" not in fields
    assert "completion_event_ref" not in fields
```

- [ ] **Step 2: Run contract REDs**

Run: `uv run pytest tests/unit/test_personal_validation_contracts.py -q`

Expected: collection failure because `envresearch.personal_validation` does not exist.

- [ ] **Step 3: Implement strict foundational models**

Create `research/stop_contracts.py` first. It owns only research-layer evidence (`ResearchCheckpointEvidence`, `ResearchFileEvidence`, and `ResearchStopInspection`) and never imports `personal_validation`. `ResearchStopInspection` binds the exact blocking gate/finding/checkpoint and a research-root-only evidence tuple; the Personal layer separately binds the cross-root `AttemptRootInventory`.

Every ID-bearing content model uses one formula: `prefix + sha256(canonical_json(identity_payload))` with the full 64-hex digest, exposes `identity_payload()`, and validates its supplied ID. Models without an internal ID (`AgentDispatchReceipt`, `ExternalAccessRecord`, `OwnerRepairDecision`, raw response/input models) use their registry-issued canonical `ArtifactRef` as the sole content identity and are always bound by that ref in successors. All `*_sha256` fields use one strict 64-lowercase-hex type. Canonical tuples have named sort keys and reject duplicates. The protocol validator itself—not merely a fixture—requires the four case kinds exactly once in the fixed order. REDs independently tamper every identity component, reorder/duplicate tuples, pass uppercase/short digests, and pass preconstructed forged nested models.

Identity payloads are explicit and versioned: protocol binds ordered case refs plus all policy/schema digests and immutable status literals; case binds kind/input/expected behavior/safe reviewer contract/data boundary/stage/prohibition fields; snapshots/inventories bind their complete sorted entries; session binds a caller-persisted canonical `session_nonce`, protocol, and ordered cases; attempt binds protocol/case/input/system/inventory/target/start/predecessor; assignment binds attempt/bundle/role/policy/invocation/primary publications; dispatch receipt binds assignment/invocation/model/runtime/time; review binds assignment/attempt/bundle/role/policy/receipt/raw response; finding binds the exact response fields plus source review ref; review publication binds assignment/review/sorted finding/access-record refs; evaluation binds case/attempt/expected behavior/target/inventory/verifier/observations/verdict; report binds evaluation, three role-ordered publications, complete findings, and state literals; every repair/protocol-regression object binds all of its declared refs and typed payloads. Prefixes are schema-specific (`personal-protocol-`, `personal-case-`, `personal-attempt-`, `personal-review-`, etc.), never shared or truncated. REDs prove nonce tampering fails, an exact nonce retry converges, and a fresh nonce produces a distinct session.

```python
STRICT = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")
StrictArtifactRef = Annotated[ArtifactRef, BeforeValidator(strict_model_input)]


class PersonalValidationProtocol(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.validation-protocol.v1"]
    protocol_id: str
    protocol_version: Literal["1"]
    cases: tuple[PersonalCanonicalCaseBinding, ...] = Field(min_length=4, max_length=4)
    scientific_policy_sha256: str
    evidence_policy_sha256: str
    synthesis_policy_sha256: str
    rubric_sha256: str
    report_schema_sha256: str
    external_access_policy_sha256: str
    scope: Literal["personal-advisory-only"]
    blocks: tuple[()] = ()
    hidden_evaluation_status: Literal["not-run"]
    product_release_status: Literal["scientific_release_pending"]

    @model_validator(mode="after")
    def require_exact_case_order_and_identity(self) -> PersonalValidationProtocol:
        require_exact_case_order(self.cases)
        require_materialized_id(self.protocol_id, "personal-protocol-", self.identity_payload())
        return self


class InputEntry(BaseModel):
    model_config = STRICT
    logical_name: str
    kind: Literal["file", "directory", "symlink", "submodule"]
    sha256: str | None
    size_bytes: int
    mode: int
    symlink_target: str | None = None


class InputSnapshot(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.input-snapshot.v1"]
    snapshot_id: str
    entries: tuple[InputEntry, ...] = Field(min_length=1)


class SystemSnapshot(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.system-snapshot.v1"]
    snapshot_id: str
    git_commit: str
    execution_tree_sha256: str
    uv_lock_sha256: str
    capability_manifest_sha256: str
    method_profile_sha256: str
    protocol_ref: StrictArtifactRef
    runtime_versions: tuple[tuple[str, str], ...] = Field(min_length=1)
    clean_worktree: bool


class RootInventoryEntry(BaseModel):
    model_config = STRICT
    logical_root: str
    relative_path: str
    kind: Literal["file", "directory", "symlink"]
    sha256: str | None
    size_bytes: int
    owner: int
    mode: int
    link_count: int
    symlink_target: str | None = None


PERSONAL_ATTEMPT_ROOTS_V1 = (
    "research-design",
    "research-citation",
    "v03",
    "v031",
    "paper",
    "factory",
    "local-analysis",
    "citation-control",
    "valuation-control",
)


class RootIdentity(BaseModel):
    model_config = STRICT
    logical_root: str
    device: int
    inode: int
    tree_sha256: str
    entry_count: int


class AttemptRootInventory(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.attempt-root-inventory.v1"]
    inventory_id: str
    root_identities: tuple[RootIdentity, ...] = Field(min_length=len(PERSONAL_ATTEMPT_ROOTS_V1))
    entries: tuple[RootInventoryEntry, ...]


class CompletedFactoryRunTarget(BaseModel):
    model_config = STRICT
    target_type: Literal["completed-factory-run"]
    run_ref: StrictArtifactRef
    run: Annotated[ResearchFactoryRun, BeforeValidator(strict_model_input)]


class CorrectStopTarget(BaseModel):
    model_config = STRICT
    target_type: Literal["correct-stop"]
    inspection_ref: StrictArtifactRef
    inspection: Annotated[ResearchStopInspection, BeforeValidator(strict_model_input)]
    attempt_inventory_ref: StrictArtifactRef


AttemptTarget = Annotated[
    CompletedFactoryRunTarget | CorrectStopTarget,
    BeforeValidator(strict_model_input),
    Field(discriminator="target_type"),
]


class PersonalCanonicalCase(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.canonical-case.v1"]
    case_id: str
    kind: Literal[
        "successful-end-to-end",
        "correct-stop",
        "data-method-incompatibility",
        "evidence-citation-challenge",
    ]
    input_snapshot_ref: StrictArtifactRef
    expected_behavior_ref: StrictArtifactRef
    reviewer_contract_ref: StrictArtifactRef
    required_logical_roots: tuple[str, ...]
    intended_use: str
    data_boundary: Literal["synthetic", "trusted-local"]
    required_factory_stages: tuple[str, ...]
    expected_terminal_kind: Literal["factory-run", "correct-stop"]
    prohibited_outcomes: tuple[str, ...]


class ExpectedObservationRequirement(BaseModel):
    model_config = STRICT
    observation_kind: Literal[
        "factory-chain-coherent",
        "correct-stop-blocker",
        "method-rejected",
        "compatible-method-retained",
        "predecessor-audit-blocked",
        "revision-closure-complete",
        "successor-release-clean",
        "namespace-absent",
    ]
    exact_code: str | None = None
    exact_finding_kind: str | None = None


class SuccessfulRunExpectedBehavior(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.expected-behavior.v1"]
    behavior_id: str
    case_kind: Literal["successful-end-to-end"]
    requirements: tuple[ExpectedObservationRequirement, ExpectedObservationRequirement]
    prohibited_outcomes: tuple[str, ...]


class CorrectStopExpectedBehavior(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.expected-behavior.v1"]
    behavior_id: str
    case_kind: Literal["correct-stop"]
    blocker_code: str
    blocker_finding_kind: str
    blocker_gate: str
    expected_checkpoint_ref: StrictArtifactRef
    expected_checkpoint_sha256: str
    requirements: tuple[ExpectedObservationRequirement, ExpectedObservationRequirement]
    prohibited_outcomes: tuple[str, ...]


class IncompatibilityExpectedBehavior(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.expected-behavior.v1"]
    behavior_id: str
    case_kind: Literal["data-method-incompatibility"]
    rejected_method: Literal["rdd"]
    retained_method: Literal["hedonic"]
    estimand_anchor_ref: StrictArtifactRef
    estimand_sha256: str
    unmet_requirements: tuple[str, ...] = Field(min_length=1)
    requirements: tuple[ExpectedObservationRequirement, ExpectedObservationRequirement]
    prohibited_outcomes: tuple[str, ...]


class EvidenceChallengeExpectedBehavior(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.expected-behavior.v1"]
    behavior_id: str
    case_kind: Literal["evidence-citation-challenge"]
    predecessor_finding_kinds: tuple[str, ...] = Field(min_length=1)
    requirements: tuple[
        ExpectedObservationRequirement,
        ExpectedObservationRequirement,
        ExpectedObservationRequirement,
    ]
    prohibited_outcomes: tuple[str, ...]


ExpectedBehaviorContract = Annotated[
    SuccessfulRunExpectedBehavior
    | CorrectStopExpectedBehavior
    | IncompatibilityExpectedBehavior
    | EvidenceChallengeExpectedBehavior,
    BeforeValidator(strict_model_input),
    Field(discriminator="case_kind"),
]


class ReviewerBehavioralContract(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.reviewer-behavioral-contract.v1"]
    contract_id: str
    case_kind: Literal[
        "successful-end-to-end",
        "correct-stop",
        "data-method-incompatibility",
        "evidence-citation-challenge",
    ]
    review_question: str
    correct_stop_is_valid: Literal[True]
    advisory_only: Literal[True]
    withheld_fields: tuple[str, ...] = Field(min_length=1)


class PersonalCanonicalCaseBinding(BaseModel):
    model_config = STRICT
    case_ref: StrictArtifactRef
    kind: Literal[
        "successful-end-to-end",
        "correct-stop",
        "data-method-incompatibility",
        "evidence-citation-challenge",
    ]


class PersonalValidationSession(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.validation-session.v1"]
    session_id: str
    session_nonce: str
    protocol_ref: StrictArtifactRef
    cases: tuple[PersonalCanonicalCaseBinding, ...] = Field(min_length=4, max_length=4)


class PersonalValidationAttempt(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.validation-attempt.v1"]
    attempt_id: str
    protocol_ref: StrictArtifactRef
    case_ref: StrictArtifactRef
    input_snapshot_ref: StrictArtifactRef
    system_snapshot_ref: StrictArtifactRef
    attempt_inventory_ref: StrictArtifactRef
    target: AttemptTarget
    start_event_id: str
    predecessor_attempt_ref: StrictArtifactRef | None = None
```

Define `PersonalValidationAttempt` with protocol/case/input/system/inventory/target/start-event/predecessor only. Its validator checks run-ref bytes against the embedded run for completed targets and checks correct-stop inventory references without accepting caller assertions. Preparation requires the inventory's sorted logical-root set to equal the case's `required_logical_roots` (the v1 cases use the complete versioned root set), with no missing, duplicate, or unknown root; every `RootIdentity.tree_sha256` and `entry_count` is recomputed from a complete descriptor-relative traversal. REDs remove each required root in turn and truncate each traversal.

- [ ] **Step 4: Write review/reducer REDs**

```python
def test_synthesis_cannot_erase_primary_important_finding() -> None:
    state = reduce_case_state(
        evaluation=evaluation_fixture(verdict="expected-behavior-observed"),
        publications=complete_review_publications(scientific_important=True),
    )
    assert state == "needs-revision"
```

```python
def test_finding_is_immutable_and_has_no_status_field() -> None:
    assert "status" not in PersonalFinding.model_fields
    with pytest.raises(ValidationError):
        PersonalFinding.model_validate({**finding_payload(), "status": "verified-closed"})
```

```python
def test_caller_boolean_or_unverified_closure_cannot_enter_reducer() -> None:
    assert "expected_behavior_observed" not in inspect.signature(reduce_case_state).parameters
    with pytest.raises(TypeError):
        reduce_case_state(evaluation_fixture(), complete_review_publications(), (closure_fixture(),))
```

- [ ] **Step 5: Implement review, finding, report, and repair models**

```python
class ReviewAssignment(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.review-assignment.v1"]
    assignment_id: str
    attempt_ref: StrictArtifactRef
    bundle_ref: StrictArtifactRef
    role: Literal["scientific", "evidence", "synthesis"]
    policy_sha256: str
    invocation_id: str
    primary_publication_refs: tuple[StrictArtifactRef, ...] = ()

    @model_validator(mode="after")
    def require_role_inputs(self) -> ReviewAssignment:
        expected = 2 if self.role == "synthesis" else 0
        if len(self.primary_publication_refs) != expected:
            raise ValueError("review assignment inputs disagree with its role")
        return self


class AgentDispatchReceipt(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.agent-dispatch-receipt.v1"]
    assignment_ref: StrictArtifactRef
    invocation_id: str
    observed_model_id: str
    observed_runtime_id: str
    dispatched_at: AwareDatetime


class AgentDispatchObservation(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.agent-dispatch-observation.v1"]
    invocation_id: str
    observed_model_id: str
    observed_runtime_id: str
    dispatched_at: AwareDatetime


class ExternalAccessResponse(BaseModel):
    model_config = STRICT
    provider: Literal["web", "zotero"]
    operation: Literal[
        "read-citation-metadata",
        "read-authorized-paper",
        "read-official-documentation",
        "read-data-license-provenance",
    ]
    request_sha256: str
    source_locator: str
    authorization_ref: StrictArtifactRef | None = None
    response_sha256: str
    retrieved_at: AwareDatetime
    policy_ref: StrictArtifactRef
    local_finding_keys: tuple[str, ...]


class ExternalAccessRecord(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.external-access-record.v1"]
    review_ref: StrictArtifactRef
    response: Annotated[ExternalAccessResponse, BeforeValidator(strict_model_input)]
    finding_refs: tuple[StrictArtifactRef, ...]


FindingDomain = Literal[
    "research-question-estimand",
    "method-identification",
    "data-compatibility",
    "assumptions-threats",
    "diagnostics-robustness",
    "evidence-numbers-citations",
    "paper-usefulness",
]


class AgentFindingResponse(BaseModel):
    model_config = STRICT
    local_finding_key: str
    domain: FindingDomain
    severity: Literal["minor", "important", "critical"]
    target_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    evidence_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    problem: str
    impact: str
    repair_proposal: str


class AgentReviewResponse(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.agent-review-response.v1"]
    role: Literal["scientific", "evidence", "synthesis"]
    findings: tuple[AgentFindingResponse, ...]
    external_access: tuple[ExternalAccessResponse, ...]
    completion_status: Literal["complete", "external-verification-pending"]


class AgentReview(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.agent-review.v1"]
    review_id: str
    assignment_ref: StrictArtifactRef
    attempt_ref: StrictArtifactRef
    bundle_ref: StrictArtifactRef
    role: Literal["scientific", "evidence", "synthesis"]
    policy_sha256: str
    dispatch_receipt_ref: StrictArtifactRef
    raw_response_sha256: str
    response: AgentReviewResponse


class PersonalFinding(BaseModel):
    model_config = STRICT
    finding_id: str
    domain: FindingDomain
    severity: Literal["minor", "important", "critical"]
    target_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    evidence_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    problem: str
    impact: str
    repair_proposal: str
    source_review_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)


class ReviewPublication(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.review-publication.v1"]
    publication_id: str
    assignment_ref: StrictArtifactRef
    review_ref: StrictArtifactRef
    finding_refs: tuple[StrictArtifactRef, ...]
    external_access_record_refs: tuple[StrictArtifactRef, ...]


class CaseBehaviorObservation(BaseModel):
    model_config = STRICT
    observation_kind: Literal[
        "factory-chain-coherent",
        "correct-stop-blocker",
        "method-rejected",
        "compatible-method-retained",
        "predecessor-audit-blocked",
        "revision-closure-complete",
        "successor-release-clean",
        "namespace-absent",
    ]
    evidence_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    exact_code: str | None = None
    exact_finding_kind: str | None = None


class CaseBehaviorEvaluation(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.case-behavior-evaluation.v1"]
    evaluation_id: str
    case_ref: StrictArtifactRef
    attempt_ref: StrictArtifactRef
    expected_behavior_ref: StrictArtifactRef
    target_ref: StrictArtifactRef
    inventory_ref: StrictArtifactRef
    verifier_version: Literal["personal-case-verifier-v1"]
    observations: tuple[CaseBehaviorObservation, ...] = Field(min_length=1)
    verdict: Literal["expected-behavior-observed", "behavior-deviation"]


class PersonalValidationReport(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.validation-report.v1"]
    report_id: str
    attempt_ref: StrictArtifactRef
    evaluation_ref: StrictArtifactRef
    review_publication_refs: tuple[StrictArtifactRef, StrictArtifactRef, StrictArtifactRef]
    finding_refs: tuple[StrictArtifactRef, ...]
    state: Literal["personal-baseline-passed", "review-required", "needs-revision"]
    scope: Literal["personal-advisory-only"]
    blocks: tuple[()] = ()
    hidden_evaluation_status: Literal["not-run"]
    product_release_status: Literal["scientific_release_pending"]
```

The publication DAG is one-way and fully content-addressable:

`ReviewAssignment -> AgentDispatchReceipt -> canonical raw AgentReviewResponse -> AgentReview -> PersonalFinding(s) -> ExternalAccessRecord(s) -> ReviewPublication -> PersonalValidationReport`.

`AgentReviewResponse` never contains a review ref and the durable `AgentReview` never contains finding refs. Raw findings use unique canonical `local_finding_key` values; raw external-access entries may refer only to those local keys. After `review_ref` exists, the service publishes durable findings, resolves local keys to exact finding refs, publishes durable access records, then publishes `ReviewPublication`. `record_review()` returns `RecordedReview(publication_ref, review_ref, finding_refs, access_record_refs, completion_event_id)`. The report consumes exact `ReviewPublication` refs and reopens every assignment, receipt, review, response hash, finding, and access record. Role bundles are typed discriminated `ScientificProjection | EvidenceProjection | SynthesisProjection`, not arbitrary dictionaries; the role must match the projection and recursive oracle/canary scans operate over the full typed canonical JSON. Model/runtime identity comes only from the orchestration-issued `AgentDispatchReceipt`, never the Agent's self-report.

`repair_contracts.py` defines the exact immutable repair envelope:

```python
SafeRelativePath = Annotated[str, AfterValidator(require_safe_relative_posix_path)]


class CanonicalReplacementBlob(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.replacement-blob.v1"]
    media_type: Literal["application/json", "text/plain"]
    utf8_content: str
    content_sha256: str


class ReplacementOperation(BaseModel):
    model_config = STRICT
    operation_kind: Literal["replace-canonical-file"]
    applicator_version: Literal["personal-replace-file-v1"]
    logical_target: SafeRelativePath
    target_ref: StrictArtifactRef
    before_sha256: str
    replacement_blob_ref: StrictArtifactRef
    after_sha256: str


class RepairOperationsArtifact(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.repair-operations.v1"]
    operations_id: str
    failed_report_ref: StrictArtifactRef
    operations: tuple[ReplacementOperation, ...] = Field(min_length=1)


class ProtectedScientificState(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.protected-scientific-state.v1"]
    protocol_ref: StrictArtifactRef
    case_ref: StrictArtifactRef
    input_snapshot_ref: StrictArtifactRef
    expected_behavior_ref: StrictArtifactRef
    estimand_sha256: str
    source_authority_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)


class RepairProposal(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.repair-proposal.v1"]
    proposal_id: str
    failed_attempt_ref: StrictArtifactRef
    failed_report_ref: StrictArtifactRef
    case_ref: StrictArtifactRef
    finding_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    operations_ref: StrictArtifactRef
    operations: Annotated[RepairOperationsArtifact, BeforeValidator(strict_model_input)]
    affected_target_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    expected_verification: tuple[str, ...] = Field(min_length=1)
    protected_state_ref: StrictArtifactRef
    protected_state: Annotated[ProtectedScientificState, BeforeValidator(strict_model_input)]


class OwnerRepairDecision(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.owner-repair-decision.v1"]
    proposal_ref: StrictArtifactRef
    decision: Literal["approved", "rejected"]
    owner: str
    decided_at: AwareDatetime


class RepairApproval(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.repair-approval.v1"]
    approval_id: str
    proposal_ref: StrictArtifactRef
    proposal: Annotated[RepairProposal, BeforeValidator(strict_model_input)]
    owner_decision_ref: StrictArtifactRef


class VerifiedFindingResolution(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.verified-finding-resolution.v1"]
    resolution_id: str
    finding_ref: StrictArtifactRef
    resolution: Literal["closed", "limited", "reopened"]
    successor_report_ref: StrictArtifactRef
    successor_evaluation_ref: StrictArtifactRef
    witness_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)


class ProtocolRegressionCaseResult(BaseModel):
    model_config = STRICT
    case_ref: StrictArtifactRef
    attempt_ref: StrictArtifactRef
    report_ref: StrictArtifactRef
    evaluation_ref: StrictArtifactRef


class ProtocolRegressionReport(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.protocol-regression-report.v1"]
    regression_id: str
    protocol_ref: StrictArtifactRef
    session_ref: StrictArtifactRef
    case_results: tuple[
        ProtocolRegressionCaseResult,
        ProtocolRegressionCaseResult,
        ProtocolRegressionCaseResult,
        ProtocolRegressionCaseResult,
    ]


class RepairClosure(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.repair-closure.v1"]
    closure_id: str
    before_attempt_ref: StrictArtifactRef
    before_report_ref: StrictArtifactRef
    approval_ref: StrictArtifactRef
    successor_attempt_ref: StrictArtifactRef
    successor_review_publication_refs: tuple[
        StrictArtifactRef, StrictArtifactRef, StrictArtifactRef
    ]
    successor_report_ref: StrictArtifactRef
    successor_evaluation_ref: StrictArtifactRef
    rerun_evidence_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    verified_resolution_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    new_finding_refs: tuple[StrictArtifactRef, ...]
    protocol_regression_ref: StrictArtifactRef
```

`reduce_case_state()` accepts only a reopened exact `CaseBehaviorEvaluation` and three authenticated `ReviewPublication` values. It computes the immutable state of that one attempt over the complete finding union. Any Important/Critical is `needs-revision`; behavior deviation, disagreement, incomplete review, or external pending is `review-required`; otherwise the exact expected behavior with complete reviews is `personal-baseline-passed`. Synthesis cannot downgrade, delete, or close primary findings. A repair never rewrites this report: `RepairClosure` separately resolves predecessor findings by binding a clean successor report/evaluation and protocol regression. Arbitrary closure models and booleans are not part of the reducer API.

For `CaseBehaviorEvaluation`, `inventory_ref` must equal the attempt's common `attempt_inventory_ref`; `target_ref` means the exact `run_ref` for `CompletedFactoryRunTarget` and the exact `inspection_ref` for `CorrectStopTarget`. The service derives both from the attempt union and rejects caller substitutions.

- [ ] **Step 6: Run contract GREEN and static gates**

Run:

```bash
uv run pytest tests/unit/test_personal_validation_contracts.py tests/unit/test_personal_validation_review_reducer.py tests/unit/test_personal_validation_repairs.py -q
uv run ruff check src/envresearch/personal_validation tests/unit/test_personal_validation_contracts.py tests/unit/test_personal_validation_review_reducer.py tests/unit/test_personal_validation_repairs.py
uv run mypy src/envresearch/personal_validation
```

Expected: all pass with strict/frozen/no-extra/nested-revalidation attacks covered.

- [ ] **Step 7: Request independent contract review and commit**

Review must check the exact four-case order, acyclic attempt fields, full-digest identities, no score/release fields, complete-union reduction, and immutable closure semantics.

Commit: `feat(validation): add personal validation contracts`

---

### Task 3: Research-owned correct-stop inspection and exact root inventories

**Files:**
- Create: `src/envresearch/research/stop_inspection.py`
- Modify: `src/envresearch/research/__init__.py`
- Create: `src/envresearch/personal_validation/snapshots.py`
- Create: `tests/integration/test_research_stop_inspection.py`
- Create: `tests/unit/test_personal_validation_snapshots.py`

**Interfaces:**
- Consumes: `open_existing_research_authority(workspace)`, pure `summarize(...)`, checkpoint/gate/finding stores, and `PinnedRoot`.
- Produces: `inspect_research_stop(workspace) -> ResearchStopInspection`, `snapshot_inputs(...)`, `snapshot_system(...)`, and `snapshot_roots(...)`.

- [ ] **Step 1: Write the read-only stop inspection RED**

```python
def test_inspect_blocked_run_is_complete_and_zero_write(
    blocked_research_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = tree_state(blocked_research_root)
    monkeypatch.setattr(ResearchOrchestrator, "initialize", forbidden)
    monkeypatch.setattr(ResearchOrchestrator, "advance", forbidden)

    inspected = inspect_research_stop(blocked_research_root)

    assert inspected.phase == "blocked"
    assert inspected.stop_code == "RESEARCH_RUN_BLOCKED"
    assert inspected.findings
    assert inspected.research_evidence
    assert tree_state(blocked_research_root) == before
```

- [ ] **Step 2: Run the stop inspection RED**

Run: `uv run pytest tests/integration/test_research_stop_inspection.py -q`

Expected: collection failure for the missing `stop_inspection` module.

- [ ] **Step 3: Implement pure stop reconstruction**

```python
def inspect_research_stop(workspace: Path) -> ResearchStopInspection:
    orchestrator = open_existing_research_authority(workspace)
    try:
        summary = summarize(
            run_id=orchestrator.config.run_id,
            graph=orchestrator.graph,
            workspace=orchestrator.workspace,
            lifecycle=orchestrator.lifecycle,
            checkpoints=orchestrator.checkpoints,
            gate_lookup=orchestrator.bound_gates.active_gate,
            has_open_blocker=lambda: bool(load_open_design_findings(orchestrator)),
            require_complete_final=forbid_complete_target,
            gate_order=GATE_ORDER,
        )
        if summary.phase is not ResearchRunPhase.BLOCKED:
            raise ValueError("research run is not a correct-stop candidate")
        return ResearchStopInspection.from_reopened_authority(orchestrator, summary)
    finally:
        orchestrator.close()
```

Do not call `ResearchOrchestrator.initialize()`, `_summarize()`, `audit.sync()`, recovery, or work-order issuance. Bind exact active gate/context payloads, unresolved design findings/review ref, completed checkpoint digests, and research-owned `ResearchFileEvidence`; do not import Personal inventory contracts into `research`.

- [ ] **Step 4: Write exact snapshot and cross-root absence REDs**

```python
def test_correct_stop_inventory_rejects_hidden_factory_result(
    correct_stop_roots: AttemptRoots,
) -> None:
    inspection = inspect_research_stop(correct_stop_roots.research_design)
    publish_fake_factory_run_pointer(correct_stop_roots.factory)
    inventory = snapshot_roots(correct_stop_roots)
    with pytest.raises(PersonalValidationIntegrityInvalid, match="result artifact"):
        correct_stop_target(inspection_ref(inspection), inspection, inventory_ref(inventory), inventory)
```

```python
def test_input_snapshot_covers_modes_symlinks_untracked_and_effective_config(tmp_path: Path) -> None:
    snapshot = snapshot_inputs(input_fixture_tree(tmp_path))
    assert {entry.kind for entry in snapshot.entries} >= {"file", "directory", "symlink"}
    assert any(entry.logical_name == "effective-config.json" for entry in snapshot.entries)
```

- [ ] **Step 5: Implement snapshots and correct-stop absence derivation**

`snapshot_roots()` records each logical root identity `(st_dev, st_ino)` plus sorted entries with relative path, kind, bytes digest, size, owner, mode, link count, and symlink target. `require_correct_stop_inventory()` uses versioned `CORRECT_STOP_FORBIDDEN_NAMESPACES_V1` predicates and rejects: Factory prepared/committed/current pointers and run objects; Paper release pending/committed/current pointers and release/draft/audit/result objects; LocalAnalysis `analyses/*/current.json` and analysis outputs; V0.3/V0.3.1 transition/current and output namespaces; and stray empirical table/figure/result artifacts. Tests seed every category independently and require the exact typed absence finding.

- [ ] **Step 6: Run inspection/snapshot GREEN and regressions**

Run:

```bash
uv run pytest tests/integration/test_research_stop_inspection.py tests/unit/test_personal_validation_snapshots.py tests/integration/test_factory_design_resolver.py -q
uv run ruff check src/envresearch/research/stop_contracts.py src/envresearch/research/stop_inspection.py src/envresearch/personal_validation/snapshots.py tests/integration/test_research_stop_inspection.py tests/unit/test_personal_validation_snapshots.py
uv run mypy src/envresearch/research/stop_contracts.py src/envresearch/research/stop_inspection.py src/envresearch/personal_validation/snapshots.py
```

- [ ] **Step 7: Request independent correct-stop review and commit**

Review must try a hidden Factory pointer/object, a Paper result, a changed checkpoint, root replacement, and spies that forbid initialize/recover/issue.

Commit: `feat(research): add read-only stop inspection`

---

### Task 4: Private store, event DAG, and exact attempt preparation

**Files:**
- Create: `src/envresearch/personal_validation/private_store.py`
- Create: `src/envresearch/personal_validation/roots.py`
- Create: `src/envresearch/personal_validation/events.py`
- Create: `src/envresearch/personal_validation/targets.py`
- Create: `src/envresearch/personal_validation/service.py`
- Create: `tests/integration/personal_validation_fixtures.py`
- Create: `tests/integration/test_personal_validation_authority.py`
- Create: `tests/integration/test_personal_validation_recovery.py`

**Interfaces:**
- Consumes: Task 1 pinned object/journal seams, Task 2 contracts, Task 3 stop inspection/snapshots, and injected `FactoryRunService`.
- Produces: strict `PersonalRootAuthorityManifest`, `PersonalValidationStore`, `PersonalValidationService.prepare_existing_run(...)`, `prepare_correct_stop(...)`, `status(...)`, and the event DAG.

The non-durable service return is exact and contains no hidden lookup state:

```python
@dataclass(frozen=True, slots=True)
class PreparedAttempt:
    session_ref: ArtifactRef
    attempt_ref: ArtifactRef
    completion_event_id: str
```

- [ ] **Step 1: Write private-root and DAG REDs**

```python
def test_private_store_uses_explicit_nested_storage_and_control_roots(
    private_root: Path,
) -> None:
    store = PersonalValidationStore.create(private_root)
    assert store.objects.lexical_path == private_root / "objects"
    assert store.journals.lexical_path == private_root / "journals"
    assert store.control.lexical_path == private_root / "control/journal"
    assert all(root.is_exact_child_of(store.root) for root in (store.objects, store.journals))
    assert store.control.is_exact_descendant_of(store.root, Path("control/journal"))
    for left, right in itertools.combinations(
        (store.objects, store.journals, store.control), 2
    ):
        assert not directories_overlap(left.fd, right.fd)
```

```python
def test_attempt_event_dag_has_no_forward_reference(
    personal_service: PersonalValidationService,
    existing_run_ref: ArtifactRef,
) -> None:
    prepared = personal_service.prepare_existing_run(protocol_ref(), case_ref(), existing_run_ref)
    attempt = personal_service.store.load(prepared.attempt_ref, PersonalValidationAttempt)
    completed = personal_service.store.require_event(prepared.completion_event_id)
    assert completed.object_ref == prepared.attempt_ref
    assert "bundle_ref" not in attempt.model_dump()
    assert "completion_event_ref" not in attempt.model_dump()
```

- [ ] **Step 2: Run private-store REDs**

Run: `uv run pytest tests/integration/test_personal_validation_authority.py tests/integration/test_personal_validation_recovery.py -q`

Expected: collection failure for missing store/service modules.

- [ ] **Step 3: Implement explicit private-root composition**

```python
class PinnedDirectoryIdentity(BaseModel):
    model_config = STRICT
    logical_name: str
    canonical_path: str
    device: int
    inode: int


class PersonalRootAuthorityManifest(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.root-authority-manifest.v1"]
    manifest_id: str
    private_root: PinnedDirectoryIdentity
    exclusion_roots: tuple[PinnedDirectoryIdentity, ...] = Field(min_length=1)


class PersonalValidationStore:
    @classmethod
    def create(
        cls, private_root: Path, exclusions: RootExclusionSet
    ) -> PersonalValidationStore:
        with ExitStack() as owned:
            root = owned.enter_context(require_private_validation_root(private_root, exclusions, create=True))
            objects = owned.enter_context(root.open_child_root(Path("objects"), private=True, create=True))
            journals = owned.enter_context(root.open_child_root(Path("journals"), private=True, create=True))
            control = owned.enter_context(root.open_child_root(Path("control/journal"), private=True, create=True))
            registry = ExitRegistry.from_pinned(objects, create=True)
            journal = owned.enter_context(SecureJournal.create_from_pinned(
                Path("personal-validation.jsonl"), storage_root=journals, control_root=control
            ))
            manifest = publish_root_authority_manifest(root, exclusions)
            root.require_attached()
            store = cls(root, objects, journals, control, registry, journal, writable=True)
            store._owned = owned.pop_all()
            return store

    @classmethod
    def open_existing(
        cls, private_root: Path, exclusions: RootExclusionSet
    ) -> PersonalValidationStore:
        with ExitStack() as owned:
            root = owned.enter_context(require_private_validation_root(private_root, exclusions, create=False))
            objects = owned.enter_context(root.open_child_root(Path("objects"), private=True, create=False))
            journals = owned.enter_context(root.open_child_root(Path("journals"), private=True, create=False))
            control = owned.enter_context(root.open_child_root(Path("control/journal"), private=True, create=False))
            registry = ExitRegistry.from_pinned(objects, create=False)
            journal = owned.enter_context(SecureJournal.open_existing(
                Path("personal-validation.jsonl"),
                storage_root=journals,
                control_root=control,
                reconcile=False,
            ))
            require_exact_root_authority_manifest(root, exclusions)
            root.require_attached()
            store = cls(root, objects, journals, control, registry, journal, writable=False)
            store._owned = owned.pop_all()
            return store
```

Validate the top private root as absolute, non-symlink, owner/0700, physically outside repository, Git common directory, every linked worktree, and every bound Obsidian root. At create, publish an authenticated `PersonalRootAuthorityManifest` binding the top `(dev, ino)`, all exclusion-root identities, and their canonical paths. Every reopen receives the full `RootExclusionSet`, revalidates the manifest and current physical separation before and after its lease, and rejects a private-root rename under any excluded root without mutating it. Open every child relative to the retained top-root descriptor, prove its exact relative identity and containment, and recheck top attachment before/after composition. Inject those same pinned children into journal/registry; never reopen their lexical paths. Reject default sibling control roots. `PersonalValidationStore` is an idempotent context manager; it closes journal, borrowed registry state, control, journals, objects, and top root in reverse ownership order, including partial-construction failure. Registry borrows and never double-closes its pin.

- [ ] **Step 4: Implement canonical event identity and exact retry**

```python
def event_id(
    session_id: str,
    operation: str,
    object_ref: ArtifactRef,
    predecessor_sha256: str,
    sequence: int,
) -> str:
    payload = canonical_json(
        {
            "session_id": session_id,
            "operation": operation,
            "object_ref": object_ref.model_dump(mode="json"),
            "predecessor_sha256": predecessor_sha256,
            "sequence": sequence,
        }
    )
    return f"personal-event-{hashlib.sha256(payload).hexdigest()}"
```

Every writer uses `with registry.lock(session_subject):` and, while that single lock remains held, reopens the journal, validates the complete sequence/predecessor chain, publishes the immutable object, and calls `append_unique(...)`. No read-then-append gap exists outside the session lock. Exact duplicate returns the existing event; divergent reuse, sequence gaps, or predecessor mismatch raises `PersonalValidationIntegrityInvalid`. The start event binds the already-published session ref; the completion event binds the attempt ref. Read-only store methods never reconcile. A two-process same-sequence/different-object RED requires exactly one appended event and one typed loser after disk reopen.

- [ ] **Step 5: Implement target adapters and attempt publication**

```python
def completed_factory_target(service: FactoryRunService, run_ref: ArtifactRef) -> CompletedFactoryRunTarget:
    status = service.status(run_ref)
    return CompletedFactoryRunTarget(
        target_type="completed-factory-run",
        run_ref=status.run_ref,
        run=status.run,
    )


def correct_stop_target(
    inspection_ref: ArtifactRef,
    inspection: ResearchStopInspection,
    inventory_ref: ArtifactRef,
    inventory: AttemptRootInventory,
) -> CorrectStopTarget:
    require_correct_stop_inventory(inventory)
    return CorrectStopTarget(
        target_type="correct-stop",
        inspection_ref=inspection_ref,
        inspection=inspection,
        attempt_inventory_ref=inventory_ref,
    )
```

`prepare_*()` order is session object -> start event -> attempt object -> completion event. Task 6 subsequently publishes ReviewBundles that bind the completed attempt. Object-before-event crash leaves an explicit orphan and exact retry reuses the same object/event identity.

- [ ] **Step 6: Write and witness recovery/root-swap attacks**

```python
@pytest.mark.parametrize("boundary", ["attempt-object", "completion-event"])
def test_prepare_recovers_exact_crash_boundary(boundary: str, process_case: ProcessCase) -> None:
    first = process_case.crash(boundary)
    assert first.exitcode == process_case.expected_exit(boundary)
    retry = process_case.fresh_service().prepare(process_case.request)
    assert retry == process_case.expected_refs
```

```python
@pytest.mark.parametrize(
    "damage",
    ["orphan-object", "missing-completion-event", "lagging-head", "missing-head", "missing-key", "missing-lock", "missing-anchor", "truncation"],
)
def test_status_is_zero_write_under_incomplete_or_corrupt_state(
    personal_case: PersonalCase, damage: str
) -> None:
    personal_case.damage(damage)
    before = personal_case.private_tree_state()
    with pytest.raises(expected_typed_error(damage)):
        personal_case.read_service.status(personal_case.session_ref)
    assert personal_case.private_tree_state() == before
```

- [ ] **Step 7: Run store/service GREEN and static gates**

Run:

```bash
uv run pytest tests/integration/test_personal_validation_authority.py tests/integration/test_personal_validation_recovery.py -q
uv run ruff check src/envresearch/personal_validation tests/integration/personal_validation_fixtures.py tests/integration/test_personal_validation_authority.py tests/integration/test_personal_validation_recovery.py
uv run mypy src/envresearch/personal_validation
```

- [ ] **Step 8: Request independent transaction review and commit**

Review must probe both Task 4 crash points, divergent event reuse, top-root swaps between every child open and journal construction, product-root mutation, partial-open cleanup, descriptor lifetime, and the full zero-write status/report damage matrix. Bundle crash coverage begins only after Task 6 creates that transaction.

Commit: `feat(validation): persist exact personal attempts`

---

### Task 5: Versioned canonical protocol and four case runners

**Files:**
- Create: `benchmarks/personal-validation/v1/protocol.json`
- Create: `benchmarks/personal-validation/v1/successful-end-to-end/case.json`
- Create: `benchmarks/personal-validation/v1/successful-end-to-end/input.json`
- Create: `benchmarks/personal-validation/v1/successful-end-to-end/expected-behavior.json`
- Create: `benchmarks/personal-validation/v1/successful-end-to-end/reviewer-contract.json`
- Create: `benchmarks/personal-validation/v1/correct-stop/case.json`
- Create: `benchmarks/personal-validation/v1/correct-stop/input.json`
- Create: `benchmarks/personal-validation/v1/correct-stop/expected-behavior.json`
- Create: `benchmarks/personal-validation/v1/correct-stop/reviewer-contract.json`
- Create: `benchmarks/personal-validation/v1/data-method-incompatibility/case.json`
- Create: `benchmarks/personal-validation/v1/data-method-incompatibility/input.json`
- Create: `benchmarks/personal-validation/v1/data-method-incompatibility/expected-behavior.json`
- Create: `benchmarks/personal-validation/v1/data-method-incompatibility/reviewer-contract.json`
- Create: `benchmarks/personal-validation/v1/evidence-citation-challenge/case.json`
- Create: `benchmarks/personal-validation/v1/evidence-citation-challenge/input.json`
- Create: `benchmarks/personal-validation/v1/evidence-citation-challenge/expected-behavior.json`
- Create: `benchmarks/personal-validation/v1/evidence-citation-challenge/reviewer-contract.json`
- Create: `benchmarks/personal-validation/v1/policies/scientific.json`
- Create: `benchmarks/personal-validation/v1/policies/evidence.json`
- Create: `benchmarks/personal-validation/v1/policies/synthesis.json`
- Create: `benchmarks/personal-validation/v1/policies/external-access.json`
- Create: `benchmarks/personal-validation/v1/rubric.json`
- Create: `benchmarks/personal-validation/v1/report-schema.json`
- Create: `src/envresearch/personal_validation/canonical_cases.py`
- Create: `src/envresearch/personal_validation/case_success.py`
- Create: `src/envresearch/personal_validation/case_stops.py`
- Create: `src/envresearch/personal_validation/case_challenge.py`
- Create: `tests/integration/test_personal_validation_cases.py`

**Interfaces:**
- Consumes: public Research/Paper/Factory services, built-in repository fixture data, and Tasks 2-4 protocol/attempt APIs.
- Produces: `load_protocol_v1()`, `run_case(case_ref, roots) -> PreparedAttempt`, and four exact case artifacts.

```python
class CanonicalPolicyRule(BaseModel):
    model_config = STRICT
    rule_id: str
    requirement: str


class CanonicalPolicyArtifact(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.policy-artifact.v1"]
    policy_kind: Literal[
        "scientific", "evidence", "synthesis", "external-access", "rubric", "report-schema"
    ]
    policy_version: str
    rules: tuple[CanonicalPolicyRule, ...] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class ProtocolPolicyArtifacts:
    scientific: CanonicalPolicyArtifact
    evidence: CanonicalPolicyArtifact
    synthesis: CanonicalPolicyArtifact
    external_access: CanonicalPolicyArtifact
    rubric: CanonicalPolicyArtifact
    report_schema: CanonicalPolicyArtifact


@dataclass(frozen=True, slots=True)
class LoadedProtocol:
    protocol_ref: ArtifactRef
    protocol: PersonalValidationProtocol
    cases: tuple[PersonalCanonicalCase, ...]
    expected_behaviors: tuple[ExpectedBehaviorContract, ...]
    reviewer_contracts: tuple[ReviewerBehavioralContract, ...]
    policy_artifacts: ProtocolPolicyArtifacts


@dataclass(frozen=True, slots=True)
class CaseExecutionContext:
    session_ref: ArtifactRef
    case: PersonalCanonicalCase
    roots: DisposableAttemptRoots
    factory: FactoryRunService
    paper: PaperCaseServices
    research: ResearchCaseServices
```

`ResearchCaseServices` explicitly exposes `build_hedonic_approved_design(include_rdd_rejection)`, `execute_until_blocking_review()`, `design_ref`, and `design_root`. `PaperCaseServices` explicitly exposes `build_clean_hedonic_release()`, `publish_auditable_overclaim()`, `revise_to_clean()`, and `build_revision_release()`. `DisposableAttemptRoots` and the two aggregates are frozen dataclasses created by private CLI composition; they expose only these production service adapters and explicit roots. They do not import test fixtures.

- [ ] **Step 1: Write the four-case RED matrix**

```python
@pytest.mark.parametrize(
    ("case_id", "expected_target", "required_observations"),
    [
        ("successful-end-to-end", "completed-factory-run", {"factory-chain-coherent", "successor-release-clean"}),
        ("correct-stop", "correct-stop", {"correct-stop-blocker", "namespace-absent"}),
        ("data-method-incompatibility", "completed-factory-run", {"method-rejected", "compatible-method-retained"}),
        ("evidence-citation-challenge", "completed-factory-run", {"predecessor-audit-blocked", "revision-closure-complete", "successor-release-clean"}),
    ],
)
def test_canonical_case_produces_exact_expected_behavior(
    validation_fixture: ValidationFixture,
    case_id: str,
    expected_target: str,
    required_observations: set[str],
) -> None:
    prepared = validation_fixture.service.run_case(case_id)
    attempt = validation_fixture.store.load(prepared.attempt_ref, PersonalValidationAttempt)
    evaluation = validation_fixture.service.evaluate_case(prepared.attempt_ref)
    assert attempt.target.target_type == expected_target
    assert attempt.input_snapshot_ref == validation_fixture.protocol.input_ref(case_id)
    assert evaluation.verdict == "expected-behavior-observed"
    assert {item.observation_kind for item in evaluation.observations} == required_observations
```

Each row also asserts literal semantics: success reopens the exact coherent design/release/audit/output chain; correct-stop matches the manifest's exact gate, code, finding kind, checkpoint, and forbidden-namespace absence; incompatibility binds the rejected RDD requirement (missing running variable/cutoff), unchanged estimand, and retained hedonic path; evidence challenge binds the predecessor blocked audit finding IDs/targets, exact revision closure witnesses, and clean revision-bound successor release. Mutation REDs independently alter each semantic edge.

```python
def test_protocol_rejects_case_or_expected_behavior_mutation(protocol_root: Path) -> None:
    mutate_one_byte(protocol_root / "correct-stop/case.json")
    with pytest.raises(PersonalValidationIntegrityInvalid, match="protocol"):
        load_protocol_v1(protocol_root)
```

- [ ] **Step 2: Run the four-case RED matrix**

Run: `uv run pytest tests/integration/test_personal_validation_cases.py -q`

Expected: missing manifest/runner failures.

- [ ] **Step 3: Implement protocol loading and exact manifest validation**

```python
def load_protocol_v1(root: Path = DEFAULT_PROTOCOL_ROOT) -> LoadedProtocol:
    protocol_bytes = read_canonical_json(root / "protocol.json")
    protocol = PersonalValidationProtocol.model_validate_json(protocol_bytes)
    cases = tuple(load_exact_case(root, item.case_ref) for item in protocol.cases)
    expected = tuple(load_exact_expected_behavior(root, case) for case in cases)
    reviewer_contracts = tuple(load_safe_reviewer_contract(root, case) for case in cases)
    policies = load_and_hash_protocol_policies(root, protocol)
    if tuple(case.kind for case in cases) != CANONICAL_CASE_ORDER:
        raise PersonalValidationIntegrityInvalid(
            "personal protocol case order is invalid",
            finding_kind="protocol-invalid",
        )
    return LoadedProtocol(
        protocol_ref(protocol_bytes), protocol, cases, expected, reviewer_contracts, policies
    )
```

All fixture refs are content-addressed. The loader byte-reopens every `expected-behavior.json`, safe `reviewer-contract.json`, and all six policy/rubric/schema artifacts, checks their canonical bytes against exact refs/digests in the case/protocol, and rejects substitution or omission. The primary `ReviewBundle.behavioral_contract_ref` must resolve only to `ReviewerBehavioralContract`; the expected-behavior ref and its transitive bytes are explicitly forbidden from both primary bundle closures. REDs substitute the full expected-behavior ref, embed its digest/seeded details under renamed fields, and require leakage rejection. Production code does not import from `tests/`.

- [ ] **Step 4: Implement successful and incompatibility cases**

The successful case uses the trusted-local synthetic hedonic path equivalent to `tests/integration/test_factory_run.py::connected_factory`, moved into production-owned case builders with repository fixture inputs.

The incompatibility case preserves a compatible hedonic primary path and records an honest rejected RDD alternative with exact `MethodRejectionEvidence` for missing running variable/cutoff. It completes a Factory run while proving the incompatible method was not selected.

```python
def run_success_case(context: CaseExecutionContext) -> ArtifactRef:
    design_ref = context.research.build_hedonic_approved_design(include_rdd_rejection=False)
    release_ref = context.paper.build_clean_hedonic_release()
    return context.factory.assemble(design_ref, release_ref)


def run_incompatibility_case(context: CaseExecutionContext) -> ArtifactRef:
    design_ref = context.research.build_hedonic_approved_design(include_rdd_rejection=True)
    release_ref = context.paper.build_clean_hedonic_release()
    return context.factory.assemble(design_ref, release_ref)
```

- [ ] **Step 5: Implement correct-stop and evidence-challenge cases**

The correct-stop case executes the repository `blocking-review` research scenario only until the blocking design finding is durable, then calls `inspect_research_stop`; it must not enter revision or Factory assembly.

The evidence challenge publishes a service-reachable policy/claim-strength overclaim predecessor, obtains a blocked independent Paper audit, revises to a clean successor with `RevisionService`, builds a revision-bound release, and assembles a Factory run. Its `DraftRevision` preserves predecessor findings, exact targets, closure witnesses, and successor evidence.

```python
def run_correct_stop_case(context: CaseExecutionContext) -> ResearchStopInspection:
    context.research.execute_until_blocking_review()
    return inspect_research_stop(context.research.design_root)


def run_evidence_challenge_case(context: CaseExecutionContext) -> ArtifactRef:
    blocked_draft_ref, blocked_audit_ref = context.paper.publish_auditable_overclaim()
    revision_ref = context.paper.revise_to_clean(blocked_draft_ref, blocked_audit_ref)
    release_ref = context.paper.build_revision_release(revision_ref)
    return context.factory.assemble(context.research.design_ref, release_ref)
```

- [ ] **Step 6: Prove disposable-root-only writes and deterministic reruns**

```python
def test_all_cases_leave_authoritative_roots_unchanged(validation_fixture: ValidationFixture) -> None:
    before = validation_fixture.authoritative_tree_state()
    first = validation_fixture.service.run_all_cases(new_session=True)
    exact_retry = validation_fixture.service.run_session(first.session_ref)
    second = validation_fixture.service.run_all_cases(new_session=True)
    assert exact_retry == first
    assert second.session_ref != first.session_ref
    assert tuple(item.attempt_ref for item in second.attempts) != tuple(
        item.attempt_ref for item in first.attempts
    )
    assert tuple(item.semantic_evaluation_digest for item in second.attempts) == tuple(
        item.semantic_evaluation_digest for item in first.attempts
    )
    assert validation_fixture.authoritative_tree_state() == before
    assert validation_fixture.disposable_tree_state()
```

- [ ] **Step 7: Run canonical GREEN and affected scientific regressions**

Run:

```bash
uv run pytest tests/integration/test_personal_validation_cases.py tests/integration/test_factory_run.py tests/integration/test_paper_revision.py tests/integration/test_paper_release_revision.py tests/integration/test_research_quality_benchmarks.py -q
uv run ruff check src/envresearch/personal_validation tests/integration/test_personal_validation_cases.py
uv run mypy src/envresearch/personal_validation
```

- [ ] **Step 8: Request independent scientific case review and commit**

Review must verify that correct-stop creates no result, incompatibility rejects the exact unsupported method without changing the primary estimand, and evidence challenge uses a service-reachable blocked-to-clean revision rather than a unit-only forged model.

Commit: `feat(validation): add four canonical cases`

---

### Task 6: Role-separated ReviewBundles, assignments, review ingestion, and reports

**Files:**
- Create: `src/envresearch/personal_validation/review_bundle.py`
- Create: `src/envresearch/personal_validation/reviews.py`
- Create: `src/envresearch/personal_validation/report.py`
- Modify: `src/envresearch/personal_validation/service.py`
- Create: `tests/integration/test_personal_validation_reviews.py`
- Create: `tests/unit/test_personal_validation_report.py`

**Interfaces:**
- Consumes: exact attempt targets and Task 2 review/report contracts.
- Produces: `PreparedBundle`, `RecordedReview`, `FinalizedReport`, `prepare_bundle()`, `assign_review()`, `record_dispatch(...)`, `record_review(..., dispatch_receipt_ref)`, synthesis assignment/recording through the same APIs, `evaluate_case()`, and `finalize_report()`.

```python
@dataclass(frozen=True, slots=True)
class PreparedBundle:
    bundle_ref: ArtifactRef
    bundle: ReviewBundle
    completion_event_id: str


@dataclass(frozen=True, slots=True)
class RecordedReview:
    publication_ref: ArtifactRef
    review_ref: ArtifactRef
    finding_refs: tuple[ArtifactRef, ...]
    access_record_refs: tuple[ArtifactRef, ...]
    completion_event_id: str


@dataclass(frozen=True, slots=True)
class FinalizedReport:
    report_ref: ArtifactRef
    report: PersonalValidationReport
    completion_event_id: str
```

`review_bundle.py` publishes a strict role projection:

```python
class ScientificProjection(BaseModel):
    model_config = STRICT
    projection_type: Literal["scientific"]
    estimand_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    identification_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    compatibility_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    assumption_threat_refs: tuple[StrictArtifactRef, ...]
    diagnostic_refs: tuple[StrictArtifactRef, ...]


class EvidenceProjection(BaseModel):
    model_config = STRICT
    projection_type: Literal["evidence"]
    lineage_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    numeric_output_refs: tuple[StrictArtifactRef, ...]
    citation_refs: tuple[StrictArtifactRef, ...]
    reproducibility_refs: tuple[StrictArtifactRef, ...]
    claim_strength_refs: tuple[StrictArtifactRef, ...]


class SynthesisProjection(BaseModel):
    model_config = STRICT
    projection_type: Literal["synthesis"]
    scientific_publication_ref: StrictArtifactRef
    evidence_publication_ref: StrictArtifactRef


class ReviewBundle(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.review-bundle.v1"]
    bundle_id: str
    attempt_ref: StrictArtifactRef
    role: Literal["scientific", "evidence", "synthesis"]
    behavioral_contract_ref: StrictArtifactRef
    target_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    evidence_refs: tuple[StrictArtifactRef, ...] = Field(min_length=1)
    projection: Annotated[
        ScientificProjection | EvidenceProjection | SynthesisProjection,
        BeforeValidator(strict_model_input),
        Field(discriminator="projection_type"),
    ]
    primary_publication_refs: tuple[StrictArtifactRef, ...] = ()
    projection_policy_sha256: str
```

Each projection is a strict/frozen allowlisted model. The validator requires role↔projection identity, no primary refs for Scientific/Evidence, and exactly two canonical `ReviewPublication` refs for Synthesis. Recursive canonical-byte oracle/canary scans cover the entire projection and every referenced projection artifact.

- [ ] **Step 1: Write bundle projection and assignment REDs**

```python
@pytest.mark.parametrize("role", ["scientific", "evidence"])
def test_primary_bundle_withholds_oracle_and_prior_review_material(
    review_case: ReviewCase, role: str
) -> None:
    prepared = review_case.service.prepare_bundle(review_case.attempt_ref, role=role)
    encoded = review_case.store.canonical_bytes(prepared.bundle_ref)
    assert review_case.seeded_location.encode() not in encoded
    assert review_case.full_oracle_digest.encode() not in encoded
    assert b"prior_review" not in encoded
    assert b"predecessor_finding" not in encoded
```

```python
def test_assignment_rejects_cross_role_replay(review_case: ReviewCase) -> None:
    assignment = review_case.assign("scientific", invocation_id="invoke-0001")
    receipt = review_case.record_dispatch(assignment)
    response = review_case.canonical_response_bytes(role="evidence")
    with pytest.raises(PersonalValidationAuthorityInvalid, match="role"):
        review_case.service.record_review(assignment, receipt, response)
```

- [ ] **Step 2: Run bundle/assignment REDs**

Run: `uv run pytest tests/integration/test_personal_validation_reviews.py -q -k 'bundle or assignment'`

Expected: missing bundle/review service methods.

- [ ] **Step 3: Implement role-safe bundles and dispatch provenance**

```python
def assign_review(
    self,
    attempt_ref: ArtifactRef,
    bundle_ref: ArtifactRef,
    *,
    role: Literal["scientific", "evidence", "synthesis"],
    invocation_id: str,
) -> ArtifactRef:
    attempt = self._reopen_attempt(attempt_ref)
    bundle = self._reopen_bundle(bundle_ref, attempt_ref, role)
    assignment = ReviewAssignment.materialize(
        attempt_ref=attempt_ref,
        bundle_ref=bundle_ref,
        role=role,
        policy_sha256=self.protocol.policy_for(role),
        invocation_id=invocation_id,
        primary_publication_refs=bundle.primary_publication_refs,
    )
    return self.store.publish_assignment(assignment)
```

Scientific bundles include estimand, identification, compatibility, assumptions, threats, diagnostics, and behavioral contract. Evidence bundles include exact lineage, numbers, tables/figures, citations, reproducibility, claim strength, and behavioral contract. Both omit the full oracle and seeded locations.

`prepare_bundle()` publishes bundle object then a `bundle-published` event and returns `PreparedBundle(bundle_ref, bundle, completion_event_id)`. Crash between those steps leaves an explicit orphan reusable only by exact retry.

`record_dispatch(assignment_ref, canonical_observation_bytes)` validates a strict `AgentDispatchObservation(invocation_id, observed_model_id, observed_runtime_id, dispatched_at)`, reopens the exact assignment/invocation, publishes `AgentDispatchReceipt`, appends its event, and returns the receipt ref. This is the sole producer used by CLI and the real Agent runbook; the Agent response cannot mint or alter it.

- [ ] **Step 4: Implement strict raw-response ingestion**

```python
def record_review(
    self, assignment_ref: ArtifactRef, dispatch_receipt_ref: ArtifactRef, raw: bytes
) -> RecordedReview:
    assignment = self.store.load(assignment_ref, ReviewAssignment)
    receipt = self._require_dispatch_receipt(dispatch_receipt_ref, assignment)
    response = AgentReviewResponse.model_validate_json(raw)
    if raw != canonical_json(response.model_dump(mode="json")):
        raise PersonalValidationIntegrityInvalid(
            "agent response bytes are noncanonical",
            finding_kind="review-response-noncanonical",
        )
    if response.role != assignment.role:
        raise PersonalValidationAuthorityInvalid(
            "agent response role differs from assignment",
            finding_kind="review-role-invalid",
        )
    review = AgentReview.from_response(
        assignment=assignment,
        dispatch_receipt_ref=dispatch_receipt_ref,
        raw_response_sha256=hashlib.sha256(raw).hexdigest(),
        response=response,
    )
    review_ref = self.store.publish_review(review)
    finding_refs = tuple(
        self.store.publish_finding(PersonalFinding.from_response(item, review_ref=review_ref))
        for item in response.findings
    )
    finding_refs_by_local_key = require_exact_local_finding_map(
        response.findings, finding_refs
    )
    access_record_refs = tuple(
        self.store.publish_external_access(
            ExternalAccessRecord.from_response(item, review_ref, finding_refs_by_local_key)
        )
        for item in response.external_access
    )
    publication_ref = self.store.publish_review_publication(
        ReviewPublication.materialize(
            assignment_ref, review_ref, finding_refs, access_record_refs
        )
    )
    event_id = self.store.append_review_event(publication_ref)
    return RecordedReview(publication_ref, review_ref, finding_refs, access_record_refs, event_id)
```

Replay of the same assignment/receipt/raw bytes returns the same exact publication/ref tuple. Same assignment with different bytes, cross-role reuse, changed receipt/model/runtime/policy/bundle/attempt, duplicate assignment, or stale target fails closed. This proves dispatch provenance without creating human/model signatures. Bundle/review/publication/report object→event transactions all use the Task 4 session lock and exact orphan recovery.

Before publishing a finding, `record_review()` requires every target/evidence ref to be in the role bundle's exact allowlisted closure (or, for Synthesis, in either primary publication's finding/evidence closure). An Agent cannot introduce an arbitrary private or unrelated ref.

- [ ] **Step 5: Write synthesis/report reducer REDs**

```python
def test_synthesis_omission_cannot_remove_primary_finding(review_case: ReviewCase) -> None:
    scientific = review_case.record_scientific(important=True)
    evidence = review_case.record_evidence()
    synthesis = review_case.record_synthesis(findings=())
    finalized = review_case.service.finalize_report(
        review_case.attempt_ref,
        review_case.evaluation_ref,
        scientific,
        evidence,
        synthesis,
    )
    assert finalized.report.state == "needs-revision"
    assert finalized.report.finding_refs == (review_case.scientific_finding_ref,)
```

```python
def test_external_access_requires_exact_allowlisted_read_provenance() -> None:
    with pytest.raises(ValidationError):
        ExternalAccessResponse(
            provider="zotero",
            operation="delete-item",
            request_sha256=DIGEST,
            source_locator="zotero://library/items/ABC",
            authorization_ref=None,
            response_sha256=DIGEST,
            retrieved_at=NOW,
            policy_ref=REF,
            local_finding_keys=(),
        )
```

- [ ] **Step 6: Implement synthesis and complete-union report publication**

Synthesis uses `assign_review(... role="synthesis")`; the service reopens the synthesis bundle and derives both exact primary publication refs rather than accepting them from the caller. The bundle contains the original safe reviewer contract plus both immutable primary publications. It is recorded through the same `record_review()` path. `evaluate_case()` independently derives and publishes `CaseBehaviorEvaluation` from the exact case, attempt target, expected-behavior artifact, and attempt-bound root inventory. `finalize_report()` does not trust a persisted verdict: under the session lock it recomputes the evaluation from reopened source authority, requires the bytes/ref to equal `evaluation_ref`, then independently reopens all three publications/reviews/findings/access records, preserves every finding and disagreement, and calls the deterministic reducer; it never trusts a status supplied by Synthesis. REDs forge verdict, omit a required observation, substitute an evidence ref, and pass an evaluation from another attempt.

External access records accept only enumerated read operations. Dispatch rejects private-root paths/private canaries before network/Zotero use. Failure records `external-verification-pending` and cannot guess a result.

- [ ] **Step 7: Run review/report GREEN**

Run:

```bash
uv run pytest tests/integration/test_personal_validation_reviews.py tests/unit/test_personal_validation_report.py tests/unit/test_personal_validation_review_reducer.py -q
uv run ruff check src/envresearch/personal_validation tests/integration/test_personal_validation_reviews.py tests/unit/test_personal_validation_report.py
uv run mypy src/envresearch/personal_validation
```

- [ ] **Step 8: Request independent review-lifecycle review and commit**

Review must attack raw-response substitution, role replay, invocation reuse, oracle leakage, synthesis deletion/downgrade, external mutation, and private-canary disclosure.

Commit: `feat(validation): add agent advisory reports`

---

### Task 7: Owner-approved repair, successor attempts, and finding closure

**Files:**
- Create: `src/envresearch/personal_validation/repairs.py`
- Modify: `src/envresearch/personal_validation/service.py`
- Create: `tests/integration/test_personal_validation_repairs.py`
- Modify: `tests/unit/test_personal_validation_repairs.py`

**Interfaces:**
- Consumes: failed attempt/report/finding refs, executable canonical full-file replacement artifacts, exact `RepairProposal`, strict `OwnerRepairDecision`, disposable case roots, successor three-Agent publications/report/evaluation, and the four-case regression service.
- Produces: `record_replacement_blob()`, `record_repair_operations()`, `propose_repair(failed_report_ref, operations_ref)`, `record_owner_decision()`, `approve_repair(proposal_ref, owner_decision_ref)`, `apply_repair()`, `finalize_protocol_regression(...)`, `finalize_repair(...)`, read-only `repair_status(closure_ref)`, and immutable `RepairClosure`.

- [ ] **Step 1: Write approval-order and no-self-approval REDs**

```python
def test_apply_repair_cannot_create_its_own_approval(repair_case: RepairCase) -> None:
    proposal_ref = repair_case.service.propose_repair(
        repair_case.failed_report_ref,
        repair_case.operations_ref,
    )
    with pytest.raises(PersonalValidationAuthorityInvalid, match="approval"):
        repair_case.service.apply_repair(proposal_ref, approval_ref=None)
```

```python
def test_approval_event_precedes_any_repair_mutation(repair_case: RepairCase) -> None:
    proposal_ref = repair_case.proposal()
    approval_ref = repair_case.service.approve_repair(proposal_ref, repair_case.owner_decision_ref)
    repair_case.service.apply_repair(proposal_ref, approval_ref)
    events = repair_case.store.events()
    assert event_index(events, "repair-proposal") < event_index(events, "repair-approved")
    assert event_index(events, "repair-approved") < event_index(events, "repair-started")
```

- [ ] **Step 2: Run repair REDs**

Run: `uv run pytest tests/integration/test_personal_validation_repairs.py -q -k 'approval or ordering'`

Expected: missing repair service methods.

- [ ] **Step 3: Implement exact proposal and local approval capture**

```python
def approve_repair(
    self,
    proposal_ref: ArtifactRef,
    owner_decision_ref: ArtifactRef,
) -> ArtifactRef:
    proposal = self.store.load(proposal_ref, RepairProposal)
    decision = self.store.load(owner_decision_ref, OwnerRepairDecision)
    self._require_failed_report_findings_and_decision(proposal, decision)
    approval = RepairApproval.materialize(
        proposal_ref=proposal_ref,
        proposal=proposal,
        owner_decision_ref=owner_decision_ref,
    )
    reference = self.store.publish_approval(approval)
    self.store.append_event("repair-approved", reference)
    return reference
```

`record_replacement_blob()` and `record_repair_operations()` are the only canonical ingestion paths. `propose_repair()` reopens the failed report/attempt/case/findings and operations artifact, then derives the complete `ProtectedScientificState`, affected refs, and verification requirements itself; callers cannot omit an invariant or supply arbitrary prose fields. `record_owner_decision()` ingests strict canonical owner-decision bytes. `approve_repair()` only binds that pre-existing decision; it cannot synthesize owner identity/time or approve itself. The executor accepts only an exact approved decision whose proposal, failed report/attempt/findings, executable operations, derived protected state, and expected verification all match.

- [ ] **Step 4: Write successor/closure REDs**

```python
def test_repair_preserves_input_snapshot_and_old_findings(repair_case: RepairCase) -> None:
    closure_ref = repair_case.approve_apply_and_verify()
    closure = repair_case.store.load(closure_ref, RepairClosure)
    before = repair_case.store.load(closure.before_attempt_ref, PersonalValidationAttempt)
    after = repair_case.store.load(closure.successor_attempt_ref, PersonalValidationAttempt)
    assert after.input_snapshot_ref == before.input_snapshot_ref
    resolutions = repair_case.store.load_many(
        closure.verified_resolution_refs, VerifiedFindingResolution
    )
    assert {item.finding_ref for item in resolutions} == set(repair_case.finding_refs)
    assert closure.successor_review_publication_refs == repair_case.successor_publication_refs
    assert closure.successor_report_ref == repair_case.successor_report_ref
    assert closure.protocol_regression_ref == repair_case.protocol_regression_ref
```

```python
def test_disclosure_text_alone_cannot_close_material_finding(repair_case: RepairCase) -> None:
    successor = repair_case.successor_with_disclosure_only()
    with pytest.raises(PersonalValidationSupportInvalid, match="material finding"):
        repair_case.service.finalize_repair(
            repair_case.approval_ref,
            successor.attempt_ref,
            successor.publication_refs,
            successor.report_ref,
            successor.evaluation_ref,
            successor.protocol_regression_ref,
        )
```

- [ ] **Step 5: Implement disposable successor execution and exact closure**

```python
def apply_repair(
    self,
    proposal_ref: ArtifactRef,
    approval_ref: ArtifactRef,
) -> ArtifactRef:
    proposal, approval = self._reopen_exact_approval(proposal_ref, approval_ref)
    self.store.append_event("repair-started", approval_ref)
    successor_roots = self._new_disposable_roots(
        predecessor_attempt_ref=proposal.failed_attempt_ref,
        approval_ref=approval_ref,
    )
    self._apply_exact_operations(successor_roots, proposal.operations.operations)
    return self.case_runner.rerun(
        proposal.case_ref,
        roots=successor_roots,
        predecessor_attempt_ref=proposal.failed_attempt_ref,
        approved_operations=proposal.operations.operations,
    ).attempt_ref
```

`repair-started` is durable before successor-directory creation or mutation. The applicator supports only full-file canonical replacement v1, resolves `SafeRelativePath` descriptor-relatively against a case-specific logical-target allowlist, rejects symlinks/`..`/absolute paths, reopens the replacement blob, and verifies before/after bytes. It refuses any operation changing the complete service-derived `ProtectedScientificState`.

After rerun, the caller must obtain fresh Scientific and Evidence publications, then Synthesis, a successor `CaseBehaviorEvaluation`, and a successor report. `finalize_protocol_regression(session_ref, four_report_refs)` reopens the protocol/session and, in fixed case order, each current attempt/report/evaluation; it requires report↔attempt↔evaluation identity, `report.state="personal-baseline-passed"`, `evaluation.verdict="expected-behavior-observed"`, no unresolved Important/Critical, and exactly one result per protocol case before materializing `ProtocolRegressionCaseResult` values. Duplicate/substituted cases or a failing report are REDs. `finalize_repair()` reopens all successor evidence plus that regression report, recomputes it byte-for-byte, computes `VerifiedFindingResolution` objects, and only then publishes `RepairClosure`. A `closed` or `limited` resolution requires exact case-specific evidence showing the defect is no longer material; limitation text alone is insufficient. Original findings remain bound forever; new/reopened findings are added. `repair_status(closure_ref)` is strictly read-only reconstruction; it never creates a closure or retries work.

- [ ] **Step 6: Add crash and mismatched-approval attacks**

Cover proposal object/event, approval object/event, repair-started event, successor-root creation, operation apply, successor attempt, each successor review/report/evaluation, protocol regression object, closure object/event, changed proposal, wrong attempt, changed input snapshot, protected invariant mutation, and same approval reused for a broadened operation. Successor-root identity binds predecessor attempt plus exact approval ref: exact retry converges to the same root, while two distinct approved proposals for one failed attempt receive disjoint roots and cannot observe/overwrite one another in sequential or concurrent REDs. Conflicting retry fails without changing prior artifacts. Prove no successor root exists before `repair-started` is durable.

- [ ] **Step 7: Run repair GREEN and regressions**

Run:

```bash
uv run pytest tests/integration/test_personal_validation_repairs.py tests/unit/test_personal_validation_repairs.py tests/integration/test_personal_validation_cases.py -q
uv run ruff check src/envresearch/personal_validation/repairs.py src/envresearch/personal_validation/service.py tests/integration/test_personal_validation_repairs.py tests/unit/test_personal_validation_repairs.py
uv run mypy src/envresearch/personal_validation
```

- [ ] **Step 8: Request independent repair review and commit**

Review must prove no mutation occurs before approval, closures cannot erase findings, input snapshots remain exact, product roots are unchanged, and exact retry cannot broaden approval.

Commit: `feat(validation): add approved repair closure`

---

### Task 8: Reference-only deterministic JSON CLI

**Files:**
- Create: `src/envresearch/personal_validation/cli.py`
- Create: `src/envresearch/personal_validation/_cli_inputs.py`
- Create: `src/envresearch/personal_validation/_cli_composition.py`
- Create: `src/envresearch/cli_groups.py`
- Modify: `src/envresearch/cli.py`
- Modify: `src/envresearch/personal_validation/__init__.py`
- Create: `tests/integration/test_personal_validation_cli.py`

**Interfaces:**
- Consumes: `PersonalValidationService` and strict canonical reference/response files.
- Produces: `envresearch personal-validation prepare|prepare-bundle|assign-review|record-dispatch|record-review|evaluate-case|finalize-report|record-replacement|record-repair-operations|propose-repair|record-owner-decision|approve-repair|apply-repair|finalize-protocol-regression|finalize-repair|repair-status|status|report`.

- [ ] **Step 1: Write CLI registration and parser-error REDs**

```python
def test_personal_validation_group_requires_explicit_reference_arguments() -> None:
    result = CliRunner().invoke(app, ["personal-validation", "status"])
    assert result.exit_code == 2
    assert json.loads(result.stdout) == {
        "error": {
            "code": "PERSONAL_VALIDATION_AUTHORITY_INVALID",
            "finding_kind": "cli-input-invalid",
            "message": result_json(result)["error"]["message"],
        }
    }
```

```python
def test_unknown_review_response_field_is_rejected(cli_case: CliCase) -> None:
    response = cli_case.response_json(extra={"unexpected": True})
    result = cli_case.invoke(
        "record-review",
        cli_case.assignment_ref_path,
        cli_case.dispatch_receipt_ref_path,
        response,
    )
    assert result.exit_code == 2
    assert result_json(result)["error"]["finding_kind"] == "review-input-invalid"
```

- [ ] **Step 2: Run CLI REDs**

Run: `uv run pytest tests/integration/test_personal_validation_cli.py -q`

Expected: `No such command 'personal-validation'`.

- [ ] **Step 3: Extract root CLI registrations before adding the new group**

```python
# src/envresearch/cli_groups.py
def register_cli_groups(
    app: typer.Typer,
    *,
    benchmark_app: typer.Typer,
    run_app: typer.Typer,
    gate_app: typer.Typer,
) -> None:
    # The three local apps are injected by cli.py; this module never imports cli.py.
    app.add_typer(benchmark_app, name="benchmark")
    app.add_typer(run_app, name="run")
    app.add_typer(gate_app, name="gate")
    app.add_typer(research_app, name="research")
    app.add_typer(replication_app, name="replication")
    app.add_typer(econometrics_app, name="econometrics")
    app.add_typer(paper_app, name="paper")
    app.add_typer(factory_app, name="factory")
    app.add_typer(personal_validation_app, name="personal-validation")
```

This avoids a `cli.py` import cycle and keeps it below 400 lines without changing existing command behavior. Add an exact parity test over all pre-existing root command names and help output before registering the new group.

- [ ] **Step 4: Implement canonical input loaders and JSON error envelope**

```python
def load_canonical(path: Path | None, model: type[ModelT], finding_kind: str) -> ModelT:
    if path is None:
        raise PersonalValidationAuthorityInvalid(
            "explicit canonical JSON input is required",
            finding_kind=finding_kind,
        )
    try:
        raw = path.read_bytes()
        value = model.model_validate_json(raw)
    except (OSError, UnicodeError, ValidationError) as exc:
        raise PersonalValidationAuthorityInvalid(
            "explicit canonical JSON input is invalid", finding_kind=finding_kind
        ) from exc
    if raw != canonical_json(value.model_dump(mode="json")):
        raise PersonalValidationAuthorityInvalid(
            "explicit JSON input is noncanonical",
            finding_kind=finding_kind,
        )
    return value
```

Use the Factory custom `TyperGroup` pattern so missing/unknown arguments also produce deterministic JSON. Valid advisory states (`needs-revision`, `review-required`) return exit 0 and do not block other system commands. Invalid/corrupt authority returns exit 2.

- [ ] **Step 5: Implement exact-reference command signatures**

```text
personal-validation prepare PREPARATION_REQUEST_REF --private-root PATH --research-root PATH --v03-root PATH --v031-root PATH --paper-root PATH --factory-root PATH --repo-root PATH --git-common-dir PATH --worktree-root PATH... --obsidian-root PATH...
personal-validation prepare-bundle ATTEMPT_REF ROLE [--primary-publication-ref REF...] --private-root PATH
personal-validation assign-review ATTEMPT_REF BUNDLE_REF ROLE INVOCATION_ID --private-root PATH
personal-validation record-dispatch ASSIGNMENT_REF DISPATCH_OBSERVATION_JSON --private-root PATH
personal-validation record-review ASSIGNMENT_REF DISPATCH_RECEIPT_REF RESPONSE_JSON --private-root PATH
personal-validation evaluate-case ATTEMPT_REF --private-root PATH
personal-validation finalize-report ATTEMPT_REF EVALUATION_REF SCIENTIFIC_PUBLICATION_REF EVIDENCE_PUBLICATION_REF SYNTHESIS_PUBLICATION_REF --private-root PATH
personal-validation record-replacement REPLACEMENT_JSON --private-root PATH
personal-validation record-repair-operations FAILED_REPORT_REF OPERATIONS_JSON --private-root PATH
personal-validation propose-repair FAILED_REPORT_REF OPERATIONS_REF --private-root PATH
personal-validation record-owner-decision PROPOSAL_REF OWNER_DECISION_JSON --private-root PATH
personal-validation approve-repair PROPOSAL_REF OWNER_DECISION_REF --private-root PATH
personal-validation apply-repair PROPOSAL_REF APPROVAL_REF --private-root PATH
personal-validation finalize-protocol-regression SESSION_REF REPORT_REF REPORT_REF REPORT_REF REPORT_REF --private-root PATH
personal-validation finalize-repair APPROVAL_REF SUCCESSOR_ATTEMPT_REF SUCCESSOR_SCIENTIFIC_REF SUCCESSOR_EVIDENCE_REF SUCCESSOR_SYNTHESIS_REF SUCCESSOR_REPORT_REF SUCCESSOR_EVALUATION_REF PROTOCOL_REGRESSION_REF --private-root PATH
personal-validation repair-status CLOSURE_REF --private-root PATH
personal-validation status SESSION_REF --private-root PATH
personal-validation report REPORT_REF --private-root PATH
```

Every command—not only `prepare`—also requires the exact common root options `--private-root`, `--research-root`, `--v03-root`, `--v031-root`, `--paper-root`, `--factory-root`, `--repo-root`, `--git-common-dir`, one or more `--worktree-root`, and zero or more explicit `--obsidian-root`; the shorter lines above omit only this repeated suffix. `PreparationRequest` is a strict discriminated canonical object: `ExistingFactoryRunRequest(protocol_ref, case_ref, run_ref, session_nonce)` or `CanonicalCaseRunRequest(protocol_ref, case_ref, session_nonce)`. All authority and exclusion roots are descriptor-validated against `PersonalRootAuthorityManifest`; there is no default sibling control root or latest scanning. `status`, `report`, and `repair-status` compose with `create=False`, use never-reconcile journal reads, and perform zero writes. Mutation commands create/resume only the private session and disposable roots. Move/alias-under-excluded-root attacks between invocations fail without modifying either tree.

- [ ] **Step 6: Write real read-only/root-overlap CLI tests**

Cover same/ancestor/descendant/symlink/hardlink root aliases, private root under repo/Git/worktree/Obsidian, default sibling control root attempt, missing key/head/lock, unsafe modes, unknown fields, noncanonical JSON, and tree snapshots proving status/report create or chmod nothing.

- [ ] **Step 7: Run CLI GREEN and existing command regressions**

Run:

```bash
uv run pytest tests/integration/test_personal_validation_cli.py tests/integration/test_factory_cli.py tests/integration/test_factory_cli_handoffs.py tests/integration/test_research_cli.py tests/integration/test_paper_cli.py -q
uv run ruff check src/envresearch/cli.py src/envresearch/cli_groups.py src/envresearch/personal_validation/cli.py src/envresearch/personal_validation/_cli_inputs.py src/envresearch/personal_validation/_cli_composition.py tests/integration/test_personal_validation_cli.py
uv run mypy src/envresearch/cli.py src/envresearch/cli_groups.py src/envresearch/personal_validation
```

- [ ] **Step 8: Request independent CLI/read-only review and commit**

Review must use real composition, not factory monkeypatches, and prove missing control state is not recreated.

Commit: `feat(cli): add personal validation workflow`

---

### Task 9: Agent orchestration dry run, concurrency, and process recovery

**Files:**
- Create: `src/envresearch/personal_validation/panel_receipt.py`
- Modify: `src/envresearch/personal_validation/cli.py`
- Modify: `src/envresearch/personal_validation/__init__.py`
- Modify: `tests/integration/test_personal_validation_cli.py`
- Create: `tests/integration/personal_validation_process_fixtures.py`
- Create: `tests/integration/test_personal_validation_processes.py`
- Create: `tests/integration/test_personal_validation_agent_panel.py`
- Create: `.superpowers/sdd/2026-08-21-personal-usability-validation/agent-review-runbook.md`

**Interfaces:**
- Consumes: CLI refs, ReviewAssignments, strict Agent response JSON schema, and four canonical bundles.
- Produces: one complete three-Agent advisory report per canonical case, strict `PanelVerificationReceipt`, `verify_panel_receipt(...)`, `personal-validation verify-panel`, and concurrency/crash evidence.

```python
class PanelVerificationReceipt(BaseModel):
    model_config = STRICT
    schema_version: Literal["personal.panel-verification-receipt.v1"]
    receipt_id: str
    protocol_ref: StrictArtifactRef
    session_ref: StrictArtifactRef
    private_root_device: int
    private_root_inode: int
    report_refs: tuple[StrictArtifactRef, StrictArtifactRef, StrictArtifactRef, StrictArtifactRef]
    assignment_refs: tuple[StrictArtifactRef, ...] = Field(min_length=12, max_length=12)
    dispatch_receipt_refs: tuple[StrictArtifactRef, ...] = Field(min_length=12, max_length=12)
    publication_refs: tuple[StrictArtifactRef, ...] = Field(min_length=12, max_length=12)
    raw_response_sha256s: tuple[str, ...] = Field(min_length=12, max_length=12)
    panel_policy_sha256: str
```

`verify_panel_receipt(private_root, exclusions, receipt_bytes)` canonical-loads the receipt, reopens the exact private-root manifest/session/four reports/twelve assignments/dispatch receipts/publications, recomputes all response digests and role counts, and returns zero-write verified evidence. The CLI requires the same full root option set as every other read command.

- [ ] **Step 1: Write process concurrency REDs**

```python
def test_identical_prepare_and_review_writers_converge(process_case: ProcessCase) -> None:
    outcomes = process_case.spawn_identical_writers(count=2)
    assert outcomes[0].reference == outcomes[1].reference
    assert outcomes[0].error is None and outcomes[1].error is None
    reopened = process_case.reopen_disk_state()
    assert reopened.event_chain == process_case.exact_single_chain
    assert reopened.object_inventory == process_case.exact_single_inventory


def test_conflicting_review_writers_have_one_typed_loser(process_case: ProcessCase) -> None:
    outcomes = process_case.spawn_conflicting_review_writers()
    successes = [item for item in outcomes if item.reference is not None]
    failures = [item for item in outcomes if item.finding_kind is not None]
    assert len(successes) == len(failures) == 1
    assert failures[0].finding_kind == "review-assignment-conflict"
    assert process_case.reopen_disk_state().current_refs == successes[0].expected_refs
```

- [ ] **Step 2: Write literal process-death boundary REDs**

Inject `os._exit()` after attempt object, completion event, bundle object, dispatch-receipt object/event, review object, each finding object, each external-access-record object, review-publication object/event, report object, approval object, repair-started event, successor attempt, protocol-regression object, and closure object. Define an explicit table-driven oracle per boundary with: `read_outcome` (`complete`, `incomplete`, or `corrupt`), exact `retry_created_object_refs`, exact `retry_appended_event_ids` (including zero), and `divergent_retry_error`. Object-before-event boundaries require strict read failure followed by only their missing event/object suffix; after-event/fully committed boundaries require successful zero-write read and zero-delta exact retry. Multi-object review boundaries enumerate the remaining finding/access-record/publication/event suffix rather than calling all of it a completion event. Every read preserves complete tree inventory/bytes/uid/mode/nlink/inodes, and divergent retry remains byte-identical. A read command is never allowed to heal.

- [ ] **Step 3: Implement only the minimal recovery/concurrency fixes exposed by REDs**

Use the one session lock, event predecessor/sequence contract, immutable object publication, and exact retry. Do not add Factory two-pointer machinery or background recovery.

- [ ] **Step 4: Run process GREEN**

Run: `uv run pytest tests/integration/test_personal_validation_processes.py -q`

Expected: every child is bounded, joined, and terminated in `finally`; no leftover locks/processes.

- [ ] **Step 5: Verify deterministic panel wiring, then dispatch the real three-Agent panel**

Pytest uses deterministic canonical fake Agent responses only to verify wiring, role separation, publication DAG, and reducer behavior. It is never labeled a real Agent evaluation.

The external Codex orchestration gate then dispatches actual agents. It accepts bare canonical JSON bytes only (Markdown/code fences fail), writes responses solely through the service, and produces a non-private verification receipt binding the explicit private-root identity, session/report refs, assignment/publication refs, dispatch receipts, response digests, and panel policy version. A separate verifier reopens those explicit refs and proves 12 review publications; it never scans latest.

For each case:

1. issue Scientific and Evidence `ReviewAssignment` refs;
2. start the two Reviewer Agent invocations concurrently with only their exact role bundle, wait for the launch handshake that supplies observed invocation/model/runtime/time, then record immutable `AgentDispatchReceipt` refs before accepting any response;
3. validate and record their canonical JSON responses against those receipts into review/finding/access/publication refs;
4. issue Synthesis only after both publication refs are immutable;
5. record Synthesis and finalize the report from the complete finding union.

The runbook includes the exact response JSON schema and forbids agents from writing the repo/private store directly. Agents may use necessary read-only network/Zotero checks under the recorded policy; otherwise they remain local-only.

- [ ] **Step 6: Assert advisory outcomes and no hidden release claim**

```python
def test_agent_panel_reports_are_personal_only(panel_results: tuple[PersonalValidationReport, ...]) -> None:
    assert len(panel_results) == 4
    assert all(report.blocks == () for report in panel_results)
    assert all(report.hidden_evaluation_status == "not-run" for report in panel_results)
    assert all(report.product_release_status == "scientific_release_pending" for report in panel_results)
    assert all("aggregate_score" not in report.model_fields for report in panel_results)
```

Add `verify-panel` RED/GREEN cases for a canonical valid receipt, one-byte receipt mutation, missing assignment/dispatch/publication/report refs, wrong private-root `(dev, ino)`, role-count substitution, and raw-response digest substitution. Snapshot the entire private tree before/after every verifier call and forbid all write/create/reconcile primitives; successful and failed verification are both zero-write.

- [ ] **Step 7: Request independent Agent-panel/recovery review and commit**

Review checks role separation, oracle leakage, external-access provenance, process cleanup, identical/conflicting writers, literal crash boundaries, and report union completeness.

Commit: `test(validation): verify personal agent panel`

---

### Task 10: Operator guide, complete regression gate, and final personal baseline

**Files:**
- Create: `tools/verify_personal_validation_slice.py`
- Create: `tests/unit/test_verify_personal_validation_slice.py`
- Create: `docs/personal-usability-validation-operator-guide.md`
- Modify: `docs/superpowers/specs/2026-08-21-personal-usability-validation-design.md`
- Modify: `.claude/project-memory/environmental-research-os.md`
- Create/Update: `.superpowers/sdd/2026-08-21-personal-usability-validation/task-10-report.md`
- Modify: `tests/integration/test_personal_validation_cli.py`
- Update: bound Obsidian `Daily/2026-08-21.md` and `00-Hub.md`

**Interfaces:**
- Consumes: completed Task 1-9 implementation and exact four-case Agent reports.
- Produces: documented one-command personal workflow, final verification evidence, and implementation-complete status without changing product release state.

- [ ] **Step 1: Write operator acceptance REDs**

```python
def test_operator_workflow_has_no_manual_scoring_or_system_block(test_operator_guide: str) -> None:
    assert "personal-validation prepare" in test_operator_guide
    assert "Scientific Reviewer Agent" in test_operator_guide
    assert "Evidence Reviewer Agent" in test_operator_guide
    assert "Synthesis Reviewer Agent" in test_operator_guide
    assert "does not disable" in test_operator_guide
    assert "scientific_release_pending" in test_operator_guide
```

Also add a CLI acceptance test that runs `prepare -> prepare-bundle -> assign-review -> record-dispatch -> record-review` for both primaries, then the same full bundle/assignment/dispatch/record path for Synthesis, `evaluate-case -> finalize-report -> status/report -> verify-panel`; for an approved repair it runs `record-replacement -> record-repair-operations -> propose-repair -> record-owner-decision -> approve-repair -> apply-repair -> successor three-Agent review/evaluate/report -> finalize-protocol-regression -> finalize-repair -> repair-status`. Each command consumes only exact ref/JSON files emitted by the immediately preceding steps; fixtures may not preseed intermediate authority refs.

- [ ] **Step 2: Write the operator guide**

Document:

- private-root creation and separation;
- one-command Codex workflow and exact CLI recovery commands;
- four canonical case meanings, including correct-stop as success;
- three Agent roles and read-only network/Zotero allowance;
- advisory states and why they never disable the system;
- explicit repair approval and immutable before/after evidence;
- status/report zero-write recovery behavior;
- no real data and no formal hidden evaluation in this slice; and
- future real-data trigger with access/license/provenance/cost approval.

- [ ] **Step 3: Run the focused Personal Validation gate**

Run:

```bash
uv run pytest \
  tests/unit/test_exit_registry_pinned.py \
  tests/unit/test_secure_journal_read_only.py \
  tests/unit/test_personal_validation_contracts.py \
  tests/unit/test_personal_validation_snapshots.py \
  tests/unit/test_personal_validation_review_reducer.py \
  tests/unit/test_personal_validation_report.py \
  tests/unit/test_personal_validation_repairs.py \
  tests/integration/test_research_stop_inspection.py \
  tests/integration/test_personal_validation_cases.py \
  tests/integration/test_personal_validation_authority.py \
  tests/integration/test_personal_validation_recovery.py \
  tests/integration/test_personal_validation_reviews.py \
  tests/integration/test_personal_validation_repairs.py \
  tests/integration/test_personal_validation_cli.py \
  tests/integration/test_personal_validation_processes.py \
  tests/integration/test_personal_validation_agent_panel.py -q
```

- [ ] **Step 4: Run affected Research/Factory/Paper/storage regressions**

Run:

```bash
uv run pytest \
  tests/unit/test_exit_registry_current.py \
  tests/integration/test_research_journal_security.py \
  tests/integration/test_research_orchestrator_recovery.py \
  tests/integration/test_factory_run.py \
  tests/integration/test_factory_run_authority.py \
  tests/integration/test_factory_cli.py \
  tests/integration/test_factory_root_safety.py \
  tests/integration/test_paper_revision.py \
  tests/integration/test_paper_release_revision.py \
  tests/integration/test_paper_cli.py -q
```

Because Task 1 changes shared storage primitives, also run the complete suites serially and record honest environment-gated skips:

```bash
uv run pytest tests/unit -q
uv run pytest tests/integration -q
```

- [ ] **Step 5: Run complete static, format, line, diff, payload, and process gates**

Run:

```bash
uv run pytest tests/unit/test_verify_personal_validation_slice.py -q
uv run ruff check src tests
SLICE_BASE=66bbf57
uv run ruff format --check $(git diff --name-only --diff-filter=ACMR "$SLICE_BASE"...HEAD -- '*.py') $(git ls-files --others --exclude-standard -- '*.py')
uv run mypy src/envresearch
uv lock --check
git diff --check
uv run python tools/verify_personal_validation_slice.py --base "$SLICE_BASE" --max-source-lines 400 --max-payload-bytes 1048576 --fail-on-archive --fail-on-binary --fail-on-secret --fail-on-live-test-process
```

The verifier enumerates both committed diff paths and untracked files, emits canonical JSON evidence, and exits nonzero for any Python/R file above 400 lines, archive/binary/secret candidate, payload above 1 MiB, or live pytest/coverage/known child process. Unit tests seed one violation of each kind and require nonzero exit plus the exact finding key; the clean slice output is persisted in the Task 10 report.

- [ ] **Step 6: Run final four-case baseline from a clean commit**

Create a fresh private validation root outside repo/worktrees/Obsidian. Run all four cases and the three-Agent panel. If any unresolved Important/Critical appears, keep its exact report state visible, propose repair, request owner approval only for the exact repair, apply/rerun, then rerun all four cases. Do not change canonical inputs to make a failure pass.

Completion evidence must show:

- exactly one current final-generation attempt for each of four cases and exactly three current review publications per final attempt; all predecessor attempts/reviews remain immutable and are counted separately;
- correct-stop has no Factory/Paper/empirical result object or current pointer;
- no unresolved Critical/Important in the final personal baseline;
- every accepted repair closure binds the successor three-Agent report/evaluation and the final four-case `ProtocolRegressionReport`;
- no product/pack/benchmark/regression promotion;
- authoritative product roots unchanged;
- `blocks=()`, hidden evaluation `not-run`, and product `scientific_release_pending`.

- [ ] **Step 7: Request final independent whole-slice review**

Require zero Critical and zero Important across contracts, scientific behavior, exact authority, Agent provenance, read-only status, recovery, repair, CLI, docs, and test honesty. Witness REDs and fix any load-bearing finding before completion.

- [ ] **Step 8: Update status/report and commit**

Update the design status to implementation complete/personal baseline passed only if the exact final evidence supports it. Record honest unresolved advisory findings if any; never call them passed. Update repo-local project memory and the bound Obsidian Daily/Hub.

Commit: `feat(validation): complete personal usability baseline`

---

## Plan self-review checklist

- [ ] Every design section 1-17 maps to at least one task above.
- [ ] No task calls or changes formal `ReleaseEvaluator` or product-release state.
- [ ] Existing-target and disposable-run modes remain separate in every service/CLI path.
- [ ] Correct-stop absence is derived from cross-root inventory, not caller assertion.
- [ ] Attempt/event and assignment→receipt→review→finding→publication→report construction are acyclic and ordered.
- [ ] Review assignments prove dispatch binding without pretending cryptographic Agent identity.
- [ ] Report state is computed from a persisted case evaluation and the complete three-publication finding union, never a caller boolean.
- [ ] Repair closure preserves every predecessor finding/exact input snapshot and binds successor reviews/report/evaluation plus four-case regression evidence.
- [ ] Status/report use strict existing pinned roots and never reconcile/write.
- [ ] Nested private roots are opened from the retained top descriptor and every owned descriptor has bounded cleanup.
- [ ] All external operations in this slice are read-only and provenance-bound.
- [ ] Every new/modified source or test file has a stated responsibility and stays under 400 lines.

## Spec coverage map

| Spec section | Implementation evidence |
|---|---|
| 1 Purpose | Tasks 5, 6, 9, and 10 deliver four cases and three advisory Agent reviews. |
| 2 Goals | Tasks 2-10 cover exact binding, repair history, private storage, external provenance, and unchanged product state. |
| 3 Non-goals | Global Constraints plus Tasks 6, 9, and 10 prohibit formal evaluation, real data, external mutation, and promotion. |
| 4 Design principles | Tasks 2, 4, 6, and 7 implement thin adapter, diagnostic state, exact evidence, no score, and immutable repairs. |
| 5 Architecture | Tasks 1-8 create the isolated package, injected Factory service, research inspector, and Codex-facing bundle boundary. |
| 6 Canonical cases | Task 5 implements exactly four immutable versioned cases. |
| 7 Contracts | Task 2 implements protocol/snapshot/case/attempt/review/finding/report/repair contracts. |
| 8 Status semantics | Tasks 2 and 6 implement complete-union deterministic reduction and closure-only resolution. |
| 9 ReviewBundle/panel | Tasks 6 and 9 implement role projections, parallel primary review, then Synthesis. |
| 10 Network/Zotero | Tasks 2, 6, and 9 implement allowlisted read-only provenance and private-canary rejection. |
| 11 Storage/recovery | Tasks 1 and 4 implement explicit pinned roots, never-reconcile reads, event DAG, and exact retry. |
| 12 Repair/rerun | Task 7 implements proposal, owner approval, disposable successor, exact closure, and full rerun. |
| 13 CLI | Task 8 implements every reference-only command and deterministic JSON error. |
| 14 Errors | Tasks 2, 4, 6, 7, and 8 define stable typed boundaries without system blocking. |
| 15 Testing | Every task witnesses RED/GREEN; Tasks 9-10 add process, Agent, regression, static, payload, and cleanup gates. |
| 16 Completion | Task 10 performs the requirement-by-requirement final baseline audit. |
| 17 Future real data | Task 10 documents the separate future access/license/provenance/cost gate without acquiring data now. |
