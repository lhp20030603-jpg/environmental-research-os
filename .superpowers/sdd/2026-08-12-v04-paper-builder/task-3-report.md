# Task 3 Implementation Report: Draft and Claim-Span Bindings

## Outcome

Task 3 delivers one deterministic, evidence-bound paper slice over the exact
current Task 1 claim ledger, Task 2 argument map, and the lifecycle-sealed
citation authority. It publishes immutable `PaperDraft` objects and a locked
current pointer, then independently reopens the draft and every transitive
authority on `status()`.

The useful local slice contains a title, research question, verified methods
statement, canonical results statement, exact limitation, one results table,
and one figure. It uses genuine always-running local contingent-valuation
evidence. No LLM, network, Zotero, R, or external sealed-root dependency is
used by this Task 3 slice or its tests.

## TDD Evidence

Production changes followed observed RED checkpoints.

1. Contract and pure-validation REDs established strict/frozen models,
   canonical paragraph ordering, explicit character offsets, non-overlapping
   spans, number and basis equality, verified citation bindings, exact output
   evidence, and materialized exact-ref validation before production code was
   added.
2. Production-authority and service REDs established lifecycle-only citation
   reopening, protected source-attestation comparison, immutable publication,
   idempotent recovery, current-pointer rollback, and independent status
   reconstruction.
3. The pure review matrix produced 13 expected failures and 10 passes:

   ```text
   uv run pytest tests/unit/test_paper_draft_review.py \
     tests/integration/test_paper_draft_authority.py -q
   13 failed, 10 passed in 12.18s
   ```

   The failures covered six table/figure caption attacks, the evaluation-only
   citation report type, incomplete limitations, unsupported validation-scope
   prose, and four real protected-attestation MAC/canonical byte mutations.
4. The final citation window RED used coherent source/brief/map regeneration
   and lifecycle resealing after the last ordinary reopen:

   ```text
   uv run pytest tests/integration/test_paper_draft_authority.py \
     -q -k 'final_window'
   3 failed, 14 deselected in 3.14s
   ```

   Publish, status, and idempotent recovery all incorrectly returned before a
   final exact citation-generation comparison.
5. The composite paper-authority RED used real spawned writers following the
   public registry subject locks:

   ```text
   uv run pytest tests/integration/test_paper_draft_concurrency.py \
     -q -k 'final_window_writer'
   6 failed, 3 deselected in 7.97s
   ```

   The six combinations were publish/status/idempotent recovery crossed with
   ledger/map current mutation. Each writer acquired its lock before the draft
   operation returned, demonstrating the stale-success window.

## Implemented Boundary

### Strict draft contracts

- `PaperParagraph`, `ClaimSpanBinding`, `CitationBinding`, `TableBinding`,
  `FigureBinding`, `PaperDraftCandidate`, and `PaperDraft` are strict, frozen,
  and canonical.
- Drafts store plain text plus explicit character offsets and exact
  `ArtifactRef` values for map, ledger, citation report, source sheets, and
  outputs.
- Every results and limitations character is covered by the appropriate exact
  claim span. Methods prose is exactly covered by verified normalized citation
  claims.
- Numeric estimates, confidence intervals, ranges, series coordinates,
  numerator/denominator counts, signs, units, population, time, price basis,
  output digests, paths, and result pointers are checked against the bound
  ledger row.
- Table and figure captions use a deterministic claim-bound renderer. Caller
  supplied facts, numbers, policy claims, causal overstatement, invented
  citations, and unsupported validation-scope prose fail closed.

### Citation authority

- `LifecycleCitationAuthority` accepts only the production
  `envresearch.benchmarks.claim_report.CitationIntegrityReport`, validates its
  canonical binding, and never accepts the evaluation-only same-name model.
- Every reopen enters `require_current_citation_report`, reloads registry cases,
  authenticates the protected source generation, and compares catalog roots,
  case IDs, source generations, exact source/map/brief refs, and triple hashes.
- Real source/report MAC or canonical-byte corruption is classified as
  `PAPER_INTEGRITY_INVALID`; valid non-current or superseded authority remains
  `PAPER_AUTHORITY_INVALID`.
- `CitationGenerationToken` binds the exact report ref, report payload digest,
  source generation, and complete protected source-anchor digest. Its final
  comparison is the citation linearization point.

### Transaction and recovery

- `DraftService.publish()` and `status()` hold one composite paper-authority
  lease in the fixed order `paper-claim-ledger -> paper-argument-map ->
  paper-draft`.
- Publish, promotion, idempotent recovery, status reconstruction, the final
  draft-current check, and the citation-token check occur inside that lease.
  Internal recovery/promotion paths do not reacquire non-reentrant locks.
- Immutable object publication precedes current-pointer promotion. Publication
  faults are typed, retries reuse exact immutable bytes, and rollback uses
  compare-and-restore so it never overwrites a newer pointer.
- Actual spawned-process tests cover identical builds, conflicting builds, and
  `os._exit(73)` after immutable object publication but before current-pointer
  installation.

## Review Rounds and Closure

The review fixes were applied in bounded TDD rounds:

1. Exact materialized refs, authentic lifecycle report binding,
   validation-scope number closure, per-claim output membership, claim-scoped
   strength checks, methods completeness, all three claim-value forms, and
   globally unique output binding IDs.
2. Deterministic captions, exact production report type, attestation corruption
   classification, limitations completeness, validation-scope fail-closed,
   real multiprocessing contention, and crash retry.
3. Final citation-generation token checks on publish, status, and idempotent
   recovery.
4. Composite ledger/map/draft lease closing the final map/ledger stale-success
   window.
5. A test-only review finding added an explicit child `attempting` handshake
   before interpreting lock non-acquisition, eliminating a slow-CI false-pass
   possibility.

The decisive independent review verdict was **PASS with 0 Critical, 0
Important, and 0 Minor findings**. A later additional read-only review also
passed the production lock order, non-reentrancy, rollback, and linearization
design; its single test-only timing warning was closed by the handshake above.

## Final Verification

### Task 3 focused gate

```text
uv run pytest tests/unit/test_paper_draft_review.py \
  tests/unit/test_paper_draft_contracts.py \
  tests/unit/test_paper_draft_validation_matrix.py \
  tests/integration/test_paper_draft_builder.py \
  tests/integration/test_paper_draft_authority.py \
  tests/integration/test_paper_draft_concurrency.py -q
84 passed in 42.27s
```

### Final citation window

```text
uv run pytest tests/integration/test_paper_draft_authority.py \
  -q -k 'final_window'
3 passed, 14 deselected in 6.31s
```

### Process concurrency and composite-writer windows

```text
uv run pytest tests/integration/test_paper_draft_concurrency.py \
  -q -k 'not final_window_writer'
3 passed, 6 deselected in 6.32s

uv run pytest tests/integration/test_paper_draft_concurrency.py \
  -q -k 'final_window_writer'
6 passed, 3 deselected in 13.82s
```

### Core Task 1-3 controller matrix

The matrix includes all Task 1 claim contracts/services/authority, Task 2
argument-map services/authority/concurrency, and Task 3 draft gates:

```text
uv run pytest tests/unit/test_paper_claim_contracts.py \
  tests/unit/test_paper_argument_map.py \
  tests/unit/test_paper_draft_contracts.py \
  tests/unit/test_paper_draft_review.py \
  tests/unit/test_paper_draft_validation_matrix.py \
  tests/integration/test_paper_claim_ledger.py \
  tests/integration/test_paper_claim_authority.py \
  tests/integration/test_paper_argument_service.py \
  tests/integration/test_paper_argument_authority.py \
  tests/integration/test_paper_argument_concurrency.py \
  tests/integration/test_paper_draft_builder.py \
  tests/integration/test_paper_draft_authority.py \
  tests/integration/test_paper_draft_concurrency.py -q
157 passed, 1 skipped in 50.24s
```

The one skip is the pre-existing optional formal sealed V0.3.1 acceptance-root
test; all repository-owned local Task 1-3 controller tests ran.

### Static and file gates

```text
ruff check: All checks passed
ruff format --check: 23 files already formatted
mypy src/envresearch/paper: Success, no issues in 14 source files
git diff --check: clean
```

Every changed Task 3 Python file is at most 400 lines. The largest is
`tests/unit/test_paper_draft_contracts.py` at 391 lines; the largest production
Task 3 file is `src/envresearch/paper/draft_validation.py` at 356 lines.

## Files

Production:

- `src/envresearch/paper/__init__.py`
- `src/envresearch/paper/_draft_candidate.py`
- `src/envresearch/paper/_draft_prose.py`
- `src/envresearch/paper/_draft_store.py`
- `src/envresearch/paper/citation_authority.py`
- `src/envresearch/paper/draft_builder.py`
- `src/envresearch/paper/draft_contracts.py`
- `src/envresearch/paper/draft_validation.py`

Tests and local fixtures:

- `tests/unit/paper_draft_fixtures.py`
- `tests/unit/test_paper_draft_contracts.py`
- `tests/unit/test_paper_draft_review.py`
- `tests/unit/test_paper_draft_validation_matrix.py`
- `tests/integration/paper_draft_integration_fixtures.py`
- `tests/integration/paper_draft_process_fixtures.py`
- `tests/integration/test_paper_draft_authority.py`
- `tests/integration/test_paper_draft_builder.py`
- `tests/integration/test_paper_draft_concurrency.py`

This report is intentionally ignored. No plan, project memory, dependency,
lockfile, network resource, Zotero library, R runtime, or external dataset was
changed or used by this implementation report step. No Task 3 commit was made.
