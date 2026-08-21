# Task 4 Implementation Report: Independent Audit and Revision Closure

## Outcome

Task 4 is complete for `PB-REQ-005`, `PB-REQ-007`, and `PB-REQ-009`.

The slice independently reconstructs every draft statement, citation binding,
claim span, table, figure, output byte, and transitive authority into a strict
`PaperAuditReport`. A blocked draft can be revised only by supplying corrected
`PaperDraftCandidate` content; the service derives the complete finding closure,
stages and independently audits the exact next generation, commits the revision
envelope, and changes the global draft pointer as its final linearization step.

No LLM, network, Zotero, R, or external sealed-root dependency is used by the
always-running Task 4 gates.

## TDD Evidence

Production behavior followed observed RED checkpoints.

1. Audit contract and attack-matrix REDs established strict/frozen nested
   references, canonical lineage, target unions, code/kind coherence, complete
   finding accumulation, and exact text/output/binding targets before audit
   production code was added.
2. Oracle-independence REDs monkeypatched Task 3 validation, rendering, and
   prose helpers. The corrected audit remained able to accept the clean draft
   and detect the forged draft using auditor-owned reconstruction only.
3. Audit lifecycle REDs covered raw table/figure byte mutation, stale external
   authorities, scoped two-phase audit promotion, torn pointers, process death,
   idempotent recovery, actual V0.3.1/citation writer contention, and lock-order
   inversion. The shared valuation authority lease was added only after real
   writer-window tests exposed the stale-success race.
4. Revision contracts and generation storage began with collection REDs for
   missing `revision_contracts` and `revision` modules. The service RED used a
   real published blocked predecessor and required a clean staged successor,
   committed predecessor and successor audits, and a committed revision before
   the final draft CAS.
5. The first revision recovery matrix produced the intended failures:

   ```text
   uv run pytest tests/integration/test_paper_revision_recovery.py -q
   3 failed, 1 passed in 6.70s
   ```

   The failures proved that a successful retry was treated as stale, a
   different candidate could overwrite a prepared revision, and a successful
   CAS still performed a fallible read. The strengthened RED remained three
   failures with two passing boundaries before the fixes.
6. The process matrix then exposed one remaining pending-intent bug:

   ```text
   uv run pytest tests/integration/test_paper_revision_concurrency.py -q
   1 failed, 8 passed in 20.10s
   ```

   A process dying after the revision pending pointer allowed a different
   candidate to replace the intent. Recovery now authenticates the pending
   envelope: the same candidate commits and promotes the exact staged objects;
   a different candidate fails without changing them.
7. Registry-boundary tests finally covered write-before-error and
   write-then-error behavior at immutable draft, audit, and revision object
   publication plus revision prepare and commit pointers:

   ```text
   uv run pytest \
     tests/integration/test_paper_revision_publication.py \
     -q -k registry_write_boundary
   7 passed in 15.78s
   ```

## Independent Audit Boundary

### Contracts and exact findings

- `PaperAuditFinding` and `PaperAuditReport` are strict, frozen, and canonical.
- A finding targets exactly one `TextSpan`, `OutputBindingTarget`, or
  `DraftBindingTarget`; finding IDs use full SHA-256 identities.
- Finding kinds map exactly to the four public codes:
  `PAPER_INTEGRITY_INVALID`, `PAPER_AUTHORITY_INVALID`,
  `PAPER_SUPPORT_INVALID`, and `PAPER_SCOPE_EXCEEDED`.
- Reports bind exact draft, map, ledger, citation report, transition, snapshot,
  citation-source, claim-map, blinded-brief, accepted-artifact, analysis, and
  output references. Role labels and nested model instances are independently
  revalidated rather than trusted.

### Independent reconstruction

- The auditor does not call `DraftService.status()`, `validate_draft()`, or the
  Task 3 prose renderer. It single-reads the draft and independently reconstructs
  its meaning from typed ledger rows, argument-map claims, citation payloads,
  and authenticated output evidence.
- The table-driven matrix covers citation mismatch, fully cited but unbound
  numbers, numeric contradictions, separate table and figure mutation,
  claim-strength excess, policy overclaim, unit/population/time/price basis
  overreach, validation-scope prose, unsupported map claims, scope
  inconsistency, cross-section contradiction, dangling/overlapping bindings,
  disjoint uncovered spans, and section-purpose mismatch.
- Title and research question use an auditor-owned closed form. Arbitrary
  factual, causal, modal, or policy language cannot bypass typed evidence.
- Every methods character is covered by exact citation spans and every results,
  limitations, and validation-scope character by the correct claim spans.
- Raw bound table and figure bytes are reopened through accepted evidence on
  every public operation; cached typed metadata cannot authenticate mutated
  output files.

### Audit transaction

- Audit identities and current subjects hash the complete exact draft ref.
- Each draft-scoped audit uses a pending pointer and independent commit marker;
  only equality of both denotes a current audit.
- Public `audit()` and `status()` hold the V0.3.1 transition authority lease,
  citation generation lease, and paper locks in one fixed global order.
- Immutable object, current pointer, commit marker, rollback, process death,
  write-then-error, and torn-state recovery all fail closed or converge to the
  exact same report.

## Revision Closure Boundary

### Generation and envelope contracts

- `PaperDraft.generation` starts at 1. Later generations require an exact
  `predecessor_ref`; the draft ref version equals the generation while the
  service-owned `draft_id` remains stable across the authority chain.
- `DraftRevision` binds exact predecessor/successor drafts and audits, unchanged
  map/ledger/citation refs, consecutive generations, the canonical complete set
  of predecessor finding IDs, and typed closure witnesses.
- Callers provide only corrected manuscript content. They cannot supply
  generation, predecessor, finding closures, witnesses, or authority refs.

### Staging, validation, and linearization

- Revision uses only package-private locked audit primitives; it never nests
  public Draft or Audit service locks.
- The global order is valuation authority, citation authority, claim ledger,
  argument map, paper draft, lexicographically sorted audit subjects, then the
  predecessor-scoped revision subject.
- The blocked predecessor audit is independently reconstructed. The successor
  immutable draft is staged as the exact next generation while the predecessor
  remains current, then independently audited and required to be clean.
- The revision envelope is published, prepared, reopened, and committed before
  the single global draft CAS. No fallible operation follows a successful CAS.
- `RevisionService.status()` historically reconstructs the blocked predecessor
  audit while requiring the successor to remain current, then reconstructs the
  clean successor audit and exact closure witnesses.

### Recovery and concurrency

- Same-input retries after either pre-CAS failure or post-CAS success return the
  same revision, successor, and audit refs.
- A committed or pending revision for a different successor is an authority
  conflict and cannot be overwritten.
- Process tests cover death after successor draft publication, successor audit
  object publication, audit pending and commit pointers, revision pending and
  commit pointers, and final draft CAS.
- Two identical processes converge to one exact revision. Two conflicting
  processes produce one winner and one `PAPER_AUTHORITY_INVALID` outcome, never
  two current generation-2 drafts.

## Review Closure

The audit implementation passed independent review with **0 Critical, 0
Important, and 0 Minor findings** after its one semantic-lineage Minor was
closed under TDD.

The revision implementation then passed a separate decisive review with **0
Critical, 0 Important, and 0 Minor findings**. The final registry-level
write-then-error matrix closed the last Important test gap before that verdict.

A final whole-slice code review initially returned PASS with two non-blocking
Minors. Both were closed before commit: preconstructed nested closure witnesses
are now strictly revalidated while canonical JSON round-trips remain supported,
and real citation/valuation writers are directly proven to wait through both
`revise()` and `status()`. The focused re-review verdict is **0 Critical, 0
Important, and 0 Minor findings**.

## Final Verification

### Task 1-4 and V0.3.1 authority/exit regression

The authoritative combined matrix includes Paper Builder claim, argument, draft,
audit, and revision contracts/services/authority/concurrency plus V0.3.1
valuation authority, corpus, exit, branch, security, and model gates:

```text
352 passed, 5 warnings in 202.90s (0:03:22)
```

The five warnings are intentional Pydantic serializer warnings generated by
tests that construct invalid nested model instances through `model_copy()` to
prove strict revalidation. They are not production warnings.

After the final two Minor closures, the complete Revision contract, store,
service, recovery, status, publication, process-concurrency, and real external
writer-exclusion matrix was rerun with a durable JUnit result:

```text
57 passed, 0 failed, 0 errors, 0 skipped in 118.953s
```

### Focused process and publication gates

```text
uv run pytest tests/integration/test_paper_revision_concurrency.py -q
10 passed in 26.56s

uv run pytest tests/integration/test_paper_revision_publication.py \
  -q -k registry_write_boundary
7 passed in 15.78s
```

### Static and file gates

```text
ruff format --check: all changed Python files already formatted
ruff check: All checks passed
mypy src: Success, no issues in 330 source files
git diff --check: clean
```

Every changed Python file is at most 400 physical lines. The largest changed
production file is `src/envresearch/paper/argument_map.py` at 384 lines; the
largest Task 4 process test is `test_paper_revision_concurrency.py` at 313
lines.

## Principal Files

Audit production:

- `src/envresearch/paper/audit_contracts.py`
- `src/envresearch/paper/auditor.py`
- `src/envresearch/paper/_audit_findings.py`
- `src/envresearch/paper/_audit_lineage.py`
- `src/envresearch/paper/_audit_prose.py`
- `src/envresearch/paper/_audit_reconstruction.py`
- `src/envresearch/paper/_audit_sections.py`
- `src/envresearch/paper/_audit_store.py`
- `src/envresearch/paper/_audit_transaction.py`
- `src/envresearch/paper/_audit_types.py`

Revision production:

- `src/envresearch/paper/revision_contracts.py`
- `src/envresearch/paper/revision.py`
- `src/envresearch/paper/_revision_draft.py`
- `src/envresearch/paper/_revision_recovery.py`
- `src/envresearch/paper/_revision_store.py`
- `src/envresearch/paper/_revision_validation.py`
- `src/envresearch/paper/_draft_store.py`
- `src/envresearch/paper/draft_contracts.py`

Authority integration:

- `src/envresearch/econometrics/valuation_authority.py`
- `src/envresearch/econometrics/valuation_exit_runner.py`
- `src/envresearch/econometrics/exit_evaluator.py`
- `src/envresearch/econometrics/valuation_exit_corpus.py`
- `src/envresearch/econometrics/valuation_transition.py`
- `src/envresearch/paper/argument_map.py`
- `src/envresearch/paper/citation_authority.py`
- `src/envresearch/paper/draft_builder.py`
- `src/envresearch/paper/ledger.py`

The complete test set is distributed across the Task 4 unit audit matrix,
audit authority/store/output/concurrency integration files, revision contract
and store tests, and revision service/recovery/status/publication/process files.

## Scope Statement

Task 4 stops at a clean, independently audited current draft plus exact revision
closure. Release-candidate construction, CLI behavior, operator documentation,
and the V0.4 final acceptance/handoff remain Task 5 and were not started here.
