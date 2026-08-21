# V0.3.1 Valuation Core Design

**Status:** Approved
**Date:** 2026-08-12
**Governing design:** `plan/2026-08-04-环境经济学与政策研究操作系统-设计规范.md`

## 1. Objective and release boundary

V0.3.1 adds one shared environmental-valuation capability pack rather than four
independent infrastructure projects. It delivers Hedonic pricing, Travel-cost,
single-bounded Contingent Valuation, and long-format Discrete Choice Experiments
on explicit local data. All four methods reuse the V0.3 authenticated data,
frozen R runtime, generated-script execution, result registry, independent
verification, exact-reference CLI, recovery, and blinded evaluator.

After V0.3.1 passes its exit matrix, method expansion stops and work proceeds to
V0.4 Paper Builder. Spatial econometrics, exposure models, environmental time
series/forecasting, all Wave-3 structural models, Stata support, and arbitrary R
source installation remain extension points. They do not block V0.4.

## 2. Architecture

```text
Authenticated local data snapshot
        -> ValuationSpec contract
        -> method-specific repository-owned R recipe
        -> typed estimates, diagnostics, welfare evidence and SVG
        -> existing immutable result registry
        -> independent snapshot/output/welfare reconstruction
        -> compact 9-case blinded exit evaluator
```

The stable kernel remains method-neutral. A discriminated `ValuationSpec` union
and typed result union contain scientific differences. New methods register
through the existing recipe/profile interfaces; no consumer may branch on file
names, prose, or untyped dictionaries.

## 3. Shared contracts

Every spec binds the outcome or choice, cost/price/bid variables, units,
population, data shape, inference, registered functional forms, diagnostic
thresholds, and resource budget before execution. Every accepted result binds:

- the exact input snapshot and spec hash;
- the reviewed R executable and frozen package authorities;
- coefficients, covariance or standard errors, confidence intervals and sample
  support;
- method-specific diagnostics and every registered sensitivity result;
- welfare quantity, currency/price base, time basis, population basis and the
  coefficient transformation used to obtain it;
- deterministic table and numeric-axis SVG evidence.

The verifier reopens raw snapshot bytes and output bytes, reconstructs support,
diagnostics and welfare values independently, and rejects stale or revised
lineage. Statistical significance is never an acceptance condition.

## 4. Method contracts

### 4.1 Hedonic pricing

Supported data are cross-section, repeated cross-section, or panel property
transactions. Required roles are transaction price, environmental attribute,
structural controls and location controls; optional declared time/location fixed
effects and clusters are allowed. The registered model is exactly one of level,
log-level, level-log, or log-log, with the transformation declared before run.

Required evidence includes support/range, coefficient and marginal implicit
price in declared units, robust or clustered uncertainty, fixed-effect structure,
multicollinearity diagnostics, and one preregistered functional-form sensitivity.
Missing price/environmental support, invalid log domains, unidentified terms,
severe collinearity, or sensitivity beyond its declared threshold is a scientific
failure.

### 4.2 Travel-cost model

Supported data are individual or zonal visitation counts. Required roles are
visits, travel cost, exposure/population offset, site identity and declared
substitute-site controls. V0.3.1 supports Poisson and Negative Binomial count
demand. The family is selected by the analysis plan, not by searching for a
preferred result.

Required evidence includes count support, exposure validity, dispersion,
zero-visit share, goodness of fit, travel-cost coefficient, consumer-surplus
transformation and sensitivity to the registered substitute-site specification.
Invalid offsets, a nonnegative cost slope, or Poisson retained despite dispersion
above its threshold is a scientific failure.

### 4.3 Contingent valuation

V0.3.1 supports single-bounded dichotomous-choice referendum data with one
yes/no response, positive bid and declared respondent covariates. Logit and probit
are registered alternatives chosen before execution. Double-bounded, payment-card
and open-ended WTP are deferred.

Required evidence includes bid coverage, yes-share by bid, fitted probability
range, bid coefficient, mean/median WTP when mathematically identified, confidence
intervals, and one registered covariate sensitivity. Invalid bids, nonmonotone
yes-probability with bid, separation/extreme probabilities, a nonnegative bid
coefficient, or undefined/unbounded WTP is a scientific failure.

### 4.4 Discrete Choice Experiment

Input is long format with respondent, choice-set, alternative, chosen indicator,
cost and declared attributes. Each respondent/choice-set must contain unique
alternatives and exactly one chosen alternative. V0.3.1 supports conditional
logit with respondent-clustered inference. Mixed logit and latent classes are
deferred.

Required evidence includes respondent/set/alternative support, complete choice
sets, attribute variation, coefficient covariance, cost coefficient, attribute
WTP ratios and a registered alternative-specific-constant sensitivity. Incomplete
sets, more or fewer than one choice, absent within-set variation, unidentified
terms, or a nonnegative/near-zero cost coefficient is a scientific failure.

## 5. Package and data policy

V0.3.1 first uses the already reviewed frozen R closure (`fixest`, `MASS`,
`survival`, and base/recommended R where sufficient). A new package may be added
only when a required estimator cannot be implemented faithfully with that
closure; it must enter the same frozen-tree authority process and cannot create a
method-specific installer.

Repository-owned fixtures are used for deterministic CI and the exit matrix.
Methods never own or automatically download datasets. A real project may bind a
licensed immutable data pack through the existing acquisition/provenance gates.

## 6. Fail-closed behavior and integrity

Contract errors stop before R execution. Scientific failures publish typed
exception reports and never trigger automatic method switching. Runtime,
snapshot, output, package-authority, unit, configuration, welfare-reconstruction
or lineage mismatch is an integrity failure. Recovery consumes exact references
and cannot scan for or substitute a latest result. Revisions supersede affected
descendants without overwriting history.

The independent verifier reconstructs support from authenticated local data and
reconciles coefficient intervals, welfare formulas, units, configuration, and
sensitivity transformations across sealed raw outputs. It does not claim that
cross-file algebra can detect a fully coherent replacement of every raw
estimator output; estimator authenticity instead depends on the registered
generated script, reviewed runtime, and exact frozen package tree. Detecting a
coherently substituted estimator solution would require an independent refit and
is outside this compact read-only status boundary.

## 7. Compact exit matrix

The V0.3.1 exit corpus contains exactly nine blinded cases:

1. green Hedonic;
2. green Travel-cost;
3. green Contingent Valuation;
4. green DCE;
5. Hedonic functional-form/support failure;
6. Travel-cost dispersion/offset failure;
7. Contingent Valuation monotonicity or WTP-identification failure;
8. DCE choice-set or cost-coefficient failure;
9. one post-run output-integrity mutation.

The evaluator sees expectations only after the runner seals all receipts. Exit is
all-or-nothing: every green output must reconstruct within declared tolerance,
all four scientific failures must emit the exact expected typed code, and the
integrity mutation must be detected. The real frozen runtime path must be exercised
at least once; normal CI remains offline and fixture-owned.

## 8. Extension interfaces

Future methods add a typed spec/result pair, recipe registration, support and
diagnostic reconstructor, method profile and benchmark cases. They may reuse data
shape primitives but cannot weaken existing contracts. Reserved post-V0.3.1
extensions are:

- spatial weights and spatial-error/lag diagnostics;
- raster/monitor exposure assignment and uncertainty propagation;
- environmental time-series decomposition and forecast evaluation;
- mixed logit, latent class, double-bounded CV and advanced travel demand;
- calibration, solver verification and global sensitivity for Wave-3 models;
- optional Stata adapters behind the same result contract.

## 9. Exit and transition to V0.4

V0.3.1 is complete only when the nine-case report is `PASSED`, all changed source
and test files meet repository type/lint/format/line gates, the full regression
suite is green, and independent review has no Critical or Important finding.

No additional method family is required before V0.4. V0.4 then builds the
claim-evidence ledger, argument map, artifact-grounded full-section writing,
citation/table/figure/policy-language audits, and review/revision loop. V1.0
subsequently composes V0.1–V0.4 into end-to-end approved research runs, hidden
evaluation, governed capability promotion, and releasable paper/reproduction
packages. The governing roadmap intentionally has no artificial V0.5–V0.9
milestones.
