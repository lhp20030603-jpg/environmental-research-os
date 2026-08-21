# V0.3 Evidence Runner Operator Guide

V0.3 is a local-data, R-first Evidence Runner for the first diversified method
wave. It provides DiD/event study, Panel FE, IV/2SLS, local-linear RDD, RCT ITT,
Synthetic Control, environmental measurement, and fixed/DL random-effects
Meta-analysis through one shared runtime and one independent result verifier.

## Runtime authority

The final exit used R 4.4.3 and one frozen 99-package dependency projection.
The reviewed pack SHA-256 is:

`89887a069786f8a4a90bd2fbe13df6e6d2eb0993ece0bbba4910204f4b378231`

The pack is imported from already installed local package trees. It records the
package name, version, license string, DESCRIPTION hash, complete installed-tree
hash, closed dependency graph, R version, and common pack hash. It does not claim
source-build provenance. Arbitrary source-package installation remains a later
hardening task.

Each analysis executes from an authenticated, execution-owned copy-on-write
snapshot rather than the mutable source projection. The snapshot is checked
before and after use and then removed. Pack publication places the package trees
and authority records into one atomic generation.

## Formal exit evidence

The final fresh run root was `temp/v03-formal-exit-v4` (temporary and not
committed). The immutable exit report was:

- Run: `run-v03-wave1-local`, generation 17,
  `77dc347fc939a61be21c7a4b34d9ea6107f05f726cfd6f1a50286b60b11d9436`.
- Report: `exit-report-v03-wave1-local-77dc347fc939`, generation 1,
  `87e91a2bd70e133a8511a499aa33f41b0ce533179f07add0ec4916e47c505ce0`.
- Result: `PASSED`, 16/16 matched: eight green analyses, seven planned
  assumption failures, and one output-integrity mutation.

The stock `econometrics exit-run` command reopens the runtime only when both the
absolute reviewed R executable identity and the exact frozen pack hash are
supplied. `econometrics exit-status` is read-only, requires the same frozen pack
root/hash whenever package-backed methods are present, and reproduces the report
from the exact report reference.

## Scope after V0.3

V0.3 is complete for repository-owned local CSV analysis and independent
verification. It does not execute downloaded author code, automatically install
arbitrary R sources, or claim advanced RDD RBC, cluster-randomized RCT, nonlinear
IV, publication-bias correction, spatial models, structural models, or Stata
support. Those extend the existing method-pack kernel in later waves; they do
not require rebuilding the V0.3 runner.

## V0.3.1 Valuation Core exit

V0.3.1 adds Hedonic pricing, Travel-cost, single-bounded Contingent
Valuation, and conditional-logit DCE to the same authenticated local-data
kernel. The formal exit ran the checked nine-case corpus through a copied,
authenticated R 4.4.3 executable and the reviewed 99-package closure above.
It did not use a fake backend, download data or packages, execute author code,
or fall back to a host-language estimator.

The execution-owned run root was `temp/v031-formal-exit-task6-final-v3`
(temporary and not committed). Its current immutable authorities are:

- manifest `valuation-manifest-valuation-core-local` v1,
  `fa2aa77da39e3a9abff934f27dbc0968354f602a82df9a9d469b82f55c2581f2`;
- run `valuation-run-valuation-core-local` v10,
  `0ff1515929895f13a446ee9f65de65d136ddd4f8a4c0bfb67f2f70f8476f3f38`;
- catalog binding `valuation-catalog-binding-valuation-core-local` v1,
  `bb438f33b24049d90a36c9602b5662f64ca322575dd04b2f975d79cf74a6e848`;
- catalog `valuation-catalog-valuation-core-local` v1,
  `e0fa9c3008fe10b2ab3b7775fb8c281b702e222199334f8adb0915d0583302d5`;
- report `valuation-report-valuation-core-local-0ff151592989` v1,
  `797dcaefc1d7c23ae9f7633a63a039d88a932c91a4352f26aa4e337be05e9fba`;
  and
- transition `valuation-transition-v031` v1,
  `c792c13d758af841542aea8004f15d9c033bc588aa4faf48b723fc8b609eb86c`.

The result is `PASSED`, 9/9 matched: four green analyses, four planned
scientific failures, and one output-integrity mutation. The reviewed copied
`Rscript` hash is
`8079ad51572be2c9e2ca3eaaf7f25110647a0866db58b5903693ab9a7c6ee033`.
Green sensitivity tolerances are fixed from checked input scale, not realized
estimates: Hedonic uses half the smallest positive sale-price increment
(`CNY 500`), while DCE uses half the observed cost-support range (`CNY 10.5`).
CV verification independently reconstructs bid-response consistency from typed
probabilities and the exact `bid_yes_shares.csv` output, whose authenticated
green hash is
`ee7b8480b877116eee940542e7fd32c43db97a27959b6fdef466e960badcc280`.
Operators must preserve the absolute frozen-pack root and exact pack hash when
reopening this transition; the transition harness independently reopens the
manifest, run, catalog binding, catalog, report, analysis snapshots, raw
outputs, runtime, and package authorities. It rechecks the complete current
transition/report/run/catalog-binding chain before and after reconstruction and
accepted-report materialization and rejects stale current pointers.

The final v3 verification passed `2,128` unit/integration tests with `15`
expected capability skips and no failures. Coverage was
`24,203/26,566 = 91.10517202439208%`; independent review returned APPROVE with
no Critical, Important, or Minor findings.

After V0.3.1 the executable method registry is frozen. Spatial, Exposure,
environmental Forecasting/Wave-3 structural models, and Stata are explicit
capability gates, not executable recipes. The next implementation stage is
V0.4 Paper Builder, which consumes exact accepted artifact references and
reopened `LocalAnalysisReport` payloads. It must not refit models or reopen
method expansion.
