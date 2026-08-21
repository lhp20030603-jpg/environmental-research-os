# Trusted local econometrics

This is the shortest supported path from a local CSV to an auditable causal-policy result. The same kernel supports DiD/event study, Panel FE, IV/2SLS, and sharp local-linear RDD. It does not download data, install R packages, inspect arbitrary scripts, or use the external replication runtime.

## What is ready

- One strict discriminated analysis spec selects a local UTF-8 CSV and declares the selected method's columns and design.
- The source is read without following symlinks, validated, and copied to an immutable content-addressed snapshot. The original file is never modified.
- The repository generates the R script. User-supplied R code is not accepted.
- DiD emits TWFE event-study and Callaway--Sant'Anna estimates. Panel FE emits declared coefficients, within-fit evidence, and a numeric-axis coefficient plot. IV/2SLS emits structural, exact joint first-stage, reduced-form, weak-instrument, optional Sargan overidentification, and numeric-axis plot evidence. RDD emits the cutoff estimate, exact 0.5/1.0/1.5 bandwidth sensitivity, donut and covariate checks, two-sided support, and an explicit non-`rdrobust` limitation.
- A PASSED report is independently reopened and checked against the snapshot, registered script, exact R executable, logs, outputs, configuration, and typed result. Panel unit/time/cluster counts, IV clusters, and every RDD window/side/unique-running count are independently reconstructed from the authenticated CSV bytes.

The current Mac has already passed real local smoke with `Rscript 4.4.3`, `fixest 0.14.0`, and `did 2.3.0`. Only DiD needs `did`; the three-method causal bundle adds no R package. Research OS never installs packages automatically.

## Registered method IDs

| Method | `method_id` | Required design evidence |
|---|---|---|
| DiD/event study | `did-event-study` | unit, time, outcome, treatment cohort, comparison group |
| Panel FE | `panel-fe` | unit, time, outcome, regressors, fixed effects |
| IV/2SLS | `iv-2sls` | outcome, endogenous variables, instruments, controls/FE, weak-F threshold |
| Sharp local-linear RDD | `rdd-local-linear` | outcome, running variable, cutoff, triangular bandwidth, donut radius |

## 1. Prepare a local CSV

Keep one row per unit-period. A minimal staggered-adoption file needs:

- a unit identifier;
- a numeric time variable;
- a numeric outcome;
- first-treatment cohort, left empty for never-treated units;
- any declared covariates.

The validator rejects duplicate unit-time keys, missing declared columns, non-finite numeric fields, links/devices, and files outside the configured workspace budget.

## 2. Write the analysis spec

```yaml
schema_version: econometrics.local-analysis.v1
method_id: did-event-study
data_path: /absolute/path/to/panel.csv
columns:
  unit: unit_id
  time: year
  outcome: emissions
  treatment_cohort: first_policy_year
  covariates: [temperature, income]
comparison_group: never-treated
reference_period: -1
inference:
  confidence_level: 0.95
  cluster_column: unit_id
  interval_mode: simultaneous
  bootstrap_seed: 20260811
budget:
  inactivity_seconds: 120
  max_output_bytes: 10000000
  max_workspace_bytes: 1000000000
```

Use an absolute `data_path`. `validate` is read-only:

```bash
uv run envresearch econometrics validate analysis.yaml --json
```

## 3. Bind the local R executable

Record the exact executable and digest that will be used:

```bash
R_BIN="$(python3 -c 'import os, shutil; p=shutil.which("Rscript"); print(os.path.realpath(p) if p else "")')"
test -n "$R_BIN" || { echo "Rscript is not installed" >&2; exit 1; }
R_SHA="$(shasum -a 256 "$R_BIN" | awk '{print $1}')"
```

If a required package is absent, the run ends non-green. Install or update packages outside Research OS under your normal R administration process, then rerun with the newly reviewed executable identity. No data is downloaded by a method package.

## 4. Run and retain the exact reference

```bash
uv run envresearch econometrics run analysis.yaml \
  --run-root /absolute/path/to/local-analysis-store \
  --r-executable "$R_BIN" \
  --r-sha256 "$R_SHA" \
  --json > run-result.json
```

Save the `reference` object from `run-result.json` as `analysis-reference.json`. Status never searches for “latest”; it accepts that exact immutable reference:

```bash
uv run envresearch econometrics status analysis-reference.json \
  --run-root /absolute/path/to/local-analysis-store \
  --frozen-r-pack-root /absolute/path/to/reviewed-pack \
  --frozen-r-pack-hash "$FROZEN_PACK_SHA" \
  --json
```

For methods with non-base R dependencies, `status` reopens the exact reviewed
frozen pack. Omitting or changing that external authority makes verification
fail closed; a package name recorded inside the result is not sufficient.

Exit codes are stable: `0` for valid/PASSED or read-only status, `1` for a durable analysis exception, and `2` for malformed authority, schema, root, reference, or tampered evidence.

## Expansion rule

The governing order remains Section 13 of `plan/2026-08-04-环境经济学与政策研究操作系统-设计规范.md`. We will not create one infrastructure project or one dataset download per method.

Panel FE + IV/2SLS + RDD now share the same local-data/runtime/evidence kernel. Later methods remain grouped by reusable data shape and diagnostics. Method packs declare estimands, compatible schemas, assumptions, estimators, diagnostics, and outputs; separately governed data snapshots satisfy those schemas and may serve several methods.

Third-wave structural models remain deferred until calibration, solver validation, and sensitivity-analysis infrastructure is strong enough.
