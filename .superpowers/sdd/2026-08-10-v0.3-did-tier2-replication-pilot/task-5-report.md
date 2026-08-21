# Task 5 Report: R-first DiD/event-study adapter

## Delivered

- Added immutable, validated author-reproduction and derived DiD plan inputs.
- Kept author execution in `author-reproduction` and derived diagnostics in
  `derived-did-event-study`; their commands and generated output locations are
  distinct.
- Restricted author scripts and derived analysis data to acquired-inventory
  members. Author output declarations require a comparator and finite,
  non-negative tolerances through the frozen intake contract.
- Generated R uses identifier-only validated columns and JSON-quoted literals.
  Its machine-readable `derived-did-event-study-v1` report includes treatment
  timing, support, balance, event-time, TWFE estimates and confidence
  intervals, configuration, and a reasoned Callaway-Sant'Anna unsupported
  state when cohort support is absent.
- Parsed derived reports reject any reproduction-result field; the typed report
  has `reproduction_result: None`.

## Verification

- RED: `uv run pytest tests/unit/test_replication_did_r.py -v` failed because
  `envresearch.replication.did_r` did not exist.
- GREEN: `uv run pytest tests/unit/test_replication_did_r.py -v` passed
  (`5 passed`).
- Static: `uv run ruff check src/envresearch/replication/did_r.py
  tests/unit/test_replication_did_r.py` passed.
- Adjacent regression: `uv run pytest tests/unit/test_replication_did_r.py
  tests/unit/test_replication_container.py tests/unit/test_replication_contracts.py -v`
  passed (`88 passed`).
- `git diff --check` passed.

## Scope and concerns

- No R, Docker, Podman, network, or external packages were invoked.
- This slice plans and parses the R workflow only. Running the generated plan,
  persisting derived evidence, and independently verifying it belong to later
  orchestration tasks.

## Review fix round 1

- Verified RED before implementation: six focused failures covered the missing
  approval-bound adapter constructor, generated-script materialization,
  namespace-specific output roots, report-directory creation, and completed
  Callaway-Sant'Anna evidence fields.
- Generated scripts are now materialized idempotently beneath the mounted
  `/input/.generated` directory by the container plan boundary; the plan to
  Docker argv path is covered directly.
- The adapter consumes a resolved `ApprovedTier2Intake` plus its typed
  `ArtifactRef`, and rejects inventories bound to a different approval.
- Author and derived output roots are allocated as disjoint validated children
  of the trusted run root. No untrusted directory is created during allocation.
- The derived script creates the output directory before writing JSON. Completed
  Callaway-Sant'Anna results now require estimates, confidence intervals, and
  configuration; both unsupported and completed expected fixtures are parsed.
- Defensive author-output tolerance checks now reject non-finite forged values.

### Review verification

- `uv run pytest tests/unit/test_replication_did_r.py
  tests/unit/test_replication_container.py tests/unit/test_replication_contracts.py -v`
  passed (`93 passed`).
- Ruff format/check, mypy over the two implementation modules, and
  `git diff --check` passed.

## Review fix round 2

- The adapter now accepts a sealed `ResearchArtifact[ApprovedTier2Intake]`,
  verifies its content hash, derives its `ArtifactRef`, and requires the
  acquired inventory to bind to that exact derived reference before planning.
  Tests reject unsealed, tampered, and mismatched approval evidence.
- Completed Callaway-Sant'Anna output uses frozen typed group-time estimate and
  confidence-interval models with finite numeric values, ordered bounds, and
  matching group-time keys. Its required configuration is limited to the fixed
  `did::att_gt` / `nevertreated` contract; empty and null configuration are
  rejected.
- The completed expected fixture now contains typed group/time evidence and the
  generated R output emits group/time values for its confidence intervals.

### Review verification

- RED: `uv run pytest tests/unit/test_replication_did_r.py -v` exposed the
  previous unsealed-artifact constructor and permissive C&S structures.
- GREEN: relevant adapter/container/contracts suites passed (`100 passed`).
- Ruff format/check, mypy over `container.py` and `did_r.py`, and
  `git diff --check` passed.

## Review fix round 3

- Completed Callaway-Sant'Anna evidence now rejects duplicate `(group, time)`
  estimates or intervals, requires equal list cardinalities, and requires equal
  group-time key sets before accepting the report.
- RED regressions covered duplicated estimate and duplicated interval cases.

### Review verification

- Relevant adapter/container/contracts suites passed (`102 passed`).
- Ruff format/check, mypy over the implementation modules, and
  `git diff --check` passed.
