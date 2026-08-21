# Task 5 — Compact nine-case Valuation exit

## Implemented behavior

- Added immutable Valuation Core contracts: a manifest, run, protected catalog,
  authenticated bindings, and all-or-nothing report, each fixed to exactly nine
  outcomes (four green, four scientific failures, one integrity failure).
- Added a checked local corpus with all four valuation families, separated
  runner/evaluator bytes, strict descriptor-relative reads, and frozen CSV views.
- Reused the existing `ExitRegistry`, `RegistryAnalysisExecutor`, locks,
  exact references, output-byte mutation, and evaluator comparison logic through
  typed constructors. Valuation subjects are distinct: `valuation-run-*`,
  `valuation-analysis-*`, and `valuation-report-*`.
- Added separate JSON CLI commands: `valuation-exit-run`,
  `valuation-exit-evaluate`, and `valuation-exit-status`. Status uses an exact
  report reference and performs no writes.
- Preserved the V0.3 16-case models, subjects, schemas, and legacy tests.

## RED evidence

Command:

```bash
uv run pytest tests/unit/test_econometrics_valuation_exit_models.py \
  tests/integration/test_econometrics_valuation_exit.py -q
```

Output before implementation: collection failed with
`ModuleNotFoundError: No module named 'envresearch.econometrics.valuation_exit_corpus'`
for both new test files. This was the intended RED state: the valuation corpus
and runner modules did not yet exist.

## GREEN evidence

```bash
uv run pytest tests/unit/test_econometrics_valuation_exit_models.py \
  tests/integration/test_econometrics_valuation_exit.py \
  tests/integration/test_econometrics_valuation_exit_security.py \
  tests/integration/test_econometrics_exit_runner.py \
  tests/integration/test_econometrics_exit_security.py -q
# 21 passed

uv run ruff check src/envresearch/econometrics \
  tests/unit/test_econometrics_valuation_exit_models.py \
  tests/integration/test_econometrics_valuation_exit.py \
  tests/integration/test_econometrics_valuation_exit_security.py
# All checks passed

uv run mypy src/envresearch/econometrics
# Success: no issues found in 81 source files

git diff --check
# clean
```

All changed Python and test files were checked at 400 lines or fewer.

## Files changed

- New Valuation contracts, corpus freezer, runner adapter, and CLI registrar.
- New checked benchmark fixtures and protected expectations.
- New unit/integration/security coverage.
- Minimal shared registry, runner, evaluator, and CLI registration seams.

## Self-review

- Expectation leakage: runner registry JSON was scanned; expected failure codes
  are absent. Expectations remain in the evaluator registry.
- Authority: run/evaluate/status take exact references; evaluator verifies current
  run/report pointers and exact case/data/binding equality. No latest lookup is
  used.
- Corpus safety: root and components use the hardened non-symlink,
  descriptor-relative reader; mutable data views are regenerated from hashed
  bytes before each execution.
- Restart: the shared runner preserves receipts, verifies existing bindings, and
  reuses the exact same run generation.
- Integrity: the final case has a distinct Hedonic spec and analysis subject;
  its output byte is mutated only after authenticated execution and must produce
  `EVIDENCE_TAMPERED`.
- All-or-nothing: `passed` is only valid when every one of the nine outcomes is
  matched with no findings.

## Concern

Task 5 intentionally uses only checked local fixtures and offline fake
execution. Frozen-R execution and its real-output expectations remain Task 6.

## Fix round 1/5 — independent review remediation

### Coverage and RED evidence

The following tests were written before their corresponding production changes:

- `tests/unit/test_econometrics_valuation_exit_models.py` added fixed-matrix
  family/role swap tests and duplicate-comparison rejection. RED command:
  `uv run pytest tests/unit/test_econometrics_valuation_exit_models.py -q`.
  Result: three expected failures because the old model accepted both swaps and
  duplicate `(output_name, selector)` comparisons.
- `tests/integration/test_econometrics_valuation_exit.py` added missing and
  mismatched snapshot authority tests. RED command:
  `uv run pytest tests/unit/test_econometrics_valuation_exit_models.py
  tests/integration/test_econometrics_valuation_exit.py -q`. Result: missing
  snapshots bound successfully, mismatch was not rejected, and evaluation still
  relied on the removed runner manifest catalog field.
- `tests/integration/test_econometrics_valuation_exit_security.py` added
  all-pair CLI root aliases, descriptor/data/expectation symlinks, frozen-data
  mutation, evaluator-before-run/partial-run, stale case/data/binding, forged
  current report, and explicit-catalog/no-latest tests. The status alias RED was
  `uv run pytest tests/integration/test_econometrics_valuation_exit_security.py -q`:
  three expected failures returned a missing-root path instead of the stable
  overlap error because status had not resolved and compared all three roots.

### Production changes

- `ValuationExitManifest` now requires the exact nine fixed
  `(case_id, family, role)` triples. `ValuationCaseExpectation` rejects duplicate
  output-selector pairs.
- Removed `expectation_catalog_ref` entirely from runner manifest bytes. The
  evaluator now owns a `ValuationExitCatalogBinding` that binds the protected
  catalog reference to the exact runner manifest reference.
- Valuation execution now requires a non-null snapshot with the exact frozen
  data hash before a binding is persisted; the protected evaluator requires the
  same authority for every non-tampered report and validates bound data hashes.
- Valuation status resolves and pairwise separates runner, evaluator, and
  analysis roots before opening state.
- `V03ExitRunner` now delegates to `ResumableExitRunner`, retaining its original
  schema version and `run-*` subjects.

### GREEN evidence

```bash
uv run pytest tests/unit/test_econometrics_valuation_exit_models.py \
  tests/integration/test_econometrics_valuation_exit.py \
  tests/integration/test_econometrics_valuation_exit_security.py \
  tests/integration/test_econometrics_exit_runner.py \
  tests/integration/test_econometrics_exit_security.py \
  tests/integration/test_econometrics_v03_exit_corpus.py -q
# 45 passed

uv run ruff check src/envresearch/econometrics \
  tests/unit/test_econometrics_valuation_exit_models.py \
  tests/integration/test_econometrics_valuation_exit.py \
  tests/integration/test_econometrics_valuation_exit_security.py
# All checks passed

uv run mypy src/envresearch/econometrics
# Success: no issues found in 81 source files
```

`git diff --check` is clean. All changed Python/test files remain at or below
400 lines.

### Self-review

- Runner registry bytes are scanned for evaluator artifact ID/hash, expectation
  fields, comparisons, and every expected scientific code; none are present.
- Catalog authority is evaluator-owned and exact-manifest-bound. Evaluation and
  status do not discover a latest catalog.
- Snapshot authority is checked prior to binding and at evaluation; the planned
  integrity case is authenticated before its deliberate output mutation.
- Corpus symlink resistance uses the descriptor-relative reader for runner and
  evaluator components. Source data mutation does not alter frozen bytes.
- Exact current run/report authority, partial/no-run rejection, stale
  case/data/binding rejection, read-only status, root separation, and legacy
  V0.3 runner/corpus compatibility are covered by executed tests.

## Fix round 2/5 — restart authority and V0.3 API compatibility

### RED evidence

- `tests/integration/test_econometrics_valuation_exit_security.py` added
  `test_valuation_same_run_restart_rejects_forged_binding_data_hash`. It runs a
  complete exact manifest, forges only the current binding `data_sha256`, then
  restarts that same manifest. Command:
  `uv run pytest tests/integration/test_econometrics_valuation_exit_security.py::test_valuation_same_run_restart_rejects_forged_binding_data_hash -q`.
  RED result: failed because restart accepted the forged binding.
- `tests/integration/test_econometrics_exit_runner.py` added
  `test_v03_runner_retains_public_registry_and_executor_attributes`. Command:
  `uv run pytest tests/integration/test_econometrics_exit_runner.py::test_v03_runner_retains_public_registry_and_executor_attributes -q`.
  RED result: `AttributeError` because delegation had removed `runner.registry`.

### Production changes

- The shared executor now checks `binding.data_sha256` against the case's exact
  frozen data reference in both the existing-binding execution branch and
  `verify()` whenever the typed binding carries data authority.
- `V03ExitRunner` again exposes its original public `registry` and `executor`
  attributes while delegating execution to `ResumableExitRunner`.

### GREEN evidence

```bash
uv run pytest tests/integration/test_econometrics_valuation_exit_security.py::test_valuation_same_run_restart_rejects_forged_binding_data_hash \
  tests/integration/test_econometrics_exit_runner.py::test_v03_runner_retains_public_registry_and_executor_attributes -q
# 2 passed
```

```bash
uv run pytest tests/unit/test_econometrics_valuation_exit_models.py \
  tests/integration/test_econometrics_valuation_exit.py \
  tests/integration/test_econometrics_valuation_exit_security.py \
  tests/integration/test_econometrics_exit_runner.py \
  tests/integration/test_econometrics_exit_security.py \
  tests/integration/test_econometrics_v03_exit_corpus.py -q
# 47 passed

uv run ruff check src/envresearch/econometrics \
  tests/unit/test_econometrics_valuation_exit_models.py \
  tests/integration/test_econometrics_valuation_exit.py \
  tests/integration/test_econometrics_valuation_exit_security.py \
  tests/integration/test_econometrics_exit_runner.py
# All checks passed

uv run mypy src/envresearch/econometrics
# Success: no issues found in 81 source files

git diff --check
# clean
```

### Self-review

- The restart test changes only the persisted binding data hash, so it exercises
  the precise stale-authority hole rather than an unrelated revision path.
- The new equality checks execute before reuse of an existing analysis reference
  and before receipt verification; legacy V0.3 bindings remain untouched because
  they do not opt into typed data-hash authority.
- Public compatibility restores references only; V0.3 run schema, subjects, and
  delegated behavior are unchanged.
