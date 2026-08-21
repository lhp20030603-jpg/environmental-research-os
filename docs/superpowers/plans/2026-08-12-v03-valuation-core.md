# V0.3.1 Valuation Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver one shared, authenticated local-data valuation pack for
Hedonic pricing, Travel-cost, single-bounded Contingent Valuation, and
conditional-logit DCE, then pass one compact nine-case blinded exit.

**Architecture:** Extend the existing method-neutral econometrics pipeline:
typed specs enter the current snapshot/service/runtime boundary, four registered
repository-owned R recipes emit typed results, and the current independent
verifier reopens raw bytes and reconstructs support and welfare quantities. Reuse
the frozen R closure and exit registries; do not create a second runner, installer,
or dataset subsystem.

**Tech Stack:** Python 3.13, Pydantic v2, Typer, `uv`, R 4.4.3, frozen
`fixest`/`MASS`/`survival`/base R authority, pytest, Ruff, mypy.

## Global Constraints

- Work only in `.worktrees/v03-did-tier2-pilot`; never implement on `master`.
- Use RED-GREEN-REFACTOR for every behavior and commit each task separately.
- Use local immutable CSV inputs only; perform no automatic data download.
- Use the existing frozen R closure. Do not add a package unless a required
  estimator is impossible with `fixest`, `MASS`, `survival`, and base R.
- Execute only repository-generated R scripts from copied authenticated library
  snapshots; no downloaded author code or host-language estimator fallback.
- Welfare evidence must bind currency/price base, time basis, population basis,
  coefficient transformation, estimate, and uncertainty.
- Scientific failures use stable typed codes and never trigger automatic model
  switching. Statistical significance is not an acceptance condition.
- Preserve exact-reference recovery, immutable history, revision invalidation,
  and independent reconstruction from raw snapshot/output bytes.
- Every changed Python/R source and test file must remain at or below 400 lines.
- Finish with exactly 4 green, 4 scientific-failure, and 1 integrity case.
- After this pack passes, stop method expansion and enter V0.4 Paper Builder.
- Do not push or merge without a separate user request.

---

### Task 1: Shared valuation contracts, data shapes, and result models

**Files:**
- Create: `src/envresearch/econometrics/valuation_contracts.py`
- Create: `src/envresearch/econometrics/valuation_results.py`
- Create: `src/envresearch/econometrics/_valuation_csv.py`
- Create: `src/envresearch/econometrics/_valuation_support.py`
- Modify: `src/envresearch/econometrics/analysis_specs.py`
- Modify: `src/envresearch/econometrics/recipes.py`
- Modify: `src/envresearch/econometrics/_causal_csv.py`
- Test: `tests/unit/test_econometrics_valuation_contracts.py`
- Test: `tests/unit/test_econometrics_valuation_data.py`
- Test: `tests/unit/test_econometrics_valuation_results.py`

**Interfaces:**
- Produces `HedonicSpec`, `TravelCostSpec`, `ContingentValuationSpec`, and
  `DiscreteChoiceSpec`, all strict frozen Pydantic models with literal
  `method_id` discriminators.
- Produces `HedonicResult`, `TravelCostResult`, `ContingentValuationResult`, and
  `DiscreteChoiceResult`, plus shared `CoefficientEstimate`, `WelfareEstimate`,
  and `ValuationConfiguration` evidence.
- Extends `AnalysisSpec`, `AnalysisResult`, `required_columns_for()`, and the CSV
  dispatch without changing any existing serialized variant.

- [x] **Step 1: Write contract RED tests**

```python
def test_valuation_specs_join_closed_analysis_union() -> None:
    payloads = (
        hedonic_payload(), travel_cost_payload(), cv_payload(), dce_payload()
    )
    parsed = tuple(ANALYSIS_SPEC_ADAPTER.validate_python(item) for item in payloads)
    assert tuple(item.method_id for item in parsed) == (
        "hedonic-pricing", "travel-cost", "contingent-valuation", "dce-clogit"
    )

@pytest.mark.parametrize("payload", invalid_valuation_payloads())
def test_valuation_specs_reject_ambiguous_authority(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ANALYSIS_SPEC_ADAPTER.validate_python(payload)
```

Include invalid duplicates, missing units, relative/non-CSV data paths,
non-finite thresholds, unregistered transformations/families, missing currency
base, and DCE attribute/cost overlap.

- [x] **Step 2: Run contract tests and confirm RED**

Run: `uv run pytest tests/unit/test_econometrics_valuation_contracts.py -q`

Expected: collection fails because `valuation_contracts` does not exist.

- [x] **Step 3: Implement strict specs and shared evidence models**

Use these closed literals and required authorities:

```python
HedonicForm = Literal["level-level", "log-level", "level-log", "log-log"]
CountFamily = Literal["poisson", "negative-binomial"]
BinaryLink = Literal["logit", "probit"]

class WelfareEstimate(BaseModel):
    estimate: float
    std_error: float
    confidence_low: float
    confidence_high: float
    currency: str
    price_base: str
    time_basis: str
    population_basis: str
    transformation: str
```

Require positive/finite thresholds, nonempty distinct column roles, declared
cluster/fixed-effect fields, exact functional form or family, and one registered
sensitivity definition per method.

- [x] **Step 4: Write CSV-shape and result-invariant RED tests**

Cover Hedonic log-domain/support and unique transaction keys; Travel-cost
nonnegative integer visits, positive exposure, site support and offsets; CV exact
binary response and positive bids; DCE unique respondent/set/alternative rows,
at least two alternatives and exactly one chosen alternative per set. Result tests
must reject incoherent confidence intervals, nonfinite covariance, missing
sensitivity rows, nonnegative cost/bid slopes, and welfare values inconsistent
with their registered transformation.

- [x] **Step 5: Implement CSV validation and typed result invariants**

Keep structural input failures in `_valuation_csv.py`; keep independent support
reconstruction functions in `_valuation_support.py`. Return stable codes:
`HEDONIC_DATA_INVALID`, `TRAVEL_COST_DATA_INVALID`, `CV_DATA_INVALID`, and
`DCE_CHOICE_SET_INVALID`.

- [x] **Step 6: Register the four variants and run focused gates**

Run:

```bash
uv run pytest tests/unit/test_econometrics_valuation_contracts.py \
  tests/unit/test_econometrics_valuation_data.py \
  tests/unit/test_econometrics_valuation_results.py \
  tests/unit/test_econometrics_causal_contracts.py -q
uv run ruff check src/envresearch/econometrics tests/unit/test_econometrics_valuation_*.py
uv run mypy src/envresearch/econometrics
```

- [x] **Step 7: Commit**

Commit: `feat(econometrics): define valuation core contracts`

---

### Task 2: Hedonic and Travel-cost recipes

**Files:**
- Create: `src/envresearch/econometrics/hedonic.py`
- Create: `src/envresearch/econometrics/travel_cost.py`
- Create: `src/envresearch/econometrics/_valuation_script.py`
- Create: `src/envresearch/econometrics/_valuation_outputs.py`
- Create: `src/envresearch/econometrics/templates/hedonic_pricing.R`
- Create: `src/envresearch/econometrics/templates/travel_cost.R`
- Create: `tests/fixtures/econometrics/hedonic_pricing.csv`
- Create: `tests/fixtures/econometrics/travel_cost.csv`
- Test: `tests/unit/test_econometrics_hedonic.py`
- Test: `tests/unit/test_econometrics_travel_cost.py`
- Test: `tests/integration/test_econometrics_hedonic_travel.py`

**Interfaces:**
- `HedonicRecipe` emits `coefficients.csv`, `implicit_price.csv`,
  `support.csv`, `collinearity.csv`, `sensitivity.csv`,
  `package_configuration.csv`, and `hedonic_plot.svg`.
- `TravelCostRecipe` emits `coefficients.csv`, `consumer_surplus.csv`,
  `support.csv`, `dispersion.csv`, `sensitivity.csv`,
  `package_configuration.csv`, and `travel_cost_plot.svg`.
- Both implement the existing `EconometricsRecipe` protocol and consume the
  Task 1 spec/result types.

- [x] **Step 1: Write renderer/parser RED tests**

```python
def test_hedonic_recipe_has_exact_output_contract(tmp_path: Path) -> None:
    recipe = HedonicRecipe(tmp_path)
    assert recipe.expected_outputs == frozenset({
        "coefficients.csv", "implicit_price.csv", "support.csv",
        "collinearity.csv", "sensitivity.csv", "package_configuration.csv",
        "hedonic_plot.svg",
    })
```

Add the analogous Travel-cost test, exact script digest tests, strict column
schema tests, SVG numeric-axis tests, and malformed/missing output tests.

- [x] **Step 2: Run recipe tests and confirm RED**

Run: `uv run pytest tests/unit/test_econometrics_hedonic.py tests/unit/test_econometrics_travel_cost.py -q`

Expected: collection fails because both recipe modules are absent.

- [x] **Step 3: Implement Hedonic generated R and parser**

Render only declared column names through the existing safe R literal helper.
Apply the registered level/log transformations, fixed effects and robust/clustered
inference with `fixest::feols`. Emit the environmental coefficient, transformed
marginal implicit price, VIF/condition evidence, support, registered sensitivity,
configuration, and a deterministic dynamic-height coefficient SVG. Raise
`HEDONIC_LOG_DOMAIN_INVALID`, `HEDONIC_TERM_UNIDENTIFIED`,
`HEDONIC_COLLINEARITY_EXCEEDED`, or `HEDONIC_SENSITIVITY_EXCEEDED` exactly.

- [x] **Step 4: Implement Travel-cost generated R and parser**

Use registered Poisson via `fixest::fepois` or Negative Binomial via
`MASS::glm.nb`, always with the declared log exposure offset. Emit cost slope,
dispersion, zero share, fit evidence, consumer surplus `-1 / beta_cost` in bound
units, registered substitute-site sensitivity, configuration, and numeric-axis
SVG. Raise `TRAVEL_COST_OFFSET_INVALID`, `TRAVEL_COST_SLOPE_INVALID`, or
`TRAVEL_COST_DISPERSION_EXCEEDED` exactly; never switch families automatically.

- [x] **Step 5: Add real frozen-runtime integration tests**

Run both repository fixtures through `TrustedLocalRBackend` and
`LocalAnalysisService`, assert `status == "passed"`, exact frozen authority refs,
and independently reopened outputs. Add one failure fixture for each registered
scientific code and one output-byte mutation rejection.

- [x] **Step 6: Run focused gates and commit**

```bash
uv run pytest tests/unit/test_econometrics_hedonic.py \
  tests/unit/test_econometrics_travel_cost.py \
  tests/integration/test_econometrics_hedonic_travel.py -q
uv run ruff check src/envresearch/econometrics tests/unit/test_econometrics_hedonic.py \
  tests/unit/test_econometrics_travel_cost.py tests/integration/test_econometrics_hedonic_travel.py
uv run mypy src/envresearch/econometrics
```

Commit: `feat(econometrics): add hedonic and travel cost recipes`

---

### Task 3: Contingent Valuation and DCE recipes

**Files:**
- Create: `src/envresearch/econometrics/contingent_valuation.py`
- Create: `src/envresearch/econometrics/discrete_choice.py`
- Create: `src/envresearch/econometrics/templates/contingent_valuation.R`
- Create: `src/envresearch/econometrics/templates/dce_clogit.R`
- Create: `tests/fixtures/econometrics/contingent_valuation.csv`
- Create: `tests/fixtures/econometrics/dce_clogit.csv`
- Test: `tests/unit/test_econometrics_contingent_valuation.py`
- Test: `tests/unit/test_econometrics_discrete_choice.py`
- Test: `tests/integration/test_econometrics_cv_dce.py`

**Interfaces:**
- `ContingentValuationRecipe` emits `coefficients.csv`, `wtp.csv`,
  `bid_support.csv`, `probabilities.csv`, `sensitivity.csv`,
  `package_configuration.csv`, and `cv_plot.svg`.
- `DiscreteChoiceRecipe` emits `coefficients.csv`, `covariance.csv`, `wtp.csv`,
  `choice_support.csv`, `sensitivity.csv`, `package_configuration.csv`, and
  `dce_plot.svg`.

- [x] **Step 1: Write renderer/parser and choice-authority RED tests**

Assert strict output sets, escaped identifiers, exact script digest,
configuration binding, numeric SVG axes, one-choice-per-set enforcement, and
rejection of source-dependent prose or arbitrary formulas.

- [x] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/unit/test_econometrics_contingent_valuation.py tests/unit/test_econometrics_discrete_choice.py -q`

Expected: collection fails because the two recipe modules are absent.

- [x] **Step 3: Implement single-bounded CV**

Fit only the preregistered logit or probit with base R `glm`. Emit bid coverage,
yes-share by bid, fitted probability range, coefficients, covariance-based WTP
interval, mean/median WTP where defined, covariate sensitivity, and deterministic
SVG. Raise `CV_BID_SLOPE_INVALID`, `CV_MONOTONICITY_FAILED`,
`CV_SEPARATION_DETECTED`, or `CV_WTP_UNIDENTIFIED` exactly.

- [x] **Step 4: Implement long-format conditional-logit DCE**

Fit `survival::clogit(chosen ~ attributes + cost + strata(choice_set_id))` with
respondent-clustered inference. Emit support, coefficients, covariance, each
attribute WTP ratio `-beta_attribute / beta_cost`, delta-method uncertainty,
registered ASC sensitivity, and deterministic SVG. Raise
`DCE_CHOICE_SET_INVALID`, `DCE_TERM_UNIDENTIFIED`, or `DCE_COST_SLOPE_INVALID`
exactly.

- [x] **Step 5: Add authenticated integration and adversarial tests**

Exercise both green fixtures through the real frozen runtime. Add CV separation
and nonmonotone-bid fixtures; add incomplete/multiple-choice and near-zero-cost DCE
fixtures. Verify all failures publish typed exception reports and never create a
PASSED result.

Implementation note: the existing Task 1 valuation data-gate suite supplies the
incomplete/multiple-choice adversarial rows, while Task 3 adds exact
template-bound CV/DCE scientific-code tests and real frozen-R green fixtures.
This keeps one shared fixture/data-gate layer instead of duplicating invalid CSVs.

- [x] **Step 6: Run focused gates and commit**

```bash
uv run pytest tests/unit/test_econometrics_contingent_valuation.py \
  tests/unit/test_econometrics_discrete_choice.py \
  tests/integration/test_econometrics_cv_dce.py -q
uv run ruff check src/envresearch/econometrics tests/unit/test_econometrics_contingent_valuation.py \
  tests/unit/test_econometrics_discrete_choice.py tests/integration/test_econometrics_cv_dce.py
uv run mypy src/envresearch/econometrics
```

Commit: `feat(econometrics): add stated preference recipes`

---

### Task 4: Independent support and welfare reconstruction

**Files:**
- Modify: `src/envresearch/econometrics/_valuation_support.py`
- Create: `src/envresearch/econometrics/_valuation_welfare.py`
- Modify: `src/envresearch/econometrics/verify.py`
- Test: `tests/unit/test_econometrics_valuation_support.py`
- Test: `tests/integration/test_econometrics_valuation_verifier.py`

**Interfaces:**
- Produces `valuation_result_matches_snapshot(spec, result, snapshot_bytes) -> bool`.
- Produces `reconstruct_welfare(spec, result) -> tuple[WelfareEstimate, ...]`.
- Extends `LocalAnalysisVerifier.verify()` without trusting result-level success,
  support totals, configuration, covariance, sensitivity, or welfare fields.

- [x] **Step 1: Write forged-evidence RED tests**

For each method, start from one legitimate PASSED report and reseal exactly one
forgery: support count, coefficient, covariance, transformation, currency base,
WTP/consumer-surplus value, sensitivity row, configuration method ID, script byte,
or package authority. Assert `service.status()` rejects every forged report.

- [x] **Step 2: Run verifier tests and confirm RED**

Run: `uv run pytest tests/unit/test_econometrics_valuation_support.py tests/integration/test_econometrics_valuation_verifier.py -q`

Expected: forged valuation evidence remains accepted because reconstruction is
not yet registered.

- [x] **Step 3: Implement snapshot support reconstruction**

Reparse the authenticated CSV bytes independently. Recompute Hedonic valid/log
support and fixed-effect levels; Travel-cost visits/exposure/site/zero counts; CV
bid/response support; and DCE respondent/set/alternative/chosen counts. Compare
every typed support field exactly.

- [x] **Step 4: Implement welfare and configuration reconstruction**

Recompute implicit price, consumer surplus, CV WTP, and DCE WTP ratios from
coefficient/covariance output rows using the registered formula and delta method.
Compare finite estimates and intervals at declared numeric tolerance. Bind exact
method ID, functional form/family/link, units, fixed effects, clusters, R version,
and package authorities.

- [x] **Step 5: Register verifier branches and run compatibility gates**

```bash
uv run pytest tests/unit/test_econometrics_valuation_support.py \
  tests/integration/test_econometrics_valuation_verifier.py \
  tests/integration/test_econometrics_recovery.py \
  tests/integration/test_econometrics_exit_security.py -q
uv run ruff check src/envresearch/econometrics tests/unit/test_econometrics_valuation_support.py \
  tests/integration/test_econometrics_valuation_verifier.py
uv run mypy src/envresearch/econometrics
```

- [x] **Step 6: Commit**

Commit: `fix(econometrics): reconstruct valuation evidence`

---

### Task 5: Compact nine-case Valuation exit

**Files:**
- Create: `src/envresearch/econometrics/valuation_exit_models.py`
- Create: `src/envresearch/econometrics/valuation_exit_corpus.py`
- Create: `src/envresearch/econometrics/valuation_exit_runner.py`
- Modify: `src/envresearch/econometrics/exit_runner.py`
- Modify: `src/envresearch/econometrics/exit_evaluator.py`
- Modify: `src/envresearch/econometrics/cli.py`
- Create: `benchmarks/econometrics/valuation-core/manifest.yaml`
- Create: `benchmarks/econometrics/valuation-core/runner/green-hedonic.yaml`
- Create: `benchmarks/econometrics/valuation-core/runner/green-travel-cost.yaml`
- Create: `benchmarks/econometrics/valuation-core/runner/green-cv.yaml`
- Create: `benchmarks/econometrics/valuation-core/runner/green-dce.yaml`
- Create: `benchmarks/econometrics/valuation-core/runner/fail-hedonic-sensitivity.yaml`
- Create: `benchmarks/econometrics/valuation-core/runner/fail-travel-dispersion.yaml`
- Create: `benchmarks/econometrics/valuation-core/runner/fail-cv-wtp.yaml`
- Create: `benchmarks/econometrics/valuation-core/runner/fail-dce-choice-set.yaml`
- Create: `benchmarks/econometrics/valuation-core/runner/integrity-output-tamper.yaml`
- Create: `benchmarks/econometrics/valuation-core/runner/data/hedonic.csv`
- Create: `benchmarks/econometrics/valuation-core/runner/data/travel-cost.csv`
- Create: `benchmarks/econometrics/valuation-core/runner/data/cv.csv`
- Create: `benchmarks/econometrics/valuation-core/runner/data/dce.csv`
- Create: `benchmarks/econometrics/valuation-core/evaluator/expectations.json`
- Create: `benchmarks/econometrics/valuation-core/evaluator/failures.json`
- Test: `tests/unit/test_econometrics_valuation_exit_models.py`
- Test: `tests/integration/test_econometrics_valuation_exit.py`
- Test: `tests/integration/test_econometrics_valuation_exit_security.py`

**Interfaces:**
- Produces `ValuationExitManifest`, `ValuationExitRun`, and
  `ValuationExitReport` with exactly nine unique cases.
- Reuses `ExitRegistry`, `RegistryAnalysisExecutor`, exact case/data refs,
  expectation separation, current authenticated bindings, and output mutation
  machinery. Existing V0.3 16-case schemas remain unchanged.
- Adds CLI commands `valuation-exit-run`, `valuation-exit-evaluate`, and
  `valuation-exit-status` using exact reference arguments.

- [x] **Step 1: Write exact-matrix RED tests**

```python
def test_valuation_manifest_requires_exact_nine_case_matrix() -> None:
    manifest = load_checked_manifest()
    assert len(manifest.cases) == 9
    assert Counter(case.role for case in manifest.cases) == {
        "green": 4, "scientific-failure": 4, "integrity-failure": 1
    }
    assert {case.family for case in manifest.cases if case.role == "green"} == {
        "hedonic-pricing", "travel-cost", "contingent-valuation", "dce-clogit"
    }
```

Also reject duplicate IDs/refs, symlinked corpus components, mutable data,
expectation leakage, wrong case family, stale analysis bindings, forged current
reports, and evaluator-before-run.

- [x] **Step 2: Run exit tests and confirm RED**

Run: `uv run pytest tests/unit/test_econometrics_valuation_exit_models.py tests/integration/test_econometrics_valuation_exit.py -q`

Expected: collection fails because the valuation exit models are absent.

- [x] **Step 3: Implement schemas and reuse the generic execution seams**

Keep V0.3 schemas frozen. Add valuation-specific exact cardinality validators and
adapt the existing registry/executor/evaluator helpers to accept a manifest/report
protocol rather than copying persistence, locking, or authentication code.

- [x] **Step 4: Add the checked local corpus**

Create four green fixture cases, one method-specific scientific failure per
family, and one post-run output-byte mutation. Freeze data bytes into runner
objects and expectations into the separate evaluator store. No case descriptor or
runtime projection may expose its expectation.

- [x] **Step 5: Add exact-reference CLI and full security tests**

Assert JSON output, stable exit codes, resumable same-run behavior, no latest
lookup, stale-revision rejection, mutation detection, and an all-or-nothing
`passed` report only for 9/9 matched outcomes.

- [x] **Step 6: Run focused gates and commit**

```bash
uv run pytest tests/unit/test_econometrics_valuation_exit_models.py \
  tests/integration/test_econometrics_valuation_exit.py \
  tests/integration/test_econometrics_valuation_exit_security.py \
  tests/integration/test_econometrics_exit_runner.py \
  tests/integration/test_econometrics_exit_security.py -q
uv run ruff check src/envresearch/econometrics tests/unit/test_econometrics_valuation_exit_models.py \
  tests/integration/test_econometrics_valuation_exit*.py
uv run mypy src/envresearch/econometrics
```

Commit: `feat(econometrics): add valuation core exit`

---

### Task 6: Formal V0.3.1 exit and V0.4 handoff

**Files:**
- Modify: `docs/econometrics-v03-operator-guide.md`
- Modify: `.claude/project-memory/environmental-research-os.md`
- Create: `docs/superpowers/specs/2026-08-12-v04-paper-builder-design.md`
- Create: `.superpowers/sdd/2026-08-12-v03-valuation-core/final-report.md`
- Test: `tests/integration/test_econometrics_valuation_acceptance.py`

**Interfaces:**
- Produces one immutable, current, independently reconstructed nine-case PASSED
  reference and records its exact hashes in the final report.
- Freezes the post-V0.3.1 extension boundary and hands V0.4 a stable
  `LocalAnalysisReport`/artifact-reference input contract; it does not implement
  Paper Builder in this task.

- [x] **Step 1: Write the formal acceptance RED test**

```python
def test_v031_exit_is_current_complete_and_v04_ready(exit_harness: Harness) -> None:
    report = exit_harness.run_and_evaluate()
    assert report.status == "passed"
    assert len(report.outcomes) == 9
    assert all(item.status == "matched" for item in report.outcomes)
    assert exit_harness.extension_registry.is_frozen
```

Add explicit assertions that no Spatial/Exposure/Forecasting/Wave-3/Stata method
is executable and that all four reserved extension registrations remain absent or
capability-gated.

- [x] **Step 2: Run the acceptance test and confirm RED**

Run: `uv run pytest tests/integration/test_econometrics_valuation_acceptance.py -q`

Expected: FAIL until the real frozen runtime report and transition marker exist.

- [x] **Step 3: Execute the real local frozen-runtime matrix**

Run the four green recipes with the authenticated frozen R pack, execute the four
scientific failures and one integrity mutation, then evaluate the sealed run.
Record exact manifest, run, catalog, report, runtime, and package hashes. Do not
download data or packages.

- [x] **Step 4: Run the complete verification matrix**

```bash
uv sync --locked --dev
uv run pytest tests/unit tests/integration -q
uv run pytest tests/integration/test_econometrics_valuation_acceptance.py -q
uv run ruff check .
uv run mypy src
uv lock --check
git diff --check
```

Also verify all changed Python/R source and test files are at or below 400 lines,
the full suite does not lower the recorded coverage baseline, and no downloaded
payload appears in the repository.

- [x] **Step 5: Request independent code review and close findings**

Review exact changed files plus adversarial runtime, package-authority, snapshot,
welfare, recovery, concurrency, corpus-separation, and current-report paths. Any
Critical or Important finding blocks completion and requires RED-GREEN repair and
fresh affected/full gates.

- [x] **Step 6: Write the final evidence report and V0.4 handoff design**

Record commands, counts, hashes, skipped-test reasons, review verdict, extension
boundary, and the next input contract. The V0.4 design must consume accepted
artifact refs and build claim-evidence/argument/writing/audit stages; it must not
reopen method expansion or invent V0.5-V0.9 milestones.

- [x] **Step 7: Commit**

Commit: `feat(econometrics): complete valuation core`
