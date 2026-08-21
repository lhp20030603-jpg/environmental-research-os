# V1.0 Governed Research Factory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compose one exact approved V0.2 design and one exact current V0.4
paper release into an immutable, independently reopenable research run whose
individual release promotion requires a separate authenticated human decision.

**Architecture:** Add a focused `envresearch.factory` orchestration package
over the existing V0.2 research, V0.3.1 evidence, and V0.4 paper authorities.
The package creates an exact V0.2 terminal adapter, independently reconstructs
cross-stage coherence, publishes immutable run and promotion artifacts with
prepared/committed pointers, and exposes reference-only read-only status. It
does not add scientific methods or claim that the deferred hidden suite passed.

**Tech Stack:** Python 3.13, Pydantic v2 frozen models, existing
`ArtifactRef`/`ExitRegistry`/`StoreFiles`, existing V0.2 gate and principal
authorities, existing `PaperReleaseService`, Typer, pytest, Ruff, Mypy, uv,
repository-owned synthetic fixtures, and the sealed V0.3.1/V0.4 formal root.

## Global Constraints

- `[RF-REQ-001]` Every public operation consumes caller-supplied exact
  references. Never scan for `latest` or silently substitute another version.
- `[RF-REQ-002]` Assembly and status reopen canonical bytes, identities,
  current pointers, predecessor chains, and raw output authorities.
- `[RF-REQ-003]` Compare only typed design/evidence fields. Missing support is
  an error; natural-language similarity is not a coherence rule.
- The first slice asserts `retrospective-coherence`, not historical
  design-to-execution provenance; existing V0.4 artifacts lack that exact edge.
- `[RF-REQ-004]` The requester/producer principal cannot decide the promotion.
- `[RF-REQ-005]` Run and promotion artifacts are immutable; public state is
  derived from live authority.
- `[RF-REQ-006]` Prepared and committed pointers must agree before a handoff is
  current. Recovery is compare-and-restore and cannot clobber another writer.
- `[RF-REQ-007]` Status is read-only: no directory, key, lock, object, pointer,
  chmod, or repair mutation.
- `[RF-REQ-008]` Research, V0.3.1, paper, factory, and protected-control roots
  must be pairwise physically non-overlapping.
- `[RF-REQ-009]` Identical concurrent work converges; conflicting work fails
  without split-brain state; every supported crash boundary is retryable or
  fail-closed.
- Service-generated assembly/request timestamps stay out of content-addressed
  candidates. Operational times live only in append-only authenticated events;
  the human-supplied signed decision retains its UTC decision time.
- `[RF-REQ-010]` Individual-run promotion does not satisfy the product-level
  hidden-evaluation or V1.0 scientific-release exit.
- Use only stable public codes: `FACTORY_AUTHORITY_INVALID`,
  `FACTORY_INTEGRITY_INVALID`, `FACTORY_SUPPORT_INVALID`,
  `FACTORY_SCOPE_EXCEEDED`, `FACTORY_PROMOTION_REQUIRED`, and
  `FACTORY_PROMOTION_REJECTED`.
- Every changed Python or R file must remain at or below 400 physical lines.
- Every behavior change follows witnessed RED -> minimal GREEN -> refactor.
- A task is not complete until a fresh read-only code review reports zero
  Critical and zero Important findings for that slice.
- Do not install packages, acquire data, register a method, refit an estimator,
  submit a paper, push a branch, or mutate the sealed acceptance roots.

---

## File map

- `src/envresearch/factory/errors.py`: stable V1 orchestration error boundary.
- `src/envresearch/factory/design_contracts.py`: strict V0.2 terminal handoff.
- `src/envresearch/factory/design_resolver.py`: exact read-only V0.2 reopen and
  design-handoff publication.
- `src/envresearch/factory/contracts.py`: run and cross-stage binding models.
- `src/envresearch/factory/coherence.py`: independent typed-field comparison.
- `src/envresearch/factory/_store.py`: canonical single-read objects and
  prepared/committed run pointers.
- `src/envresearch/factory/authority.py`: fixed composite lease and root checks.
- `src/envresearch/factory/service.py`: run assembly and read-only status.
- `src/envresearch/factory/promotion_contracts.py`: context, decision, status.
- `src/envresearch/factory/_promotion_store.py`: promotion two-phase storage.
- `src/envresearch/factory/promotion.py`: independent human promotion service.
- `src/envresearch/factory/cli.py`: reference-only deterministic JSON CLI.
- `tests/integration/factory_fixtures.py`: genuine V0.2 + V0.4 connected fixture.
- `tests/integration/factory_process_fixtures.py`: spawn-only crash/writer helpers.

---

### Task 1: Exact V0.2 Approved-Design Handoff

**Files:**
- Create: `src/envresearch/factory/__init__.py`
- Create: `src/envresearch/factory/errors.py`
- Create: `src/envresearch/factory/design_contracts.py`
- Create: `src/envresearch/factory/design_resolver.py`
- Modify: `src/envresearch/research/final_integrity.py`
- Test: `tests/unit/test_factory_design_contracts.py`
- Test: `tests/integration/test_factory_design_resolver.py`
- Create: `tests/integration/factory_fixtures.py`

**Interfaces:**
- Consumes: `ResearchRunManifest`, `AnalysisPlanPayload`, `ArtifactRef`,
  `BoundGateContext`, `GateRequest`, `NodeCheckpoint`, `terminal_refs()`,
  `ResearchArtifactLifecycle`, `NodeCheckpointStore`, and `ExitRegistry`.
- Produces:
  `ResearchFileEvidence`, `ApprovedDesignHandoff`, `FinalApprovalState`,
  `reopen_complete_final_exact(...) -> FinalApprovalState`,
  `V02ApprovedDesignResolver.build(plan_ref, context_ref) -> ArtifactRef`,
  `resolve(handoff_ref) -> ApprovedDesignHandoff`,
  `require_current(handoff_ref) -> None`, and `authority_lease()`.

- [ ] **Step 1: Write strict contract REDs**

  Define tests for frozen/extra-forbid models, lowercase SHA-256, safe relative
  file paths, strict nested `ArtifactRef`, a paired approved gate/context, exact
  terminal checkpoint inputs, and method-profile digests:

  ```python
  handoff = ApprovedDesignHandoff(
      schema_version="factory.approved-design.v1",
      design_id=approved_design_id(plan_ref, context_ref),
      producer="research-factory-design-adapter-v1",
      manifest=manifest,
      manifest_evidence=ResearchFileEvidence(
          relative_path="research-run-manifest.json",
          sha256=manifest_sha256,
          size_bytes=len(manifest_bytes),
      ),
      plan_ref=plan_ref,
      plan=plan_payload,
      final_context_ref=context_ref,
      final_context=context,
      final_gate=approved_gate,
      terminal_checkpoint=checkpoint,
      decision_log_evidence=decision_log_evidence,
      method_profile_sha256=manifest.method_profile_sha256,
  )
  with pytest.raises(ValidationError):
      ApprovedDesignHandoff.model_validate(
          {**handoff.model_dump(), "method_profile_sha256": {}}
      )
  ```

- [ ] **Step 2: Run contract RED**

  Run:

  ```bash
  uv run pytest tests/unit/test_factory_design_contracts.py -q
  ```

  Expected: collection error for missing
  `envresearch.factory.design_contracts`.

- [ ] **Step 3: Implement the strict contracts and errors**

  Add the public error family:

  ```python
  class FactoryError(ValueError):
      code: ClassVar[str]

      def __init__(self, message: str, *, finding_kind: str) -> None:
          super().__init__(message)
          self.finding_kind = finding_kind

  class FactoryAuthorityInvalid(FactoryError):
      code = "FACTORY_AUTHORITY_INVALID"

  class FactoryIntegrityInvalid(FactoryError):
      code = "FACTORY_INTEGRITY_INVALID"

  class FactorySupportInvalid(FactoryError):
      code = "FACTORY_SUPPORT_INVALID"

  class FactoryScopeExceeded(FactoryError):
      code = "FACTORY_SCOPE_EXCEEDED"
  ```

  Use `ConfigDict(extra="forbid", frozen=True, strict=True)` and
  `BeforeValidator` wrappers that dump and strictly revalidate already-created
  nested Pydantic instances. Compute `design_id` from canonical full plan and
  context references, not from a path or truncated hash.

- [ ] **Step 4: Run contract GREEN**

  Run:

  ```bash
  uv run pytest tests/unit/test_factory_design_contracts.py -q
  ```

  Expected: all strict, canonical, and nested-forgery cases pass.

- [ ] **Step 5: Write genuine final-gate reopen REDs**

  Reuse `tests/integration/orchestrator_fixtures.py` to complete a real V0.2
  run through Final Gate. Require an explicit approved plan ref and an explicit
  synthetic final-context ref:

  ```python
  plan_ref = orchestrator.lifecycle.artifact_ref(
      Path("artifacts/analysis-plan.yaml")
  )
  context = orchestrator.bound_gates.active_context("final-gate")
  assert context is not None and context.context_hash is not None
  context_ref = ArtifactRef(
      artifact_id="final-gate-context",
      artifact_version=context.revision,
      content_hash=context.context_hash,
  )
  ref = resolver.build(plan_ref, context_ref)
  assert resolver.resolve(ref).plan_ref == plan_ref
  ```

  Add failures for a validated-but-unapproved plan, wrong context version/hash,
  superseded context, mutated decision log, changed method-profile digest,
  mutated checkpoint, plan bytes, hardlink/symlink substitution, and a current
  pointer advance during the final reopen. Snapshot the research root and prove
  `resolve()`/`require_current()` make no byte or mode changes.

- [ ] **Step 6: Run resolver RED**

  Run:

  ```bash
  uv run pytest tests/integration/test_factory_design_resolver.py -q
  ```

  Expected: failure because `reopen_complete_final_exact` and
  `V02ApprovedDesignResolver` do not exist.

- [ ] **Step 7: Add a read-only exact Final Gate reopen seam**

  In `research/final_integrity.py`, separate validation from the legacy
  promotion call:

  ```python
  @dataclass(frozen=True)
  class FinalApprovalState:
      plan_ref: ArtifactRef
      plan: AnalysisPlanPayload
      context_ref: ArtifactRef
      context: BoundGateContext
      gate: GateRequest
      checkpoint: NodeCheckpoint
      terminal_inputs: tuple[ArtifactRef, ...]

  def reopen_complete_final_exact(
      *, lifecycle: ResearchArtifactLifecycle,
      gates: BoundGateManager,
      checkpoints: NodeCheckpointStore,
      nodes: Mapping[str, ArtifactNode],
      semantics: SemanticSubmissionValidator,
      plan_ref: ArtifactRef,
      context_ref: ArtifactRef,
      audit: ResearchAuditState | None = None,
  ) -> FinalApprovalState: ...
  ```

  It must read existing history and verify the terminal checkpoint without
  calling `promote_status`, creating a checkpoint, or selecting the active
  context as a substitute for the supplied exact reference.

- [ ] **Step 8: Implement exact adapter publication and status**

  `build()` holds the V0.2 mutation lease and the design-handoff subject lock,
  reopens the exact final state before and after immutable publication, then
  installs prepared/current pointers. `resolve()` reads object bytes once,
  verifies SHA-256, canonical JSON, ID/version, pointer equality, and reruns the
  V0.2 reconstruction. Map raw `OSError`/`ValidationError` to stable factory
  errors.

- [ ] **Step 9: Run Task 1 full gate and static checks**

  Run:

  ```bash
  uv run pytest tests/unit/test_factory_design_contracts.py \
    tests/integration/test_factory_design_resolver.py \
    tests/integration/test_research_orchestrator_integrity.py -q
  uv run ruff check src/envresearch/factory \
    src/envresearch/research/final_integrity.py \
    tests/unit/test_factory_design_contracts.py \
    tests/integration/test_factory_design_resolver.py \
    tests/integration/factory_fixtures.py
  uv run mypy src/envresearch/factory \
    src/envresearch/research/final_integrity.py
  git diff --check
  ```

  Expected: all tests and static gates pass; every changed Python file is at
  most 400 lines.

- [ ] **Step 10: Obtain a read-only review and commit Task 1**

  Require zero Critical and zero Important findings, then run:

  ```bash
  git add src/envresearch/factory src/envresearch/research/final_integrity.py \
    tests/unit/test_factory_design_contracts.py \
    tests/integration/test_factory_design_resolver.py \
    tests/integration/factory_fixtures.py
  git commit -m "feat(factory): bind approved research design"
  ```

---

### Task 2: Governed Run Assembly and Independent Coherence

**Files:**
- Create: `src/envresearch/factory/contracts.py`
- Create: `src/envresearch/factory/coherence.py`
- Create: `src/envresearch/factory/authority.py`
- Create: `src/envresearch/factory/_store.py`
- Create: `src/envresearch/factory/service.py`
- Modify: `src/envresearch/factory/__init__.py`
- Modify: `src/envresearch/paper/release.py`
- Test: `tests/unit/test_factory_run_contracts.py`
- Test: `tests/unit/test_factory_coherence.py`
- Test: `tests/integration/test_factory_run.py`
- Test: `tests/integration/test_factory_run_authority.py`

**Interfaces:**
- Consumes: `ApprovedDesignHandoff`, `V02ApprovedDesignResolver`,
  `PaperReleaseCandidate`, `PaperReleaseService`, `ClaimEvidenceLedger`,
  `ArgumentMap`, and existing V0.3.1/citation/paper authority leases.
- Produces: `BindingField`, `CrossStageBindingReport`, `ResearchFactoryRun`,
  `CapabilityProfileBinding`, `FactoryRunStatus`, `factory_run_id()`,
  `FactoryAuthority`,
  `FactoryRunService.assemble(design_ref, release_ref) -> ArtifactRef`, and
  `FactoryRunService.status(run_ref) -> FactoryRunStatus`.

- [ ] **Step 1: Write run-contract REDs**

  Require strict nested handoffs, complete canonical lineage, deterministic
  identity, and separation between immutable assembly verdict and derived
  state:

  ```python
  run = ResearchFactoryRun(
      schema_version="factory.research-run.v1",
      factory_run_id=factory_run_id(design_ref, release_ref),
      producer="research-factory-run-v1",
      design_ref=design_ref,
      design=design,
      release_ref=release_ref,
      release=release,
      binding_report=binding_report,
      artifact_refs=expected_artifact_refs,
      analysis_refs=release.analysis_refs,
      output_refs=release.output_refs,
      capability_profiles=capability_profiles,
      assembly_verdict="assembled",
  )
  assert run.model_copy(update={"release_ref": other_ref}) != run
  with pytest.raises(ValidationError):
      ResearchFactoryRun.model_validate(run.model_dump() | {"extra": 1})
  ```

- [ ] **Step 2: Run run-contract RED**

  Run:

  ```bash
  uv run pytest tests/unit/test_factory_run_contracts.py -q
  ```

  Expected: missing `envresearch.factory.contracts`.

- [ ] **Step 3: Implement frozen run and finding contracts**

  `BindingField` uses exact typed values and one relation literal:
  `exact`, `narrower`, or `blocked`. `CrossStageBindingReport` rejects any
  blocked field when `verdict="coherent"`, requires canonical field order, and
  binds every paper method/claim row. It also carries the canonical union of
  design and release limitations as `limitations: tuple[str, ...]`.
  `CapabilityProfileBinding` stores the profile ID, registered version, and
  full SHA-256 from `ResearchRunManifest.method_profile_sha256`; it is not an
  `ArtifactRef` because V0.2 profiles are registry-bound values.
  `ResearchFactoryRun` revalidates nested models and verifies that all embedded
  refs equal the embedded payloads.

  The report includes `provenance_claim="retrospective-coherence"`. It rejects
  forward-provenance labels for the pre-existing V0.4 release. Run publication
  events record operational time outside canonical run bytes, so object-only
  crash recovery rematerializes the same exact reference.

- [ ] **Step 4: Write table-driven independent coherence REDs**

  Build a clean design/release pair and mutate one typed dimension at a time:

  ```python
  @pytest.mark.parametrize(
      ("dimension", "expected_code"),
      [
          ("method", "FACTORY_SUPPORT_INVALID"),
          ("estimand", "FACTORY_SUPPORT_INVALID"),
          ("unit", "FACTORY_SCOPE_EXCEEDED"),
          ("population", "FACTORY_SCOPE_EXCEEDED"),
          ("time", "FACTORY_SCOPE_EXCEEDED"),
          ("price", "FACTORY_SCOPE_EXCEEDED"),
          ("strength", "FACTORY_SCOPE_EXCEEDED"),
          ("limitation", "FACTORY_SUPPORT_INVALID"),
      ],
  )
  def test_coherence_rejects_typed_mismatch(dimension, expected_code):
      with pytest.raises(FactoryError) as caught:
          reconstruct_binding_report(design, mutated_release(dimension), ledger)
      assert caught.value.code == expected_code
  ```

  Include one legal typed narrowing that preserves the original limitation.
  Monkeypatch V0.4 draft validators/renderers to raise and prove coherence uses
  ledger/map typed artifacts rather than Paper Builder acceptance or prose.

- [ ] **Step 5: Run coherence RED**

  Run:

  ```bash
  uv run pytest tests/unit/test_factory_coherence.py -q
  ```

  Expected: failures because independent reconstruction is absent.

- [ ] **Step 6: Implement minimal typed coherence reconstruction**

  Reopen the release's exact ledger and map under Paper authority. Compare the
  plan primary/alternative method profile IDs to ledger method IDs through one
  explicit registry mapping. Compare embedded estimand and basis fields only
  when both contracts expose them. Reject absence of a required binding instead
  of filling it from prose. Return sorted `BindingField` values and complete
  exact provenance.

- [ ] **Step 7: Write genuine assembly/status/recovery REDs**

  Create a connected fixture that completes a real V0.2 Final Gate and builds a
  real V0.4 release from evidence compatible with that plan. Assert:

  ```python
  ref = service.assemble(design_ref, release_ref)
  status = service.status(ref)
  assert status.state == "promotion-required"
  assert status.run_ref == ref
  assert status.run.binding_report.verdict == "coherent"
  assert service.assemble(design_ref, release_ref) == ref
  ```

  Add exact design/release/transition/output mutations before publication,
  after immutable publication, after prepared pointer, before commit, and after
  commit. Add pointer write-then-raise, object byte swap, noncanonical JSON,
  wrong ID/version/hash, conflicting intent, and read-only status snapshots.

- [ ] **Step 8: Run service RED**

  Run:

  ```bash
  uv run pytest tests/integration/test_factory_run.py \
    tests/integration/test_factory_run_authority.py -q
  ```

  Expected: failures because `FactoryRunService` and storage do not exist.

- [ ] **Step 9: Implement authority order, storage, assembly, and status**

  `FactoryAuthority` acquires V0.2 research, V0.3.1 global, citation, paper
  ledger/map/draft/audit/revision/release, design-handoff, then factory-run
  subjects. Reuse package-private already-locked Paper reopen seams; add one to
  `PaperReleaseService` only if calling public `status()` would reacquire a
  non-reentrant lock.

  `_FactoryRunStore` performs one-read canonical loading and exposes
  `prepared()`, `committed()`, `current()`, `prepare()`, `commit()`, and
  compare-and-restore recovery. `status()` never calls recovery and returns
  `promotion-required` only after two full upstream reconstructions and a final
  exact run-current check.

- [ ] **Step 10: Run Task 2 gates**

  Run:

  ```bash
  uv run pytest tests/unit/test_factory_run_contracts.py \
    tests/unit/test_factory_coherence.py \
    tests/integration/test_factory_run.py \
    tests/integration/test_factory_run_authority.py \
    tests/integration/test_paper_acceptance.py -q
  uv run ruff check src/envresearch/factory src/envresearch/paper/release.py \
    tests/unit/test_factory_run_contracts.py \
    tests/unit/test_factory_coherence.py \
    tests/integration/test_factory_run.py \
    tests/integration/test_factory_run_authority.py
  uv run mypy src/envresearch/factory src/envresearch/paper/release.py
  git diff --check
  ```

  Expected: all tests/static gates pass and files remain within 400 lines.

- [ ] **Step 11: Obtain review and commit Task 2**

  Require zero Critical/Important findings, then:

  ```bash
  git add src/envresearch/factory src/envresearch/paper/release.py \
    tests/unit/test_factory_run_contracts.py \
    tests/unit/test_factory_coherence.py \
    tests/integration/test_factory_run.py \
    tests/integration/test_factory_run_authority.py
  git commit -m "feat(factory): assemble governed research run"
  ```

---

### Task 3: Independent Human Promotion

**Files:**
- Create: `src/envresearch/factory/promotion_contracts.py`
- Create: `src/envresearch/factory/_promotion_store.py`
- Create: `src/envresearch/factory/promotion.py`
- Modify: `src/envresearch/factory/contracts.py`
- Modify: `src/envresearch/factory/service.py`
- Modify: `src/envresearch/factory/__init__.py`
- Test: `tests/unit/test_factory_promotion_contracts.py`
- Test: `tests/integration/test_factory_promotion.py`
- Test: `tests/integration/test_factory_promotion_authority.py`

**Interfaces:**
- Consumes: `FactoryRunService`, `ResearchFactoryRun`, `GateDecision`, and the
  existing protected principal registry/capability authority.
- Produces: `FactoryPromotionContext`, `FactoryRunPromotion`,
  `FactoryPromotionRequired`, `FactoryPromotionRejected`,
  `FactoryPromotionService.request(run_ref, requested_by) -> ArtifactRef`,
  `record(context_ref, decision, principal_capability) -> ArtifactRef`, and
  `status(promotion_ref, run_ref) -> FactoryRunStatus`. `FactoryRunService`
  remains the public facade and delegates `request_promotion(...)`,
  `record_promotion(...)`, and `promotion_status(...)` to that internal service.

- [ ] **Step 1: Write strict promotion-contract REDs**

  Require service-derived checklist fields, exact run/context binding, UTC
  timestamps, terminal decisions, and explicit hidden-suite status:

  ```python
  context = FactoryPromotionContext(
      schema_version="factory.promotion-context.v1",
      context_id=promotion_context_id(run_ref, 1),
      producer="research-factory-promotion-context-v1",
      generation=1,
      run_ref=run_ref,
      run=run,
      decision_kind="individual-run-release",
      requested_by="factory-agent",
      limitations=run.binding_report.limitations,
      checklist=derived_checklist,
      hidden_evaluation_status="not-run",
      product_release_status="scientific_release_pending",
  )
  with pytest.raises(ValidationError):
      FactoryPromotionContext.model_validate(
          context.model_dump()
          | {"hidden_evaluation_status": "passed"}
      )
  ```

  Strictly revalidate already-created nested `GateDecision` and reject a
  request/decision principal collision. The context contains no service-created
  request timestamp; the promotion request event stores that operational time.

- [ ] **Step 2: Run promotion-contract RED**

  Run:

  ```bash
  uv run pytest tests/unit/test_factory_promotion_contracts.py -q
  ```

  Expected: missing promotion contracts.

- [ ] **Step 3: Implement promotion contracts and derived statuses**

  `FactoryRunStatus.state` is one of `promotion-required`, `promoted`, or
  `promotion-rejected`. Invalid authority raises a typed error instead of
  returning an accepted payload. `FactoryRunPromotion` binds one exact context,
  decision, principal capability digest, and authenticated principal evidence.
  It cannot encode product-level V1.0 promotion.

- [ ] **Step 4: Write request/decision authority REDs**

  Test an exact request, independent approval, terminal rejection, new context
  generation after rejection, stale run, forged principal capability,
  producer/requester deciding their own request, decision predating the
  authenticated request event,
  broadened decision conditions, and a changed context after decision:

  ```python
  context_ref = promotions.request(run_ref, requested_by="factory-agent")
  with pytest.raises(ValueError, match="independent"):
      promotions.record(
          context_ref,
          approved(decided_by="factory-agent"),
          principal_capability=requester_capability,
      )
  promotion_ref = promotions.record(
      context_ref,
      approved(decided_by="human-reviewer"),
      principal_capability=reviewer_capability,
  )
  assert promotions.status(promotion_ref, run_ref).state == "promoted"
  ```

- [ ] **Step 5: Run promotion service RED**

  Run:

  ```bash
  uv run pytest tests/integration/test_factory_promotion.py \
    tests/integration/test_factory_promotion_authority.py -q
  ```

  Expected: failures because the promotion service/store are absent.

- [ ] **Step 6: Implement protected promotion request and decision**

  Derive context checklist/limitations from a freshly reopened run under the
  full composite lease. Authenticate the human principal through the existing
  protected registry; compare it against run producer, context requester, and
  contributing worker principals. Publication uses distinct prepared/committed
  subjects for context and promotion. After commit, rerun run, context,
  principal, and promotion authority before return.

- [ ] **Step 7: Add concurrency, rollback, and final-window REDs**

  Cover identical/conflicting request and decision writers, raw principal-store
  mutation, promotion object/pointer failures, process death, compare-and-
  restore lost-update protection, and real V0.2/V0.3.1/citation/paper writers
  waiting until promotion returns. A failed/rejected promotion must not change
  the current factory run.

  Public integration cases call the approved facade signatures:

  ```python
  context_ref = service.request_promotion(run_ref, requested_by="factory-agent")
  promotion_ref = service.record_promotion(
      context_ref,
      approved(decided_by="human-reviewer"),
      principal_capability=reviewer_capability,
  )
  assert service.promotion_status(promotion_ref, run_ref).state == "promoted"
  ```

- [ ] **Step 8: Run Task 3 gates**

  Run:

  ```bash
  uv run pytest tests/unit/test_factory_promotion_contracts.py \
    tests/integration/test_factory_promotion.py \
    tests/integration/test_factory_promotion_authority.py -q
  uv run ruff check src/envresearch/factory \
    tests/unit/test_factory_promotion_contracts.py \
    tests/integration/test_factory_promotion.py \
    tests/integration/test_factory_promotion_authority.py
  uv run mypy src/envresearch/factory
  git diff --check
  ```

  Expected: all tests/static/line gates pass.

- [ ] **Step 9: Obtain review and commit Task 3**

  Require zero Critical/Important findings, then:

  ```bash
  git add src/envresearch/factory \
    tests/unit/test_factory_promotion_contracts.py \
    tests/integration/test_factory_promotion.py \
    tests/integration/test_factory_promotion_authority.py
  git commit -m "feat(factory): require independent run promotion"
  ```

---

### Task 4: Exact CLI, Process Recovery, and Read-Only Roots

**Files:**
- Create: `src/envresearch/factory/cli.py`
- Modify: `src/envresearch/cli.py`
- Modify: `src/envresearch/factory/authority.py`
- Create: `tests/integration/test_factory_cli.py`
- Create: `tests/integration/test_factory_concurrency.py`
- Create: `tests/integration/factory_process_fixtures.py`
- Create: `tests/integration/test_factory_root_safety.py`

**Interfaces:**
- Consumes: Task 1--3 services and exact `ArtifactRef` JSON files.
- Produces: `factory_app`, `service_for_roots(..., create: bool)`, deterministic
  JSON commands `assemble`, `status`, `request-promotion`, `record-promotion`,
  and `promotion-status`.

- [ ] **Step 1: Write CLI REDs**

  Test only explicit reference JSON and explicit roots:

  ```python
  result = runner.invoke(
      app,
      [
          "factory", "assemble",
          str(design_ref_file), str(release_ref_file),
          "--research-root", str(research_root),
          "--v031-root", str(v031_root),
          "--paper-root", str(paper_root),
          "--factory-root", str(factory_root),
      ],
  )
  assert result.exit_code == 0
  payload = json.loads(result.stdout)
  assert payload["status"]["state"] == "promotion-required"
  ```

  Add missing/malformed reference, missing root, unsafe root, stale input,
  promotion-required exit, rejected decision, and stable JSON error cases.
  Typer parser failures must also emit deterministic JSON rather than Rich-only
  stderr.

- [ ] **Step 2: Run CLI RED**

  Run:

  ```bash
  uv run pytest tests/integration/test_factory_cli.py -q
  ```

  Expected: missing `factory` command group.

- [ ] **Step 3: Implement read-only composition and CLI**

  `service_for_roots(create=False)` uses only existing root/key/lock openers.
  It closes every queue/principal descriptor on both success and construction
  failure. `_validated_roots()` includes all derived protected roots. Register:

  ```python
  from envresearch.factory.cli import factory_app
  app.add_typer(factory_app, name="factory")
  ```

  Emit `{reference, payload, status}` on success and
  `{error: {code, finding_kind, message}}` on failure. Never emit a mutable
  filesystem path as the handoff.

- [ ] **Step 4: Write full root-safety/read-only REDs**

  Snapshot bytes, inode/link metadata, owner, and modes for research, V0.3.1,
  paper, factory, and protected-control trees. Delete one existing lock before
  each status case and require failure without recreation. Parametrize same,
  ancestor, descendant, symlink, hardlink, and derived-control overlap.

- [ ] **Step 5: Write spawn-only crash/concurrency REDs**

  In `factory_process_fixtures.py`, use `multiprocessing.get_context("spawn")`,
  bounded `attempting/acquired/release` events, `Queue.get(timeout=20)`, and a
  `finally` block that terminates and joins all children. Cover object,
  prepared, commit, and post-commit-final-check process death for design,
  factory run, context, and promotion. Assert identical writers converge and
  conflicting writers leave the original exact pointers unchanged.

- [ ] **Step 6: Run CLI/process REDs**

  Run:

  ```bash
  uv run pytest tests/integration/test_factory_cli.py \
    tests/integration/test_factory_concurrency.py \
    tests/integration/test_factory_root_safety.py -q
  ```

  Expected: failures at missing read-only/root/concurrency behavior.

- [ ] **Step 7: Close recovery and root-safety behavior**

  Extend stores only through their public prepared/committed recovery
  contracts. A retry examines authenticated intent before upstream reopen so a
  stale upstream cannot leave an unvalidated pointer exposed. A post-commit
  failure compare-and-restores only its own exact marker. Root validation occurs
  before any constructor with write capability.

- [ ] **Step 8: Run Task 4 gates**

  Run:

  ```bash
  uv run pytest tests/integration/test_factory_cli.py \
    tests/integration/test_factory_concurrency.py \
    tests/integration/test_factory_root_safety.py \
    tests/integration/test_factory_run.py \
    tests/integration/test_factory_promotion.py -q
  uv run ruff check src/envresearch/factory src/envresearch/cli.py \
    tests/integration/test_factory_cli.py \
    tests/integration/test_factory_concurrency.py \
    tests/integration/test_factory_root_safety.py \
    tests/integration/factory_process_fixtures.py
  uv run ruff format --check src/envresearch/factory src/envresearch/cli.py \
    tests/integration/test_factory_cli.py \
    tests/integration/test_factory_concurrency.py \
    tests/integration/test_factory_root_safety.py \
    tests/integration/factory_process_fixtures.py
  uv run mypy src/envresearch/factory src/envresearch/cli.py
  uv lock --check
  git diff --check
  ```

  Expected: all gates pass and no changed Python file exceeds 400 lines.

- [ ] **Step 9: Obtain review and commit Task 4**

  Require zero Critical/Important findings, then:

  ```bash
  git add src/envresearch/factory src/envresearch/cli.py \
    tests/integration/test_factory_cli.py \
    tests/integration/test_factory_concurrency.py \
    tests/integration/test_factory_root_safety.py \
    tests/integration/factory_process_fixtures.py
  git commit -m "feat(factory): add exact governed-run CLI"
  ```

---

### Task 5: Formal Acceptance and Next-Slice Handoff

**Files:**
- Create: `tests/integration/test_factory_acceptance.py`
- Create: `docs/research-factory-v1-operator-guide.md`
- Modify: `docs/superpowers/specs/2026-08-14-v1-governed-research-factory-design.md`
- Modify: `plan/2026-08-04-环境经济学与政策研究操作系统-设计规范.md`
- Modify: `.claude/project-memory/environmental-research-os.md`
- Create: `.superpowers/sdd/2026-08-14-v1-governed-research-factory/task-5-report.md`

**Interfaces:**
- Consumes: complete Tasks 1--4 and the sealed V0.3.1/V0.4 acceptance root.
- Produces: one exact current run/promotion handoff, operator documentation,
  formal gate evidence, and an explicit hidden-evaluation next-slice boundary.

- [ ] **Step 1: Write formal acceptance REDs**

  Add one always-running synthetic connected run and one environment-gated
  sealed-root path:

  ```python
  @pytest.mark.skipif(
      "ENVRESEARCH_V04_ACCEPTANCE_ROOT" not in os.environ,
      reason="formal sealed V0.4 root is operator supplied",
  )
  def test_sealed_release_assembles_exact_factory_run():
      ref = factory.assemble(design_ref, release_ref)
      reopened = factory.status(ref)
      assert reopened.state == "promotion-required"
      assert reopened.run.release_ref == release_ref
      assert reopened.run.design_ref == design_ref
  ```

  The formal case must copy the reviewed roots before destructive mutation
  tests and must not alter the original sealed roots.

- [ ] **Step 2: Run acceptance RED**

  Run:

  ```bash
  uv run pytest tests/integration/test_factory_acceptance.py -q
  ```

  Expected: the synthetic case exposes any remaining handoff/composition gap;
  the formal case is an honest expected skip when the root is absent.

- [ ] **Step 3: Close only acceptance-surface defects**

  Fix failures without changing methods, fixtures, thresholds, models,
  estimands, hidden-evaluation status, or V0.4 accepted evidence. Any required
  scientific change stops Task 5 and returns to design review.

- [ ] **Step 4: Run the authoritative affected suite**

  Run:

  ```bash
  uv run pytest tests/unit/test_factory_*.py \
    tests/integration/test_factory_*.py \
    tests/integration/test_research_*.py \
    tests/integration/test_paper_*.py \
    tests/integration/test_econometrics_valuation_*.py -q
  ```

  Expected: zero failures; only documented environment/privilege skips.

- [ ] **Step 5: Run complete repository and coverage gates**

  Run sequentially:

  ```bash
  uv run pytest tests/unit -q
  uv run pytest tests/integration -q
  uv run coverage erase
  uv run coverage run -m pytest tests/unit tests/integration -q
  uv run coverage report
  uv run ruff check .
  uv run mypy src
  uv lock --check
  git diff --check
  ```

  Run `ruff format --check` only over changed Python files. Verify every changed
  Python/R file is at most 400 physical lines and scan changed/untracked payloads
  for archives, binaries, secrets, and files larger than 1 MiB.

- [ ] **Step 6: Obtain independent formal reviews**

  Require separate read-only reviews of:

  1. exact design-to-release scientific coherence;
  2. authority/TOCTOU/deadlock/recovery/root safety;
  3. promotion principal separation and no self-approval;
  4. test honesty and formal-root evidence; and
  5. documentation claims versus fresh gate output.

  Any Critical or Important finding returns to a witnessed RED before the
  verdict can become PASS.

- [ ] **Step 7: Write operator and next-slice documentation**

  Document exact reference files, root layout, build/status/promotion commands,
  deterministic exit codes, recovery procedure, read-only expectations, and
  the distinction between individual-run promotion and product-level
  `scientific_release_pending`. Record exact fresh refs/hashes/counts rather
  than copying anticipated values from this plan.

- [ ] **Step 8: Update design status and project memory**

  Mark the design `Implemented and independently reviewed` only after all gates
  pass. Update repo-local project memory, the bound Obsidian Daily note, and the
  Hub because the top-level project phase changes. State that hidden evaluation,
  capability-pack evolution, and replication-package evaluation remain the next
  V1.0 slice.

- [ ] **Step 9: Re-run post-document static gates**

  Run:

  ```bash
  uv run ruff check .
  uv run mypy src
  uv lock --check
  git diff --check
  ```

  Re-run the changed-file formatter, 400-line, and payload scans after the
  documentation/report write-back.

- [ ] **Step 10: Commit the governed-run slice**

  Stage only reviewed paths, never `git add -A` because execution roots under
  `temp/` are intentionally untracked:

  ```bash
  git add src/envresearch/factory src/envresearch/research/final_integrity.py \
    src/envresearch/paper/release.py src/envresearch/cli.py \
    tests/unit/test_factory_*.py tests/integration/test_factory_*.py \
    tests/integration/factory_fixtures.py \
    tests/integration/factory_process_fixtures.py \
    docs/research-factory-v1-operator-guide.md \
    docs/superpowers/specs/2026-08-14-v1-governed-research-factory-design.md \
    plan/2026-08-04-环境经济学与政策研究操作系统-设计规范.md \
    .claude/project-memory/environmental-research-os.md
  git commit -m "feat(factory): deliver governed research run"
  ```

  Do not push or create a PR without a separate user request.
