# V0.4 Paper Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the exact current V0.3.1 evidence transition into a current,
audited paper candidate whose claims, numbers, citations, tables, figures, and
limitations can all be independently reopened.

**Architecture:** Add a focused `envresearch.paper` package on top of the
existing content-addressed artifact kernel. V0.4 reuses `ArtifactRef`,
`ExitRegistry`, `V031ExitHarness`, and independently reconstructed
`LocalAnalysisReport` payloads; it never copies estimator logic into the
writing layer. Each stage publishes an immutable object, promotes one locked
current pointer only after revalidation, and reopens every upstream exact ref
on status.

**Tech Stack:** Python 3.13, Pydantic v2 frozen models, existing
`ExitRegistry`/`StoreFiles`, pytest, Ruff, Mypy, uv, repository-owned local
fixtures, and the sealed V0.3.1 frozen-R transition for the formal acceptance
path.

## Global Constraints

- `[PB-REQ-001]` The only Valuation Core input is one caller-supplied exact
  current `econometrics.v031-transition.v1` `ArtifactRef`; no latest/path scan.
- `[PB-REQ-002]` Every proposed empirical claim must bind its exact transition,
  analysis report, snapshot, raw outputs, and one typed reconstructed value:
  either an estimate with uncertainty, a descriptive range, or a counted
  series. Every row also binds a generic unit, population/time/price basis,
  allowed strength, explicit uncertainty status, and limitations.
- `[PB-REQ-003]` Argument nodes containing empirical content must cite current
  ledger claim IDs; the support graph must be acyclic and conclusions require
  accepted incoming support.
- `[PB-REQ-004]` Draft prose must carry exact claim-span, citation, table, and
  figure bindings; citations must resolve through a typed `CitationAuthority`
  adapter that reopens exact `CuratorSourceSheet` artifacts and the current
  lifecycle-sealed citation-integrity attestation, and numbers and scope must
  match the bound claim payloads.
- `[PB-REQ-005]` Audit must detect citation, numeric, table/figure, strength,
  policy-language, unit/population/time/price, scope, and cross-section defects.
- `[PB-REQ-006]` A release candidate is current only when all exact upstream
  refs are current and every finding is closed by a revalidated draft revision.
- `[PB-REQ-007]` Mutation, supersession, failed reconstruction, missing raw
  bytes, or a changed current pointer invalidates descendants fail-closed.
- `[PB-REQ-008]` V0.4 must not refit estimators, acquire data, install packages,
  register methods, or promote scientific significance as a release rule.
- `[PB-REQ-009]` Identical concurrent builds are idempotent; conflicting builds
  and interrupted publications are recoverable or rejected without split-brain
  current state.
- `[PB-REQ-010]` V0.2/V0.3 artifact contracts and capability gates remain
  compatible; Spatial, Exposure, Forecasting/Wave-3, and Stata stay gated.
- Every changed Python or R file must remain at or below 400 physical lines.
- Tests must follow RED -> GREEN -> REFACTOR and must exercise observable
  behavior rather than source text or mock existence.
- Use stable public error codes only: `PAPER_AUTHORITY_INVALID`,
  `PAPER_INTEGRITY_INVALID`, `PAPER_SUPPORT_INVALID`, and
  `PAPER_SCOPE_EXCEEDED`; keep detailed finding kinds orthogonal.

---

### Task 1: Exact Claim-Evidence Ledger

**Files:**
- Create: `src/envresearch/paper/__init__.py`
- Create: `src/envresearch/paper/errors.py`
- Create: `src/envresearch/paper/contracts.py`
- Create: `src/envresearch/paper/valuation_claims.py`
- Create: `src/envresearch/paper/ledger.py`
- Modify: `src/envresearch/econometrics/valuation_transition.py`
- Test: `tests/unit/test_paper_claim_contracts.py`
- Test: `tests/integration/test_paper_claim_ledger.py`

**Interfaces:**
- Consumes: `ArtifactRef`, `LocalAnalysisReference`, `LocalAnalysisReport`,
  `OutputEvidence`, `V031ExitHarness`, `accepted_analysis_reports()`, and
  `ExitRegistry`.
- Produces:
  `ClaimUncertainty`, `EstimatedClaimValue`, `DescriptiveRangeValue`,
  `DescriptiveSeriesValue`, `AnalysisOutputRef`, `ClaimEvidenceRow`,
  `ClaimEvidenceLedger`,
  `ClaimLedgerService.build(transition_ref) -> ArtifactRef`, and
  `ClaimLedgerService.status(ledger_ref, transition_ref) -> ClaimEvidenceLedger`.

- [x] **Step 1: Write strict contract REDs**

  Create literal tests that require frozen/extra-forbid models, canonical unique
  claim IDs, nonempty exact refs and outputs, independently reconstructed
  status, typed finite values, explicit basis fields, one registered strength,
  and at least one limitation. The core wished-for construction is:

  ```python
  row = ClaimEvidenceRow(
      claim_id="cv-median-wtp",
      claim_type="welfare-estimate",
      method_id="contingent-valuation",
      quantity="median-wtp",
      value=EstimatedClaimValue(
          estimate=20.0,
          uncertainty=ClaimUncertainty(
              std_error=2.0,
              confidence_low=16.08,
              confidence_high=23.92,
              confidence_level=0.95,
          ),
      ),
      transition_ref=transition_ref,
      analysis_ref=analysis_ref,
      snapshot_ref=snapshot_ref,
      output_evidence=(wtp_output, figure_output),
      reconstruction_status="independently-reconstructed",
      unit="cny",
      population_basis="sample",
      time_basis="annual",
      price_base="p2025",
      allowed_strength="model-conditional-valuation",
      limitations=("Stated-preference estimate conditional on the registered response model.",),
  )
  ```

  Add literal descriptive REDs for the reconstructed CV probability range and
  the ordered bid-level yes-share series. Counts must reconcile exactly, and a
  descriptive value must declare `uncertainty_status="not-estimated"`; it may
  not smuggle an estimator uncertainty interval into a descriptive claim.

- [x] **Step 2: Run the contract tests and verify RED**

  Run:

  ```bash
  uv run pytest tests/unit/test_paper_claim_contracts.py -v
  ```

  Expected: collection fails because `envresearch.paper.contracts` does not yet
  exist.

- [x] **Step 3: Implement the minimum frozen contracts and stable errors**

  Define the public error boundary:

  ```python
  class PaperBuilderError(ValueError):
      code: str

  class PaperAuthorityInvalid(PaperBuilderError):
      code = "PAPER_AUTHORITY_INVALID"

  class PaperIntegrityInvalid(PaperBuilderError):
      code = "PAPER_INTEGRITY_INVALID"

  class PaperSupportInvalid(PaperBuilderError):
      code = "PAPER_SUPPORT_INVALID"

  class PaperScopeExceeded(PaperBuilderError):
      code = "PAPER_SCOPE_EXCEEDED"
  ```

  Implement strict models with one validator that binds every row's
  `transition_ref` to the enclosing ledger and rejects duplicate claim IDs.

- [x] **Step 4: Run the contract tests and verify GREEN**

  Run:

  ```bash
  uv run pytest tests/unit/test_paper_claim_contracts.py -v
  ```

  Expected: all contract cases pass.

- [x] **Step 5: Write real exact-transition ingestion REDs**

  First add always-running build/status/idempotence/recovery and final-window
  race REDs using an injected repository-owned `AcceptedEvidenceResolver` that
  returns genuine typed passed reports from deterministic local fixtures. Then,
  against the sealed local V0.3.1 root, require a caller-supplied exact
  transition ref and build one deterministic ledger from all four green reports.
  Assert that every welfare row binds the matching `LocalAnalysisReference`,
  snapshot ref, `welfare`/figure output bytes, uncertainty, basis fields, and
  method-specific limitation. Require two additional CV descriptive rows bound
  to `probabilities.csv` and `bid_yes_shares.csv`, derived from the independently
  reconstructed typed result rather than copied diagnostic summaries. Also assert:

  ```python
  exact = V031ExitHarness.open_exact(run_root, transition_ref)
  assert exact.marker_ref == transition_ref

  with pytest.raises(PaperAuthorityInvalid):
      service.build(forged_transition_ref)

  with pytest.raises(PaperSupportInvalid):
      valuation_claims(exception_report, transition_ref)
  ```

- [x] **Step 6: Run ingestion tests and verify RED**

  Run:

  ```bash
  ENVRESEARCH_V031_ACCEPTANCE_ROOT="$V03_ROOT" \
    uv run pytest tests/integration/test_paper_claim_ledger.py -v
  ```

  Expected: failure because `ClaimLedgerService` and valuation row derivation do
  not exist.

- [x] **Step 7: Implement exact derivation, publication, and status**

  `ClaimLedgerService` must use a dedicated Paper Builder registry and never
  write beneath the V0.3.1 root. `from_v031()` must reject equal, ancestor,
  descendant, symlink-alias, or otherwise physically overlapping roots:

  ```python
  class V031ExitHarness:
      @classmethod
      def open_exact(
          cls, run_root: Path, transition_ref: ArtifactRef
      ) -> "V031ExitHarness": ...

  class ClaimLedgerService:
      @classmethod
      def from_v031(
          cls, *, run_root: Path, paper_root: Path
      ) -> "ClaimLedgerService": ...

      def build(self, transition_ref: ArtifactRef) -> ArtifactRef: ...

      def status(
          self, ledger_ref: ArtifactRef, transition_ref: ArtifactRef
      ) -> ClaimEvidenceLedger: ...
  ```

  `open_exact()` must load the caller-supplied ref, require that same ref to be
  current, and fail rather than silently select another current generation.

  Under one subject lock, identical concurrent builds may reuse the same exact
  ref. Before promotion and on every status call, reopen the exact transition,
  call `accepted_analysis_reports()`, rederive all rows, compare the complete
  ledger payload, and require both the ledger and transition to remain current.
  Repeat the upstream-current check immediately after `set_current`; if it
  changed in the final window, restore the prior paper pointer or leave it
  absent and raise `PAPER_AUTHORITY_INVALID`.

- [x] **Step 8: Add stale, mutation, and concurrency regressions**

  Copy the sealed root for mutation cases. Advance the transition/report/run/
  catalog-binding current pointer, replace one referenced output, or change the
  Paper Builder current pointer. Each case must raise the matching stable error
  and publish no conflicting current ledger. Run two processes building the
  identical ledger and require one exact current ref. Add equal/ancestor/
  descendant/alias root cases and a mutation hook between final revalidation
  and `set_current` to prove no stale ledger becomes current.

- [x] **Step 9: Run Task 1 gates and commit**

  Run:

  ```bash
  ENVRESEARCH_V031_ACCEPTANCE_ROOT="$V03_ROOT" \
    uv run pytest tests/unit/test_paper_claim_contracts.py \
      tests/integration/test_paper_claim_ledger.py -q
  uv run ruff check src/envresearch/paper tests/unit/test_paper_claim_contracts.py \
    tests/integration/test_paper_claim_ledger.py
  uv run mypy src/envresearch/paper
  git diff --check
  ```

  Commit:

  ```bash
  git add src/envresearch/paper tests/unit/test_paper_claim_contracts.py \
    tests/integration/test_paper_claim_ledger.py
  git commit -m "feat(paper): build exact claim evidence ledger"
  ```

  *Traceability:* Implements `PB-REQ-001`, `PB-REQ-002`, `PB-REQ-007`,
  `PB-REQ-008`, and the Task 1 part of `PB-REQ-009`.

---

### Task 2: Typed Argument Map

**Files:**
- Create: `src/envresearch/paper/argument_contracts.py`
- Create: `src/envresearch/paper/argument_map.py`
- Test: `tests/unit/test_paper_argument_map.py`
- Test: `tests/integration/test_paper_argument_service.py`

**Interfaces:**
- Consumes: exact current `ClaimEvidenceLedger` ref and payload from Task 1.
- Produces: `ArgumentNode`, `ArgumentEdge`, `ArgumentMap`, and
  `ArgumentMapService.build(ledger_ref, candidate) -> ArtifactRef` plus
  `ArgumentMapService.status(map_ref, ledger_ref) -> ArgumentMap`.

- [x] **Step 1: Write REDs for graph semantics**

  Require typed nodes (`research-question`, `contribution`, `mechanism`,
  `empirical-claim`, `robustness`, `limitation`, `policy-implication`) and typed
  edges (`evidence-backed`, `interpretive`, `conditional`). Reject duplicate
  nodes, dangling claim IDs, cycles, empirical nodes without claims, and
  conclusion/policy nodes without accepted incoming support.

- [x] **Step 2: Verify RED**

  ```bash
  uv run pytest tests/unit/test_paper_argument_map.py -v
  ```

  Expected: missing argument-map module.

- [x] **Step 3: Implement frozen graph contracts and validation**

  Use one deterministic topological-sort validator. An empirical node stores
  only ledger claim IDs, never copied untyped evidence summaries. `status()`
  must reopen the exact map, ledger, and transition payloads and recheck their
  current pointers before returning.

- [x] **Step 4: Add exact-ledger publication/status REDs**

  Advance or mutate the ledger between candidate validation and current
  promotion. Require `PAPER_AUTHORITY_INVALID` and no current argument map.

- [x] **Step 5: Implement service, run gates, and commit**

  ```bash
  uv run pytest tests/unit/test_paper_argument_map.py \
    tests/integration/test_paper_argument_service.py -q
  uv run ruff check src/envresearch/paper tests/unit/test_paper_argument_map.py \
    tests/integration/test_paper_argument_service.py
  uv run mypy src/envresearch/paper
  git diff --check
  git add src/envresearch/paper tests/unit/test_paper_argument_map.py \
    tests/integration/test_paper_argument_service.py
  git commit -m "feat(paper): bind claims into argument map"
  ```

  *Traceability:* Implements `PB-REQ-003`, `PB-REQ-007`, and `PB-REQ-009`.

---

### Task 3: Draft and Claim-Span Bindings

**Files:**
- Create: `src/envresearch/paper/citation_authority.py`
- Create: `src/envresearch/paper/draft_contracts.py`
- Create: `src/envresearch/paper/draft_builder.py`
- Create: `src/envresearch/paper/draft_validation.py`
- Test: `tests/unit/test_paper_draft_contracts.py`
- Test: `tests/integration/test_paper_draft_builder.py`

**Interfaces:**
- Consumes: exact current argument-map and ledger refs, exact accepted
  `CitationAuthority` ref/payload pairs, plus a typed draft candidate. The
  production authority adapter must reopen exact `CuratorSourceSheet` artifacts
  containing `VerifiedClaim` rows and validate the lifecycle-sealed
  `envresearch.benchmarks.claim_report.CitationIntegrityReport` through the
  protected attestation/current-source-generation boundary; it must not accept
  the similarly named evaluation-only report type.
- Produces: `PaperParagraph`, `ClaimSpanBinding`, `CitationBinding`,
  `TableBinding`, `FigureBinding`, `PaperDraft`, and
  `DraftService.publish(candidate) -> ArtifactRef` plus
  `DraftService.status(draft_ref, map_ref, ledger_ref) -> PaperDraft`.

- [x] **Step 1: Write REDs for span completeness and exact numbers**

  Require every empirical sentence span to bind claim IDs and every numeric
  token to equal a bound estimate/range/series/interval/basis. Reject missing
  spans, overlapping invalid offsets, invented citations, citations absent from
  the exact accepted source sheets, a stale source generation, or a non-passing
  current protected citation attestation, mutated table/figure hashes,
  contradictory signs, and unit/population/time/price changes.

- [x] **Step 2: Verify RED and implement minimal contracts**

  ```bash
  uv run pytest tests/unit/test_paper_draft_contracts.py -v
  ```

  The draft stores canonical plain text plus explicit character offsets; it does
  not embed mutable paths or ask an LLM to certify its own claims. Citation
  authority is an exact-ref input whose typed payload and protected current
  attestation are reopened on every publication and `status()` call.

- [x] **Step 3: Write and implement one useful deterministic paper slice**

  Build a title, research question, methods paragraph, results paragraph,
  limitations paragraph, one results table binding, and one figure binding from
  the Task 1/2 artifacts. Failed/integrity exit cases may appear only in the
  validation-scope paragraph and must never be promoted as findings.

- [x] **Step 4: Add stale-upstream and coherent-forgery regressions**

  Reseal draft text and bindings after changing a number, claim strength, or
  policy phrase. Status must independently reopen upstream artifacts and reject
  with `PAPER_SUPPORT_INVALID` or `PAPER_SCOPE_EXCEEDED`.

- [x] **Step 5: Run gates and commit**

  ```bash
  uv run pytest tests/unit/test_paper_draft_contracts.py \
    tests/integration/test_paper_draft_builder.py -q
  uv run ruff check src/envresearch/paper tests/unit/test_paper_draft_contracts.py \
    tests/integration/test_paper_draft_builder.py
  uv run mypy src/envresearch/paper
  git diff --check
  git add src/envresearch/paper tests/unit/test_paper_draft_contracts.py \
    tests/integration/test_paper_draft_builder.py
  git commit -m "feat(paper): generate evidence bound draft"
  ```

  *Traceability:* Implements `PB-REQ-004`, `PB-REQ-007`, and `PB-REQ-008`.

---

### Task 4: Independent Audit and Revision Closure

**Files:**
- Create: `src/envresearch/paper/audit_contracts.py`
- Create: `src/envresearch/paper/auditor.py`
- Create: `src/envresearch/paper/revision.py`
- Test: `tests/unit/test_paper_audit.py`
- Test: `tests/integration/test_paper_revision.py`

**Interfaces:**
- Consumes: exact current draft, argument map, ledger, and all transitive source
  refs.
- Produces: `PaperAuditFinding`, `PaperAuditReport`, `DraftRevision`,
  `PaperAuditService.audit(draft_ref) -> ArtifactRef`, and
  `RevisionService.revise(predecessor_ref, candidate) -> ArtifactRef`, with
  `PaperAuditService.status(audit_ref, draft_ref) -> PaperAuditReport` and
  `RevisionService.status(revision_ref, predecessor_ref) -> DraftRevision`.

- [x] **Step 1: Write audit-matrix REDs**

  Cover citation mismatch, numeric contradiction, table/figure mutation,
  claim-strength excess, policy overclaim, unit/population/time/price overreach,
  scope inconsistency, and cross-section contradiction. Each finding binds an
  exact span and upstream refs and uses one of the four stable public codes.

- [x] **Step 2: Verify RED and implement independent audit**

  ```bash
  uv run pytest tests/unit/test_paper_audit.py -v
  ```

  Audit must reconstruct from exact refs rather than trust draft-supplied
  summaries, including the exact accepted citation authority payloads.

- [x] **Step 3: Write revision-lineage REDs**

  Closing a finding requires a new draft generation whose affected spans and
  upstream refs revalidate. Reject caller-declared closure, stale predecessor,
  partial closure, or upstream revision during publication.

- [x] **Step 4: Implement revision, recovery, and gates**

  Inject failures at immutable draft publish, audit publish, and current-pointer
  promotion. Retry must recover the same exact generation or fail without an
  unreadable current state.

- [x] **Step 5: Commit**

  ```bash
  uv run pytest tests/unit/test_paper_audit.py \
    tests/integration/test_paper_revision.py -q
  uv run ruff check src/envresearch/paper tests/unit/test_paper_audit.py \
    tests/integration/test_paper_revision.py
  uv run mypy src/envresearch/paper
  git diff --check
  git add src/envresearch/paper tests/unit/test_paper_audit.py \
    tests/integration/test_paper_revision.py
  git commit -m "feat(paper): audit and revise paper evidence"
  ```

  *Traceability:* Implements `PB-REQ-005`, `PB-REQ-007`, and `PB-REQ-009`.

---

### Task 5: Current Release Candidate, CLI, and V0.4 Exit

**Files:**
- Create: `src/envresearch/paper/release.py`
- Create: `src/envresearch/paper/cli.py`
- Modify: `src/envresearch/cli.py`
- Create: `tests/integration/test_paper_cli.py`
- Create: `tests/integration/test_paper_acceptance.py`
- Create: `docs/paper-builder-v04-operator-guide.md`
- Update: `docs/superpowers/specs/2026-08-12-v04-paper-builder-design.md`

**Interfaces:**
- Consumes: current clean audit report and its exact draft/argument/ledger/
  transition chain.
- Produces: `PaperReleaseCandidate`, read-only `paper status`, exact-reference
  `paper build`, and the stable V1.0 handoff pair `(ArtifactRef, payload)`.

- [x] **Step 1: Write release-boundary REDs**

  Require one clean current audit, exact transitive refs, no open findings, and
  a complete reopened payload. Reject stale transition, superseded analysis,
  mutated table/figure, unsupported claim, citation mismatch, numeric/unit/
  policy overclaim, and upstream revision after audit.

- [x] **Step 2: Write concurrency and recovery REDs**

  Run identical builders concurrently and require one exact release ref. Inject
  process death at every publication boundary and prove restart either recovers
  the sealed generation or retains the last readable current candidate.

- [x] **Step 3: Implement release and reference-only CLI**

  Commands accept explicit JSON `ArtifactRef` inputs and emit deterministic JSON.
  `status` is read-only; authority/input errors exit 2, an audited non-release
  state exits 1, and a current green candidate exits 0. No command scans latest
  artifacts or invokes an estimator.

- [x] **Step 4: Run the full acceptance and compatibility matrix**

  ```bash
  ENVRESEARCH_V031_ACCEPTANCE_ROOT="$V03_ROOT" \
    uv run pytest tests/unit tests/integration -q \
      --cov=envresearch --cov-report=term-missing --cov-fail-under=80
  uv run ruff check .
  uv run mypy src
  uv lock --check
  git diff --check
  ```

  Confirm all changed Python/R files are at most 400 lines and V0.3.1 reserved
  capability gates remain unchanged.

- [x] **Step 5: Independent review, documentation, and commit**

  Request a read-only reviewer to reproduce the green slice and every fail-closed
  category. Update the operator guide, project memory, and V0.4 design status only
  after a zero-Critical/zero-Important verdict.

  ```bash
  git add src/envresearch/paper src/envresearch/cli.py \
    tests/integration/test_paper_cli.py \
    tests/integration/test_paper_acceptance.py \
    docs/paper-builder-v04-operator-guide.md \
    docs/superpowers/specs/2026-08-12-v04-paper-builder-design.md
  git commit -m "feat(paper): complete audited paper builder"
  ```

  *Traceability:* Implements `PB-REQ-006`, `PB-REQ-007`, `PB-REQ-009`, and
  `PB-REQ-010`; verifies the full V0.4 acceptance matrix.

## Self-Review

- **Spec coverage:** Tasks 1--5 map respectively to design sections 3.1, 3.2,
  3.3, 3.4, and sections 4--7. Every acceptance case in design section 6 is
  covered in Task 4 or Task 5.
- **Scope control:** No task adds an estimator, data connector, package installer,
  methodology registry entry, or V0.5--V0.9 milestone.
- **Placeholder scan:** The plan contains no deferred implementation placeholders;
  every task names exact files, interfaces, observable REDs, commands, and commit
  boundaries.
- **Type consistency:** All downstream stages consume `ArtifactRef` plus the
  exact typed payload produced by the preceding stage. `ClaimEvidenceLedger`,
  `ArgumentMap`, `PaperDraft`, `PaperAuditReport`, and `PaperReleaseCandidate`
  names remain stable across tasks.
- **Early usefulness:** Task 1 independently delivers a queryable claim ledger
  from real accepted V0.3.1 evidence; later tasks can be paused without losing
  that useful capability.
- **Independent plan review:** Final read-only verdict was PASS with zero
  Critical and zero Important findings after exact-transition, descriptive
  evidence, citation-authority, status/reopen, root-separation, and race
  boundaries were made explicit.
