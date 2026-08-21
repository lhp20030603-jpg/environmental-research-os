# Task 4 Report: Private store, event DAG, and exact attempt preparation

## Status

Implemented the owner-private Personal Validation store, authenticated root manifest,
canonical object registry, strict event DAG, exact Factory/correct-stop attempt
preparation, zero-write status, and boundary-specific exact retry. No Product,
bundle, or review authority was added.

## Files

- `src/envresearch/personal_validation/private_store.py` (created)
- `src/envresearch/personal_validation/roots.py` (created)
- `src/envresearch/personal_validation/events.py` (created)
- `src/envresearch/personal_validation/targets.py` (created)
- `src/envresearch/personal_validation/service.py` (created)
- `tests/integration/personal_validation_fixtures.py` (created)
- `tests/integration/test_personal_validation_authority.py` (created)
- `tests/integration/test_personal_validation_recovery.py` (created)

## TDD Evidence

### Initial RED

Command:

```text
uv run pytest tests/integration/test_personal_validation_authority.py tests/integration/test_personal_validation_recovery.py -q
```

Observed before production modules existed:

```text
ERROR collecting tests/integration/test_personal_validation_authority.py
ModuleNotFoundError: No module named 'envresearch.personal_validation.private_store'
ERROR collecting tests/integration/test_personal_validation_recovery.py
ModuleNotFoundError: No module named 'envresearch.personal_validation.private_store'
2 errors during collection
```

### Focused GREEN

The first complete implementation reached:

```text
20 passed in 2.98s
```

The final focused suite, after the adversarial refinements below:

```text
uv run pytest tests/integration/test_personal_validation_authority.py tests/integration/test_personal_validation_recovery.py -q
33 passed in 7.42s
```

### Adversarial RED/GREEN refinements

Top-root replacement after each child open and at journal construction initially
escaped the typed boundary at three locations:

```text
uv run pytest tests/integration/test_personal_validation_authority.py::test_top_root_swap_during_composition_is_typed_zero_product_write -q
4 failed in 0.46s
```

After typed composition wrapping and exact attachment checks:

```text
4 passed in 0.36s
```

A divergent retry against a valid but lagging completion record initially repaired
the authenticated head before rejecting the changed request:

```text
uv run pytest tests/integration/test_personal_validation_recovery.py::test_divergent_retry_does_not_reconcile_lagging_completion -q
1 failed in 0.50s
```

After separating non-mutating writer inspection from exact-event recovery:

```text
1 passed in 0.58s
```

Writer reopen initially recreated a deleted top child/object registry layout:

```text
uv run pytest tests/integration/test_personal_validation_authority.py::test_writer_reopen_never_recreates_missing_child_root tests/integration/test_personal_validation_recovery.py::test_writer_reopen_does_not_reconcile_malformed_lagging_event -q
1 failed, 1 passed in 0.42s
```

After requiring the exact existing child and owner-private registry layout:

```text
3 passed in 0.39s
```

The malformed signed lag record is now rejected before reconciliation, while an
exact lagging completion retry changes only its missing authenticated head. A live
writer also rejects deleted registry layout without recreating it.

The first successor attempt RED showed that an exact predecessor was being treated
as a divergent retry:

```text
uv run pytest tests/integration/test_personal_validation_authority.py::test_successor_attempt_extends_exact_per_case_predecessor_chain -q
1 failed in 0.65s
```

After enforcing a per-case predecessor chain and distinguishing exact retry from a
new successor:

```text
1 passed in 0.54s
```

## Final Verification

Affected pinned-storage, Personal contract/snapshot, Research stop-inspection, and
real Factory run/authority regressions:

```text
uv run pytest tests/unit/test_exit_registry_pinned.py tests/unit/test_secure_journal_read_only.py tests/unit/test_personal_validation_contracts.py tests/unit/test_personal_validation_reviews.py tests/unit/test_personal_validation_repairs.py tests/unit/test_personal_validation_snapshots.py tests/integration/test_research_stop_inspection.py tests/integration/test_factory_run.py tests/integration/test_factory_run_authority.py -q
177 passed in 107.63s (0:01:47)
```

Static and formatting gates:

```text
uv run ruff check src/envresearch/personal_validation tests/integration/personal_validation_fixtures.py tests/integration/test_personal_validation_authority.py tests/integration/test_personal_validation_recovery.py
All checks passed!

uv run mypy src/envresearch/personal_validation
Success: no issues found in 12 source files

uv run ruff format --check src/envresearch/personal_validation tests/integration/personal_validation_fixtures.py tests/integration/test_personal_validation_authority.py tests/integration/test_personal_validation_recovery.py
15 files already formatted

git diff --check
(no output; exit 0)
```

All changed source/test files remain at or below 400 lines: 398, 342, 345,
366, 387, 335, 386, and 350 lines respectively.

## Self-review

- Root composition requires one explicit absolute owner/0700 top root, binds its
  device/inode and every canonical exclusion identity, and rejects repository,
  Git common-directory, linked-worktree, Obsidian, nested-child, and moved-root
  overlap without Product-root writes.
- The store owns all top/child pins through one `ExitStack`; journal and registry
  borrow those pins, construction failures unwind them, and close is idempotent.
- Every writer holds the session lock across complete event-history inspection,
  canonical session/attempt publication, and unique completion append.
- Session start binds the already-published session object; attempt completion
  binds the already-published attempt object. Event IDs, sequence, and per-session
  predecessor hashes are deterministic and fully revalidated.
- Writer inspection authenticates and validates lagging event bytes before any
  recovery. Only the exact request whose expected event is the lagging record may
  publish the missing head; divergent and malformed retries remain byte-identical.
- Status always uses a fresh `reconcile=False` reader, reopens exact session and
  attempt objects, validates event/attempt predecessor closure, reports explicit
  orphans, and never heals missing or corrupt state.
- Both `prepare_existing_run` with a genuine assembled Factory run and
  `prepare_correct_stop` with a strict inspection/root inventory are covered.
- The returned `PreparedAttempt` contains all three exact references and the
  service retains no implicit last-session lookup state.
- Tests cover both Task 4 object/event crash boundaries, start-event head lag,
  malformed and divergent lag records, exact completed retry, root swaps,
  missing control/layout state, partial-open descriptor cleanup, two-process
  sequence contention, successor lineage, and Product-tree byte/metadata identity.

## Concerns

No known correctness concern remains. Delaying head recovery until Personal event
validation requires a narrow adapter over `SecureJournal`'s protected descriptor
methods because Task 1 exposes separate strict-read and eager-recovery capabilities
but no public validate-before-repair callback. This coupling is isolated in
`events.py` and covered by malformed, divergent, exact-lag, and storage regression
tests. Independent review was not dispatched because the controller explicitly
prohibited subagents/reviewers for this task.

---

## Independent-review remediation

The independent Task 4 review returned two Critical and two Important findings.
All four are fixed in a follow-up transaction-hardening commit.

### Malformed lag validation and atomic append

RED command:

```text
uv run pytest -q tests/integration/test_personal_validation_recovery.py::test_completion_append_does_not_reconcile_malformed_lagging_event
```

Observed before the fix:

```text
1 failed in 0.47s
```

The service raised `event-history-invalid`, but the authenticated head and journal
had already advanced beyond the captured malformed lag. After moving complete
event-model/DAG validation, strict head inspection, exact recovery selection,
append, and reopen verification beneath one journal lock and descriptor:

```text
1 passed in 0.45s
```

`PersonalEventJournal.append_expected()` no longer calls the generic
auto-reconciling `append_unique()` seam.

### Non-healing journal and session-lock authority

RED command:

```text
uv run pytest -q tests/integration/test_personal_validation_authority.py::test_live_writer_rejects_deleted_control_identity_without_write
```

Observed before the fix:

```text
2 failed in 0.79s
```

Deleting the journal lock/anchor was detected only after the attempt object write,
and deleting the live session lock was accepted. After bootstrap-only creation,
static authenticated root-lock identity, active named-inode verification, and
strict journal key/lock/anchor/head checks at every authority boundary:

```text
2 passed in 0.73s
```

Existing writer capabilities now set journal `can_create_control=False` and
registry `create=False`; journal reconciliation is also disabled outside the
explicit exact-recovery adapter. Missing/replaced live controls fail typed and do
not change the captured damaged tree.

### Session-wide orphan slot ownership

RED command:

```text
uv run pytest -q tests/integration/test_personal_validation_recovery.py::test_other_case_cannot_consume_an_orphan_attempt_event_slot
```

Observed before the fix:

```text
1 failed in 0.64s
```

Case B completed after case A had crashed at `attempt-object`. After inspecting all
orphans bound to the session-start event, rather than filtering by current case:

```text
1 passed in 0.72s
```

The divergent case is byte-identically rejected and the exact case-A retry reuses
the orphan object and its sequence/event identity.

### Raw event API removal and real service concurrency

The independent reviewer RED established that `record_event()` accepted a
session-start event for a nonexistent object and that the old process test relied
on this invalid path. The review did not supply its probe command, so no command is
invented here. The raw method was removed, the remaining event writer methods were
privatized, and the API check is:

```text
uv run python -c "from envresearch.personal_validation.private_store import PersonalValidationStore; print(hasattr(PersonalValidationStore, 'record_event'), hasattr(PersonalValidationStore, 'append_event_locked'))"
False False
```

The replacement test runs identical and divergent two-process
`prepare_correct_stop()` calls:

```text
uv run pytest -q tests/integration/test_personal_validation_authority.py::test_two_process_prepare_has_one_canonical_attempt_without_residue
2 passed in 1.91s
```

Identical writers return the same session/attempt/event identities. Divergent
writers produce one canonical success and one typed `attempt-retry-divergent`.
Disk reopen proves exactly two events, one attempt object, an empty current
directory, and no temporary residue.

### Final verification after all fixes

Focused Personal store/service, contracts, and snapshots:

```text
uv run pytest -q tests/integration/test_personal_validation_authority.py tests/integration/test_personal_validation_recovery.py tests/unit/test_personal_validation_contracts.py tests/unit/test_personal_validation_snapshots.py
94 passed in 11.68s
```

Affected storage, Personal, Research stop, and real Factory regressions:

```text
uv run pytest tests/unit/test_exit_registry_pinned.py tests/unit/test_secure_journal_read_only.py tests/unit/test_personal_validation_contracts.py tests/unit/test_personal_validation_reviews.py tests/unit/test_personal_validation_repairs.py tests/unit/test_personal_validation_snapshots.py tests/integration/test_research_stop_inspection.py tests/integration/test_factory_run.py tests/integration/test_factory_run_authority.py -q
177 passed in 110.43s (0:01:50)
```

Final gates:

```text
uv run ruff check src/envresearch/personal_validation tests/integration/personal_validation_fixtures.py tests/integration/test_personal_validation_authority.py tests/integration/test_personal_validation_recovery.py
All checks passed!

uv run mypy src/envresearch/personal_validation
Success: no issues found in 12 source files

uv run ruff format --check src/envresearch/personal_validation tests/integration/personal_validation_fixtures.py tests/integration/test_personal_validation_authority.py tests/integration/test_personal_validation_recovery.py
15 files already formatted

git diff --check
(no output; exit 0)
```

Changed source/test line counts are 393, 400, 398, 381, 388, 400, and 399;
the unchanged Task 4 `targets.py` remains 366 lines. Every source/test file is at
or below 400 lines.

### Follow-up self-review and concerns

- The sole static session mutex is store-wide, not case-specific. Its authenticated
  anchor and named descriptor identity are checked while active and in a `finally`
  path before release, including when the preparation body raises.
- Exact completed-event recovery remains service-gated: a pending later completion
  is not repaired while merely reopening the already-complete session-start event.
  The exact/divergent attempt comparison decides whether recovery is authorized.
- Status and ordinary reopen paths acquire locks only for strict verification;
  they neither reconcile nor create control state.
- No bundle, review, report, Product promotion, repository, Git, worktree, or
  Obsidian authority was added.

No known correctness concern remains. The event adapter and static lock lease use
narrow protected `SecureJournal` lock/head helpers because Task 1 exposes no
public validate-before-repair transaction callback. This coupling is isolated in
`events.py`/`roots.py`, is non-product-authoritative, and is covered by malformed,
exact-lag, divergent-lag, live-deletion, two-process, and affected-storage tests.
