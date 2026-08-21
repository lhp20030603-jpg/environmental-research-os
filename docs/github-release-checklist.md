# GitHub Public Release Checklist

## Repository contents

- [ ] README reflects the Personal Pilot feature boundary.
- [ ] LICENSE, SECURITY.md, CONTRIBUTING.md, CITATION.cff, and CHANGELOG.md are
  present.
- [ ] No credentials, `.env` files, personal paths, private knowledge-base
  content, real data, local runs, or generated artifacts are tracked.
- [ ] Every tracked binary/archive is documented and redistributable.
- [ ] Test data is synthetic or explicitly licensed for redistribution.

## Verification

```bash
uv sync --locked --dev
uv run ruff check .
uv run mypy src
uv run pytest --cov=envresearch --cov-report=term-missing --cov-fail-under=80
uv lock --check
uv run python scripts/publication_audit.py
git diff --check
git ls-files
```

Record the exact commit and results in the GitHub release notes. Do not convert
an expected environment-gated skip into a PASS. The complete authority/recovery
suite is intentionally allowed up to 90 minutes in CI; a timeout is a failed
gate, not a scientific or engineering PASS.

## GitHub setup

Current public setup:

- repository: <https://github.com/lhp20030603-jpg/environmental-research-os>;
- visibility: public;
- default branch: `main`;
- license: MIT;
- first public tag: `v1-personal-pilot-public-2026-08-21`.

The development history previously tracked machine-local project-memory paths.
Deleting them from the current tree does not remove them from old commits.
Therefore, do not push the development branch or its full history to the public
remote. Create a single-commit orphan release branch from the final verified
tree (or perform an independently verified history rewrite), and run the
publication audit again on that branch.

The empty public repository has been created and the sanitized release branch
has been pushed as `main`. Wait for CI and inspect the GitHub file list before
creating the public tag and GitHub Release. Never push the private development
branch or the separate paper-project repository.

## Release description

Include installation commands, supported Python versions, optional R/runtime
requirements, verification results, known limitations, and the statement:

> Environmental Research OS is a Personal Pilot / Research Prototype. Formal
> scientific release remains pending.
