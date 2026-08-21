# V0.3.1 Valuation Core Final Report

**Date:** 2026-08-12
**Status:** PASSED — 9/9 matched
**Scope:** Hedonic pricing, Travel-cost, single-bounded Contingent Valuation,
and conditional-logit DCE

## Release result

The checked corpus ran through `LocalAnalysisService`, repository-generated R,
the execution-owned reviewed `Rscript`, and the frozen local R pack. Independent
evaluation reopened raw snapshots and outputs from exact references. The final
report is current and contains four green cases, four planned scientific
failures, and one detected output-integrity mutation.

CV verification now consumes typed model probabilities and independently
reconstructs the observed bid-response diagnostic from an authenticated
`bid_yes_shares.csv`; it does not trust an estimator-produced diagnostic
summary. The V0.4 handoff rechecks the full current transition/report/run/
catalog-binding chain before and after reconstruction and materialization.

No network access, package installation, download, author code, package-tree
mutation, or host-language estimator fallback was used.

Green sensitivity tolerances are derived from authenticated input scale before
execution: Hedonic uses half the smallest positive checked sale-price increment
(`CNY 500`), and DCE uses half the checked cost-support range (`CNY 10.5`).

## Immutable authorities

| Authority | Artifact/version | SHA-256 |
|---|---|---|
| Manifest | `valuation-manifest-valuation-core-local` v1 | `fa2aa77da39e3a9abff934f27dbc0968354f602a82df9a9d469b82f55c2581f2` |
| Run | `valuation-run-valuation-core-local` v10 | `0ff1515929895f13a446ee9f65de65d136ddd4f8a4c0bfb67f2f70f8476f3f38` |
| Catalog binding | `valuation-catalog-binding-valuation-core-local` v1 | `bb438f33b24049d90a36c9602b5662f64ca322575dd04b2f975d79cf74a6e848` |
| Catalog | `valuation-catalog-valuation-core-local` v1 | `e0fa9c3008fe10b2ab3b7775fb8c281b702e222199334f8adb0915d0583302d5` |
| Report | `valuation-report-valuation-core-local-0ff151592989` v1 | `797dcaefc1d7c23ae9f7633a63a039d88a932c91a4352f26aa4e337be05e9fba` |
| Transition | `valuation-transition-v031` v1 | `c792c13d758af841542aea8004f15d9c033bc588aa4faf48b723fc8b609eb86c` |

The run root is `temp/v031-formal-exit-task6-final-v3` and is intentionally
untracked and excluded from the release scope. The repository has no `/temp/`
ignore rule, so release staging must use explicit paths rather than a broad
`git add -A`.
The copied R 4.4.3 executable hash is
`8079ad51572be2c9e2ca3eaaf7f25110647a0866db58b5903693ab9a7c6ee033`.
The frozen 99-package pack hash is
`89887a069786f8a4a90bd2fbe13df6e6d2eb0993ece0bbba4910204f4b378231`.

Relevant package records are `fixest` 0.14.0
(`82e1183cebfcd6aebffd31455433edeae1363c89f0d266a146e5c3776a89928b`),
`MASS` 7.3-64
(`86b6a63bbcf35607cd653fb8cbcd629b5dfcd53fcbc207db517ddf889ec81674`),
and `survival` 3.8-3
(`6563a6f50623e9de3d1ad547951252e253ac2e941735ecbeedc3e9c22768a213`).

## Case outcomes

| Case | Role | Result/code | Analysis report SHA-256 |
|---|---|---|---|
| `fail-cv-wtp` | scientific failure | `CV_MONOTONICITY_FAILED` | `dc1c4ff5ecdb43b27da0088478f76db5ea277be2f63de6f537217515f84e85dc` |
| `fail-dce-choice-set` | scientific failure | `DCE_CHOICE_SET_INVALID` | `59e7cfcde49854b2067d0a4052d50bfbace74e27b45fa743e414e6eedac1252a` |
| `fail-hedonic-sensitivity` | scientific failure | `HEDONIC_SENSITIVITY_EXCEEDED` | `dcaa282171c0115c9462f4240fc34fcdbd5ce945830c7fcb2d1bb1ca7c591fdf` |
| `fail-travel-dispersion` | scientific failure | `TRAVEL_COST_DISPERSION_EXCEEDED` | `3bcbd7253c356c9c9758c1f8ffb5f5af2f4c95452e9cbb4576539ba313bb5519` |
| `green-cv` | green | passed | `785ab0c97f8681738c9c0cdb60b8dc9a9619cd29771919417fda85ccd9f53ad4` |
| `green-dce` | green | passed | `32d37f516a849c79e874ecbea0a1dcff56a6cd94b2ce5a9de7cfb96bf43db1af` |
| `green-hedonic` | green | passed | `c69cfd27e402d0dd58f7389e5ba2a187e5106285eb210fff06856979b257be2b` |
| `green-travel-cost` | green | passed | `1d307d11a95e6ff73b1f760a2e033fb9a9b3a034df890d04bf3217a3bb91139d` |
| `integrity-output-tamper` | integrity failure | `EVIDENCE_TAMPERED` | `b8169673a4a89a829a30a0a552a73fa43b84c2e57e8771cb61128f9b7d9612af` |

The restart status JSON hash is
`f2fe070ef99f349a28ccb2190fa2515915b24dbc88943d364ea3c16af13d5ad2`.
Mutation tests on copied v3 roots failed closed for stale or changed transition,
report, run, and catalog-binding authority while preserving the sealed root.
Re-running the exit returned the same run reference without executing completed
cases.

## Verification and review

After the bounded CV-diagnostic and V0.4 current-chain repairs, the fresh v3
unit/integration matrix passed `2,128 passed, 15 expected skips, 0 failed` in
824.92 seconds. Coverage is 24,203/26,566 statements with 2,363 missed
(`91.10517202439208%`), above the recorded `91.03460030680075%` baseline.
The sealed real-root acceptance passed all five checks; focused transition/CV
passed 53 tests, and broad econometrics passed 595 tests with 10 expected
skips. Locked sync/lock, Ruff, changed-file formatting, mypy (299 source
files), diff, 400-line, and downloaded-payload gates all passed.
The sole checked expectation change is authenticated `bid_yes_shares.csv` SHA
`ee7b8480b877116eee940542e7fd32c43db97a27959b6fdef466e960badcc280`;
the resulting expectation-file SHA is
`2f29dc9fa34d42a53ecfc2549561928583ab6dba8ce201fe29dbe705d4268c0c`.
No fixture, threshold, model, or method changed.

Fresh independent v3 review is APPROVE with 0 Critical, 0 Important, and 0
Minor findings. The reviewer independently passed 31 focused tests and the
Ruff, diff, and lock checks after verifying the exact authority chain, corpus
delta, report truthfulness, and temp/external-vault hygiene.

## Frozen boundary and V0.4

The post-V0.3.1 executable registry is frozen. Spatial, Exposure,
Forecasting/Wave-3 structural models, and Stata remain capability-gated and no
recipe is registered for them. V0.4 Paper Builder consumes exact accepted
artifact references plus reopened `LocalAnalysisReport` payloads to build
claim-evidence, argument, writing, and audit artifacts. It does not refit
models, reopen method expansion, or create V0.5--V0.9 milestones.
