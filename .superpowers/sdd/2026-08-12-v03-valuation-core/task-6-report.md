# Task 6 TDD, Real-R, and Verification Report

**Task:** Formal V0.3.1 exit and V0.4 handoff
**Implementation base:** `02c5ca6`
**Follow-up reseal base:** `dbcd736`
**Date:** 2026-08-12

## 1. Implementation

- Added a frozen post-V0.3.1 executable-method boundary with four explicit
  non-executable capability gates: Spatial, Exposure, Forecasting/Wave-3, and
  Stata.
- Added `V031TransitionMarker`, a read-only exact-reference harness, transition
  publication, and the future Paper Builder input adapter for accepted green
  `LocalAnalysisReference`/`LocalAnalysisReport` pairs.
- Bound invalid-input analysis identity to the SHA-256 of the exact bytes, so
  two structurally invalid datasets cannot reuse one report.
- Preserved exact data authority for pre-snapshot scientific rejection while
  continuing to require a snapshot for every green outcome and to reject every
  non-null mismatched snapshot.
- Replaced non-estimable four-row checked fixtures with deterministic local
  reviewed-support fixtures and sealed every actual green output hash.
- Kept offline unit/integration fakes in a copied test-only marker catalog; the
  checked real-R expectation catalog contains only authenticated outputs.
- Added a typed CV bid-level yes-share output and made probability/bid
  diagnostic verification reconstruct independently from that artifact rather
  than trusting an estimator-produced summary.
- Revalidated the complete V0.4 handoff current chain before and after public
  report reconstruction and accepted-report materialization.
- Added the V0.4 Paper Builder handoff design, operator record, repo-local
  project memory update, and final evidence report. The external Obsidian daily
  note was not edited.

## 2. TDD RED evidence

### Formal acceptance RED

Command:

```bash
uv run pytest tests/integration/test_econometrics_valuation_acceptance.py -q
```

Exact result: `1 failed, 1 skipped`. The intentional failure was
`ModuleNotFoundError: No module named 'envresearch.econometrics.extension_registry'`.
This proved the frozen extension/capability boundary did not exist before its
implementation.

### Exact invalid-input binding RED

The first authenticated run stopped with:

```text
VALUATION_EXIT_INVALID: exit analysis snapshot is not bound to its exact data
```

The DCE choice-set rejection occurs before snapshot persistence. A regression
test then submitted two different invalid exact byte strings under the same
spec and proved that both returned the same `LocalAnalysisReference`. The
minimal GREEN bound `_invalid_analysis_id` to the expected data SHA-256 and
allowed only a pre-snapshot scientific rejection to omit a snapshot. Changed
invalid bytes now receive different immutable references.

### Checked-corpus support RED

The first real R probe exposed two concrete fixture failures:

- four-row CV caused `glm.fit: fitted probabilities numerically 0 or 1`, which
  is fatal under the authenticated runtime's `options(warn=2)`; and
- four-row Travel-cost was rank-deficient and `fixest` rejected the saturated
  specification after collinear-term removal.

`test_checked_green_corpus_has_estimable_support` then failed at
`assert len(cv_rows) >= 30` because the checked fixture had four rows. The
deterministic GREEN fixtures now contain Hedonic 24 rows, Travel-cost 30 rows,
CV 40 rows, and DCE 60 rows. Travel exposure varies deterministically from 361
to 390, avoiding offset/intercept collinearity. `cv-fail.csv` separately
preserves an exact bid-mean nonmonotonicity of 0.7, 0.4, 0.6, 0.3; the green CV
means are 0.7, 0.6, 0.4, 0.3.

Authenticated R observed DCE sensitivity change `6.963` versus the placeholder
threshold `1.0`, and Hedonic sensitivity change `412.003 CNY` versus `0.25`.
An initial correction to `10.0`/`500.0` was rejected during independent review
because it followed those outputs without a predeclared scientific rule. Before
any further value edit, the report recorded and the acceptance test RED asserted
two input-only rules: one-half the smallest checked Hedonic sale-price increment
(`CNY 1,000 / 2 = CNY 500`) and one-half the checked DCE cost support
(`(CNY 28 - CNY 7) / 2 = CNY 10.5`). The DCE RED was `10.0 != 10.5`; after
correcting it, an authenticated focused R run passed, then the complete nine-
case matrix was rerun and resealed. No significance gate, model-family change,
or automatic model switching was added.

### Focused GREEN

```text
Ruff: All checks passed!
acceptance + valuation exit + exit security + valuation exit models:
29 passed in 9.13s
```

The real-root acceptance alone previously passed `4 passed in 7.64s`.

## 3. Reviewed local runtime and packages

Source authority:

```text
pack root: <local-reviewed-runtime-pack>/v03-final-runtime-pack-v2
pack SHA-256: 89887a069786f8a4a90bd2fbe13df6e6d2eb0993ece0bbba4910204f4b378231
R: 4.4.3
```

The system R executable was copied to the new execution-owned path
`temp/v031-formal-exit-task6-final-v3/reviewed/Rscript`; its SHA-256 is
`8079ad51572be2c9e2ca3eaaf7f25110647a0866db58b5903693ab9a7c6ee033`.
Frozen-pack preflight loaded 99 authorities, including:

| Package | Version | Record SHA-256 |
|---|---:|---|
| `fixest` | 0.14.0 | `82e1183cebfcd6aebffd31455433edeae1363c89f0d266a146e5c3776a89928b` |
| `MASS` | 7.3-64 | `86b6a63bbcf35607cd653fb8cbcd629b5dfcd53fcbc207db517ddf889ec81674` |
| `survival` | 3.8-3 | `6563a6f50623e9de3d1ad547951252e253ac2e941735ecbeedc3e9c22768a213` |

There was no network access, installation, download, package mutation, author
code, or host-language estimator fallback.

## 4. Real-R execution commands and result

With `ROOT` set to the absolute
`.../.worktrees/v03-did-tier2-pilot/temp/v031-formal-exit-task6-final-v3` and
`PACK` to the absolute reviewed pack root, the real execution used:

```bash
uv run envresearch econometrics valuation-exit-run \
  "$ROOT/refs/manifest.json" \
  --runner-root "$ROOT/runner" \
  --evaluator-root "$ROOT/evaluator" \
  --analysis-root "$ROOT/analysis" \
  --r-executable "$ROOT/reviewed/Rscript" \
  --r-sha256 8079ad51572be2c9e2ca3eaaf7f25110647a0866db58b5903693ab9a7c6ee033 \
  --frozen-r-pack-root "$PACK" \
  --frozen-r-pack-hash 89887a069786f8a4a90bd2fbe13df6e6d2eb0993ece0bbba4910204f4b378231 \
  --json

uv run envresearch econometrics valuation-exit-evaluate \
  "$ROOT/refs/run.json" "$ROOT/refs/catalog.json" \
  --runner-root "$ROOT/runner" \
  --evaluator-root "$ROOT/evaluator" \
  --analysis-root "$ROOT/analysis" \
  --frozen-r-pack-root "$PACK" \
  --frozen-r-pack-hash 89887a069786f8a4a90bd2fbe13df6e6d2eb0993ece0bbba4910204f4b378231 \
  --json

uv run envresearch econometrics valuation-exit-status \
  "$ROOT/refs/report.json" \
  --runner-root "$ROOT/runner" \
  --evaluator-root "$ROOT/evaluator" \
  --analysis-root "$ROOT/analysis" \
  --frozen-r-pack-root "$PACK" \
  --frozen-r-pack-hash 89887a069786f8a4a90bd2fbe13df6e6d2eb0993ece0bbba4910204f4b378231 \
  --json
```

Result: `passed`, 9/9 `matched`. A second `valuation-exit-run` returned the
identical run reference without re-execution.

`green-cv` was first executed through the same authenticated service as the
expectation-sealing probe. The final nine-case command recovered that exact
receipt, executed the other seven estimable/scientific cases, exercised the
integrity mutation, and published the complete v10 run. No fake backend entered
the final root.

## 5. Exact authorities and case receipts

| Authority | Artifact/version | Hash |
|---|---|---|
| Manifest | `valuation-manifest-valuation-core-local` v1 | `fa2aa77da39e3a9abff934f27dbc0968354f602a82df9a9d469b82f55c2581f2` |
| Run | `valuation-run-valuation-core-local` v10 | `0ff1515929895f13a446ee9f65de65d136ddd4f8a4c0bfb67f2f70f8476f3f38` |
| Catalog binding | `valuation-catalog-binding-valuation-core-local` v1 | `bb438f33b24049d90a36c9602b5662f64ca322575dd04b2f975d79cf74a6e848` |
| Catalog | `valuation-catalog-valuation-core-local` v1 | `e0fa9c3008fe10b2ab3b7775fb8c281b702e222199334f8adb0915d0583302d5` |
| Report | `valuation-report-valuation-core-local-0ff151592989` v1 | `797dcaefc1d7c23ae9f7633a63a039d88a932c91a4352f26aa4e337be05e9fba` |
| Transition | `valuation-transition-v031` v1 | `c792c13d758af841542aea8004f15d9c033bc588aa4faf48b723fc8b609eb86c` |

| Case | Status/code | Analysis ref hash | Data hash |
|---|---|---|---|
| `fail-cv-wtp` | `CV_MONOTONICITY_FAILED` | `dc1c4ff5ecdb43b27da0088478f76db5ea277be2f63de6f537217515f84e85dc` | `ad92810c2e5f6fb784a24f7441c15923360e1745d68ed65573c10e3968f9d3cd` |
| `fail-dce-choice-set` | `DCE_CHOICE_SET_INVALID` | `59e7cfcde49854b2067d0a4052d50bfbace74e27b45fa743e414e6eedac1252a` | `843406e732085fa70f206cad1b07f5ba5a90634d8bdbed95afb829e2c1965ebc` |
| `fail-hedonic-sensitivity` | `HEDONIC_SENSITIVITY_EXCEEDED` | `dcaa282171c0115c9462f4240fc34fcdbd5ce945830c7fcb2d1bb1ca7c591fdf` | `5e6fb10eab23b058d05457c51c91efe8c78337ec7ea2f8afcbd4168f1d66dbdc` |
| `fail-travel-dispersion` | `TRAVEL_COST_DISPERSION_EXCEEDED` | `3bcbd7253c356c9c9758c1f8ffb5f5af2f4c95452e9cbb4576539ba313bb5519` | `11ab36c796d50897b1676a743d69bc184a8513e327b06b9eb61489b520699e59` |
| `green-cv` | passed | `785ab0c97f8681738c9c0cdb60b8dc9a9619cd29771919417fda85ccd9f53ad4` | `f7c98cbf611a6c1829cf70b2ed133ee568294a71915276a8c611de086fa33802` |
| `green-dce` | passed | `32d37f516a849c79e874ecbea0a1dcff56a6cd94b2ce5a9de7cfb96bf43db1af` | `1cac65fee28a69d6375e3bdb2b7260a08e430b3365e8993f25e10bce5a296234` |
| `green-hedonic` | passed | `c69cfd27e402d0dd58f7389e5ba2a187e5106285eb210fff06856979b257be2b` | `5e6fb10eab23b058d05457c51c91efe8c78337ec7ea2f8afcbd4168f1d66dbdc` |
| `green-travel-cost` | passed | `1d307d11a95e6ff73b1f760a2e033fb9a9b3a034df890d04bf3217a3bb91139d` | `11ab36c796d50897b1676a743d69bc184a8513e327b06b9eb61489b520699e59` |
| `integrity-output-tamper` | `EVIDENCE_TAMPERED` | `b8169673a4a89a829a30a0a552a73fa43b84c2e57e8771cb61128f9b7d9612af` | `5e6fb10eab23b058d05457c51c91efe8c78337ec7ea2f8afcbd4168f1d66dbdc` |

The exact restart status JSON hash is
`f2fe070ef99f349a28ccb2190fa2515915b24dbc88943d364ea3c16af13d5ad2`.

## 6. Authenticated green output hashes

The checked expectation catalog contains the exact hashes of all real outputs:

- Hedonic: coefficients `e66122878d929ae290214fb64fde73a816cd756558112a0ec8ae8c1f90674f07`,
  collinearity `038fa57dfcd183a9ef9ee8556f3dccb4cc88077944c96bdfac1c1e7c86b316df`,
  covariance `9e776489c87b1654b246d14b6c103a8b44ea77311bc343076e8ac6828d034cf2`,
  plot `56cbcc780a2e5f65a0dc770c1b050332e3fc461c47ccaa7e4c25014168f8c970`,
  implicit price `3f89230bfce062ca25bf13886a2271d958d130166fc8e7b0b44e16011bfba8b7`,
  configuration `b06782b7efc69bb6df1bbf7ecbf86386c38f6f188471f143df5e87db5f16aa4e`,
  sensitivity `846b226e0d893133230b66a2d5f7640ba8e167eee78d2c9925909d24d63e4cf5`,
  support `f0a6fb6da2e827017955a7f64699854ca069b1ea7e9ebcc71f6a5e29c15d18e8`.
- Travel-cost: coefficients `4948f75529cc6df387d5bfb798546dd068d8ee14e8e53b7aac2e62abddc8a652`,
  consumer surplus `f2c5ae7ece6d8a91a850892b85a5b8afdec516b28d2df17da0094081595da34c`,
  covariance `150cfa38faba326a95eec5ca3ddf23cad6e50e9b8c21f779b01b51a1f28fa8b9`,
  dispersion `45407fd32e7f1874957469989053771c96ec5a2118b9847bccede5d6b741ef3e`,
  fit `f6f440bea0ed546e3bfa7bbe289be8015c5c92fcad724107e710f1a748b5f858`,
  configuration `66fa68c025ae1ae11603e98ca25ce6ee65f645615f684e5e1a7b24a980723438`,
  sensitivity `45a810c4d640196ddd429002a9fc5fdbb2c2317f505af6f683f5e59392f98118`,
  support `939dd11df61d9d71fdfb7f8211518c7c76a6751a9904bd71202724ef25472b9a`,
  plot `98e9a7f94b7bea4b6504e9e4bb44aee943963f40a2358eddfe7fcd4bacafdc4c`.
- CV: support `7f44189e3d40347e2f04f9335d2614d68febb219e0a88bdb9bd4243e3c7a5b47`,
  bid-level yes shares `ee7b8480b877116eee940542e7fd32c43db97a27959b6fdef466e960badcc280`,
  coefficients `b4e18831bfbf30d108e95097e00876868e2d22c4d78733344258026a1ac30aff`,
  covariance `51aa4e6085f294df94ff836ff01a7d4ebba7de82e0cc12a01d6cd9fa37e154d2`,
  plot `f13ed6a04760e807e07fc94205ce9a7bb1e8bb4bda0cff74d9916f1ed5c781b8`,
  configuration `78c7a759a2a4a59265419bf5f8016c0fe9fcc0c08a4a454e14f0bcbe3981e9b5`,
  probabilities `e945bdc63b3ebfa36130a238e9b3481df62b01266c00b37681885a58a324954d`,
  sensitivity `45c4d5663a28a57b94968327101bca60638b9e6be98c9f3f9ee9ca63f2ed12a2`,
  WTP `88a4e934ef792c94727848838cbba84d945c48865925db5bce5b85b76f31ebf1`.
- DCE: support `5f6e38fb8c84c3d1f9c55e8950f4f1ef3ce403a5ebf91c588e54d43c7c67fa1b`,
  coefficients `946491db09e95852a7333e417759900646457560b371720023edae00149b7182`,
  covariance `34ee42d54a5774e4c9bfc78b93da9f49bccef4865aa6ce55963c214c19c1d9bb`,
  plot `1c00d38d51c18d9dc8e03a5b20df225cff055cdea903cbaad534ce75a878064e`,
  configuration `0c43bd0991df06d1548ae8423ce97528c399538b3ee57cac674f9a880615297c`,
  sensitivity `98e7261d167cb51bf03fdfc949d73d0f0f663677dfe4a76a837e53be72af665f`,
  WTP `898948b09639eb6d6b47b6aacd0924dcc9685b3c34dfebf334db54171cd0bcb2`.

## 7. Mutation, restart, current authority, and recovery

- The transition was sealed only after exact status reproduced 9/9.
- Focused transition tests copied the v3 sealed root and proved transition,
  report, run, and catalog-binding mutations fail closed during reconstruction
  and accepted-report materialization without mutating the authoritative root.
- A fresh process ran `valuation-exit-status`; the JSON was byte-hashed above.
- Re-running `valuation-exit-run` returned the exact v10 run reference and did
  not re-execute completed receipts.
- The report, transition, run, catalog binding, and catalog are all required to
  remain current; the harness rejects a stale exact ref even when its history
  bytes still exist.

## 8. Final matrix

The first full coverage measurement was an additional engineering RED:
`2,002 passed, 15 skipped`, 26,450 statements and 2,607 missed
(`90.14366729678639%`). That was below the recorded
`91.03460030680075%` baseline, so it was not accepted or described as stable.
Targeted behavior/security tests were then added for exact-input identity,
evidence collision, frozen-package authority, output/result reconstruction,
runner/evaluator current state, CLI error mapping, descriptor races, and
managed-R archive/tree closure. No coverage threshold, source denominator, or
scientific threshold was lowered.

Formal Task 6 full-matrix commands and results before the bounded follow-up:

- `uv sync --locked --dev`: PASS; 35 packages resolved, 33 audited.
- `ENVRESEARCH_V031_ACCEPTANCE_ROOT="$PWD/temp/v031-formal-exit-task6-final-v2"
  uv run pytest tests/unit tests/integration -q --cov=envresearch
  --cov-report=term-missing
  --cov-report=json:temp/v031-formal-exit-task6-final-v2/coverage-final-clean.json
  --cov-fail-under=80`: final post-review PASS; `2,114 passed, 15 skipped, 0
  failed`, 28 warnings in 543.55 seconds.
- Coverage numerator/denominator: 24,129 covered / 26,486 statements; 2,357
  missed; exact `91.10095899720608%`, above the recorded baseline by
  `0.06635869040533` percentage points.
- The 15 skips are expected environment/capability skips: explicitly disabled
  optional real-R smoke families, privileged foreign-owner checks, and a
  platform filesystem-alias check. The sealed Task 6 real-R acceptance did not
  skip.
- `ENVRESEARCH_V031_ACCEPTANCE_ROOT=... uv run pytest
  tests/integration/test_econometrics_valuation_acceptance.py -q`: PASS;
  `5 passed` in 8.59 seconds after the final threshold/registry/real-root
  acceptance additions.
- `uv run ruff check .`: PASS after mechanical import-order/unused-import
  cleanup in new tests.
- changed-file `uv run ruff format --check ...`: PASS; all changed Python files
  formatted. The format-only refactor retained the 400-line cap and its
  affected branch suite passed `13 passed` in 0.33 seconds.
- `uv run mypy src`: PASS; 299 source files.
- `uv lock --check`: PASS; 35 packages resolved.
- `git diff --check`: PASS.
- changed Python/R line cap: PASS; every changed file is at most 400 lines
  (largest: 398).
- downloaded-payload scan: PASS; no binary diff, archive, non-text downloaded
  payload, or changed file over 1 MiB outside the deliberately untracked
  `temp/` evidence root.

The bounded follow-up was subsequently verified by a fresh v3 full matrix:

- `ENVRESEARCH_V031_ACCEPTANCE_ROOT="$PWD/temp/v031-formal-exit-task6-final-v3"
  uv run pytest tests/unit tests/integration -q --cov=envresearch
  --cov-report=term-missing
  --cov-report=json:temp/v031-formal-exit-task6-final-v3/coverage-final.json
  --cov-fail-under=80`: PASS; `2,128 passed, 15 expected skips, 0 failed`, 28
  warnings in 824.92 seconds;
- coverage numerator/denominator: 24,203 covered / 26,566 statements, 2,363
  missed, exact `91.10517202439208%`, above the recorded
  `91.03460030680075%` baseline; and
- the 15 skips remain the expected optional-runtime, privilege, and platform
  capability skips. The sealed real-R acceptance did not skip.

The supporting v3 matrices were:

- real-root acceptance: `5 passed in 20.66s`;
- transition security/materialization plus CV reconstruction and affected unit
  tests: `53 passed in 454.36s`;
- broad econometrics: `595 passed, 10 expected skips, 0 failed in 473.56s`;
- `uv sync --locked --dev`: PASS, 35 resolved and 33 audited;
- `uv run ruff check .`: PASS;
- changed-file `uv run ruff format --check ...`: PASS, 13 files;
- `uv run mypy src`: PASS, 299 source files;
- `uv lock --check`: PASS, 35 packages;
- `git diff --check`: PASS;
- changed Python/R line cap: PASS, maximum 391 lines; and
- downloaded-payload scan: PASS, 15 non-temp paths, maximum 15,942 bytes, no
  binary, archive, or file over 1 MiB.

The checked expectation file SHA-256 is
`2f29dc9fa34d42a53ecfc2549561928583ab6dba8ce201fe29dbe705d4268c0c`;
its sole follow-up corpus change is the authenticated exact comparison for
`bid_yes_shares.csv`. No threshold, fixture, model, or method changed.

## 9. Files

Production and contracts:

- `src/envresearch/econometrics/service.py`
- `src/envresearch/econometrics/exit_runner.py`
- `src/envresearch/econometrics/exit_evaluator.py`
- `src/envresearch/econometrics/extension_registry.py`
- `src/envresearch/econometrics/valuation_transition.py`
- `src/envresearch/econometrics/contingent_valuation.py`
- `src/envresearch/econometrics/_valuation_diagnostics.py`
- `src/envresearch/econometrics/_valuation_evidence.py`
- `src/envresearch/econometrics/_valuation_outputs.py`
- `src/envresearch/econometrics/_valuation_verification.py`
- `src/envresearch/econometrics/valuation_results.py`
- `src/envresearch/econometrics/templates/contingent_valuation.R`

Checked corpus: the exact expectation catalog under
`benchmarks/econometrics/valuation-core/`; the follow-up added only the exact
`bid_yes_shares.csv` comparison. Fixture bytes and scientific thresholds were
unchanged.

Tests:

- `tests/integration/test_econometrics_valuation_acceptance.py`
- `tests/integration/test_econometrics_valuation_exit.py`
- `tests/integration/test_econometrics_valuation_exit_security.py`
- `tests/integration/test_econometrics_v031_transition_security.py`
- `tests/integration/test_econometrics_valuation_corpus_boundaries.py`
- `tests/integration/test_econometrics_valuation_exit_branch_boundaries.py`
- `tests/integration/test_econometrics_valuation_result_boundaries.py`
- `tests/integration/test_econometrics_valuation_verifier_boundaries.py`
- `tests/integration/test_econometrics_valuation_welfare_boundaries.py`
- `tests/integration/test_econometrics_valuation_service_authority.py`
- `tests/integration/test_econometrics_valuation_cli_error_mapping.py`
- `tests/integration/test_econometrics_cv_probability_verification.py`
- `tests/integration/test_econometrics_v031_transition_materialization.py`
- `tests/integration/econometrics_valuation_verifier_fixtures.py`
- `tests/unit/test_econometrics_frozen_r_library_boundaries.py`
- `tests/unit/test_econometrics_local_backend_boundaries.py`
- `tests/unit/test_econometrics_store_file_security.py`
- `tests/unit/test_econometrics_valuation_data.py`
- `tests/unit/test_econometrics_valuation_diagnostic_boundaries.py`
- `tests/unit/test_econometrics_valuation_evidence_boundaries.py`
- `tests/unit/test_econometrics_valuation_output_boundaries.py`
- `tests/unit/test_econometrics_contingent_valuation.py`
- `tests/unit/test_econometrics_valuation_results.py`

Documentation:

- `docs/econometrics-v03-operator-guide.md`
- `.claude/project-memory/environmental-research-os.md`
- `docs/superpowers/specs/2026-08-12-v04-paper-builder-design.md`
- this report and `final-report.md`
- the implementation-plan checkbox update

No `temp/` file is staged or committed. Because the repository does not have a
root `/temp/` ignore rule, final staging must enumerate the reviewed production,
test, corpus, report, guide, and repo-local memory paths explicitly and must not
use broad `git add -A` or `git add .`.

## 10. Self-review

- **Scientific thresholds:** The final green thresholds are derived solely from
  checked input resolution/support and asserted before execution. The initial
  output-following correction was rejected, documented, replaced, and the full
  real-R exit rerun. No significance gate, family switch, estimator switch, or
  hidden selection was added.
- **Runtime/package authority:** Execution requires the copied R hash and frozen
  pack hash; status reconstruction loads exact package authorities.
- **Snapshot/output reconstruction:** Green outcomes require exact snapshots;
  all actual outputs are reopened, SHA-verified, and catalog-matched. CV
  probability/bid diagnostics are independently reconstructed from typed
  probabilities and bid-level yes shares. Invalid pre-snapshot input identity
  includes the exact data hash.
- **Corpus separation:** Runner cannot read evaluator expectations. Offline
  marker fakes use a temporary copied test corpus, never the checked catalog.
- **Current/ref authority:** The evaluator transition pointer is the sole handoff
  authority and the complete transition/report/run/catalog-binding chain is
  checked before and after every public reconstruction and accepted-report
  materialization.
- **Recovery/concurrency:** Candidate status is authenticated before atomic
  promotion under a dedicated transition lock. A two-process distinct-publisher
  test proves one winner, one already-sealed loser, and a current reopenable
  winner; repeat execution, mutation, and restart behavior were also exercised.
- **Report truthfulness:** This report distinguishes TDD fakes from the real
  frozen-R exit and records the concrete fixture/threshold corrections.
- **Compatibility:** Existing V0.2/V0.3 serialized variants and default
  non-snapshot exit behavior are unchanged.
- **V0.4 scope:** The handoff consumes exact accepted evidence and stops method
  expansion; no Paper Builder, Spatial, Exposure, Forecasting/Wave-3, Stata, or
  V0.5--V0.9 implementation is present.

## 11. Independent review and concerns

Independent review checkpoint verdict: **BLOCK / Important findings; no Critical
finding confirmed**. The reviewer reported:

1. `publish_v031_transition` published evaluator current and the top-level
   pointer before independently validating the candidate and had no transition
   lock/recovery protocol. Invalid or concurrent publishers could leave an
   unreopenable/non-current marker, contradicting the claim that sealing occurs
   only after exact 9/9 reproduction.
2. `_invalid_analysis_id` changed legacy `run(spec)` invalid-input identities by
   hashing `{"spec": ..., "snapshot_sha256": null}` even when the caller did
   not use exact authenticated bytes, breaking V0.2/V0.3 durable recovery
   compatibility.
3. The observed-output-following green thresholds (`10.0` after DCE `6.963`;
   `500.0` after Hedonic `412.003`) lacked an ex-ante scientific adequacy rule
   and therefore risked benchmark/threshold overfitting.
4. The acceptance reserved-method set omitted gated `spatial-error`. The
   reviewer also observed a 422-line formatted test snapshot; subsequent
   behavior-preserving compaction is 398 lines and the line cap is rechecked.

Proposed ex-ante threshold rule before any further value edit: benchmark green
thresholds must be derivable from declared source units/support and a symmetric,
predeclared adequacy tolerance, never from the realized estimator output. For
Hedonic, the current sensitivity is an absolute implicit-price change in CNY;
the threshold will be fixed at one-half of the smallest positive checked sale-
price increment (`CNY 1,000 / 2 = CNY 500`). For DCE, WTP is in CNY per
attribute unit, so the threshold will be fixed at one-half of the full checked
cost support (`(CNY 28 - CNY 7) / 2 = CNY 10.5`). Both are deterministic
resolution/support rules computed solely from authenticated input columns and
declared units, independent of the observed `412.003` and `6.963` sensitivity
outputs. These rules will be asserted from checked fixture support before the
case value is changed and the complete nine-case exit is rerun and resealed. If
the checked data cannot satisfy an output-independent rule, the fixture/spec
will fail rather than widening the threshold.

The first repair round closed all four findings: single locked evaluator-current
authority with validate-before-promote, legacy spec-only invalid IDs plus exact-
byte IDs, input-scale threshold assertions followed by a new 9/9 sealed real-R
root, and exhaustive reserved recipe gates. Focused acceptance/security was
`19 passed`.

The second reviewer checkpoint remained **BLOCK / 2 Important, 0 Critical**:
the public harness could still opt into a non-current candidate and did not
recheck current after construction, while durable reports/full gates still
described the superseded run. Strict RED was `2 failed` in 16.68 seconds. The
public candidate escape is now removed; private publication-only candidate
reconstruction remains, public reconstruction checks current before and after,
and accepted report consumption rechecks after materialization. Acceptance plus
transition security then passed `21 passed` in 80.48 seconds. A two-process
distinct-seal characterization passed `1 passed` in 21.64 seconds. Durable v2
authorities were replaced before the post-repair full/static matrix and final
clean re-review.

The next final adversarial checkpoint remained **BLOCK / 1 Important, 0
Critical**: a harness constructed before a transition-object or copied-runtime
mutation compared only the unchanged current ref during public pre/post checks,
so cached marker/runtime authority could still return `passed`. Both concrete
after-construction mutations become strict REDs below. The minimal repair must
reload and compare the exact marker and reauthenticate the runtime/pack at every
public pre/post check, then perform the same candidate reauthentication after
private reconstruction and immediately before atomic promotion.

Strict RED was `2 failed` in 23.60 seconds: both the exact transition-object
replacement and copied-runtime append incorrectly returned `passed`. The repair
exact-loads and compares the marker and reauthenticates the runtime/pack during
every public pre/post/materialization check; the private candidate repeats it
immediately before promotion. The regressions passed `2 passed` in 9.16 seconds,
and the affected acceptance/security suite passed `24 passed` in 179.36 seconds.
Final independent re-review verdict: **APPROVE — 0 Critical, 0 Important, 0
Minor findings**. The reviewer independently reran the two mutations: `2 passed
in 9.13s`. The final full/static matrix above followed this repair.

The bounded v3 reseal received a fresh independent final verdict:
**APPROVE — 0 Critical, 0 Important, 0 Minor findings**. The reviewer verified
the sole checked expectation addition, all six artifact refs and current
pointers, restart status hash, historical-coverage versus v3-delta reporting,
untracked-temp staging warning, external-vault non-mutation, and the CV/current-
chain implementation. Their independent focused rerun passed `31 tests`; Ruff,
diff, and lock checks also passed.

Known concerns: the execution-owned real-R run root and reviewed R copy are
untracked temporary evidence. The durable committed record contains exact refs
and hashes, but independently reopening the live transition still requires
preserving that local run root and the reviewed frozen pack at their recorded
absolute paths. Because `temp/` is not protected by an ignore rule, broad
staging would be unsafe; use only the explicit reviewed path set.
