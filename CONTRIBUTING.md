# Contributing

Thank you for improving Environmental Research OS. The current public target is
a useful Personal Pilot, not a claim of completed scientific validation.

## Set up the repository

```bash
uv sync --locked --dev
uv run python scripts/preflight.py
```

Create a focused branch, make the smallest coherent change, and add tests for
behavioral changes. Python code follows PEP 8, uses type hints, and is checked by
Ruff and mypy.

## Before opening a pull request

```bash
uv run ruff check .
uv run mypy src
uv run pytest --cov=envresearch --cov-report=term-missing --cov-fail-under=80
uv lock --check
git diff --check
```

Use a Conventional Commit such as `fix(econometrics): preserve diagnostic
warnings` or `docs(workflow): clarify local-data setup`.

## Research and data rules

- Add only synthetic fixtures that you created or that the repository is
  explicitly licensed to redistribute.
- Do not commit real research data, private papers, API keys, local run roots,
  personal paths, or Obsidian/project-memory content.
- Do not weaken evidence lineage, immutable-reference checks, independent
  review boundaries, or explicit external-access approval merely to make a test
  pass.
- Separate engineering verification from scientific validity. A green test
  suite does not by itself change `scientific_release_pending`.
- Document the estimand, data shape, assumptions, diagnostics, and limitations
  when adding or changing a method capability.

## Pull-request description

Explain the user-visible outcome, affected modules, tests run, data/license
impact, and any remaining limitation. Avoid including private run output.
