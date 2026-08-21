# Environmental Research OS — Personal Pilot

**Release date:** 2026-08-21

**Positioning:** Personal Pilot / Research Prototype

**Product status:** `scientific_release_pending`

## What this release is

This public preview packages the currently usable research workflow for
classmates and collaborators. It can create governed research designs, execute
registered econometric recipes on explicitly supplied local data, build an
evidence-bound paper release, and assemble the exact design and paper release
into a governed Research Factory run.

The status label does not disable personal use. A run may be created, inspected,
revised, and rerun. Problems are surfaced as typed findings so that the user can
correct the inputs or implementation; the system is not globally blocked merely
because an advisory check reports a problem.

## Implemented layers

1. V0.2 Research Design: broad-topic and structured-brief intake, multi-method
   planning, evidence and data feasibility, gates, and recovery.
2. V0.3 Evidence Runner: local CSV execution and verification across eight
   first-wave method families.
3. V0.3.1 Valuation Core: Hedonic, Travel-cost, single-bounded CV, and
   conditional-logit DCE.
4. V0.4 Paper Builder: exact claim-evidence ledger through independently audited
   release candidate.
5. V1 Research Factory: exact design-to-release assembly and independent
   individual-run promotion.
6. Personal Validation foundation: four canonical behaviors and three-role
   Agent advisory review/report generation.

## Deliberately not completed for this preview

The previous long Personal Validation plan contained Tasks 7–10 for owner-approved
repair application, a public CLI/status surface, a larger regression matrix, and
final personal sign-off. They are not required by the four stable public CLI
surfaces and were intentionally stopped to reach a useful release sooner.
Tasks 7–10 are intentionally outside this preview.

Consequently, Personal Validation is an internal API and test harness in this
release. It is not advertised as a stable end-user command. Hidden evaluation,
capability-pack evolution, and replication-package scientific evaluation remain
future, separately governed work.

## Installation

```bash
git clone https://github.com/lhp20030603-jpg/environmental-research-os.git
cd environmental-research-os
uv sync --locked --dev
uv run python scripts/preflight.py
uv run envresearch --help
```

Python 3.11–3.13 is supported. R 4.4.3 and reviewed packages are optional and
needed only for the local econometric execution paths. Docker/Podman is not
installed by the project and is not a default execution path.

The Personal Pilot adds an explicit required `base_period` field to persisted
DiD diagnostics. A pre-preview `econometrics.local-report.v1` created before
commit `3256797` may therefore be unreadable by the current model. Keep the old
code with the old report for audit, or rerun the analysis from its authenticated
input and runtime authority to create a current report; no in-place migration is
claimed in this release.

## Scientific boundary

Engineering tests and repository-owned synthetic fixtures demonstrate software
behavior, recovery, integrity checks, and known-case correctness. They do not
replace a held-out research cohort, independent expert score sheets, or a formal
scientific-release decision. The public repository and any GitHub release must
retain the `scientific_release_pending` statement until those inputs exist.

## Redistribution boundary

The public tree contains source code, tests, documentation, configuration, and
synthetic fixtures. It must not contain personal project memory, local absolute
paths, credentials, real research datasets, unpublished papers, local `runs/`,
or private artifacts. Only the sanitized single-commit public release branch is
approved for upload; the private development history is not. See the repository
security and contribution policies.
