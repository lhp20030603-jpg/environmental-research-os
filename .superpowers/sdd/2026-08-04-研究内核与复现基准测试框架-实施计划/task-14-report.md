# Task 14 Implementation Report

## Outcome

Added the v0.1 quality gate after the two-tier benchmark catalog. The gate
ships deterministic reusable pytest fixtures, end-to-end seeded failure
injection tests, an Ubuntu CI workflow, and user-facing architecture and
security documentation. No production source change was required: the CLI and
benchmark runner already emitted the required stable Finding codes.

## TDD trace

The new integration tests were written before `tests/conftest.py`. The
production change each test protects is a missing or incorrect boundary mapping
between a benchmark failure and its Finding code/severity.

RED:

```text
uv run pytest tests/integration/test_seeded_failures.py -v
collected 4 items
4 errors
fixture 'invalid_schema_manifest_path' not found
fixture 'command_failure_benchmark' not found
fixture 'missing_doi_manifest_path' not found
fixture 'hash_mismatch_benchmark' not found
```

GREEN:

```text
uv run pytest tests/integration/test_seeded_failures.py -v
4 passed in 0.39s
```

The fixtures deliberately create only repository-owned synthetic paths and
files. `missing_doi_manifest_path` is otherwise complete public metadata;
`hash_mismatch_benchmark` hashes a synthetic raw file against an intentionally
wrong manifest checksum; `command_failure_benchmark` invokes the trusted
`python` token with a non-zero exit; and `mismatch_benchmark` is available for
output-comparison coverage. `interrupted_workspace` reserves a dedicated clean
workspace for recovery tests without embedding test-only behavior in production
classes.

## Architecture and security decisions

- The Task 8 command runner remains a trusted local package runner, not a
  hostile-code sandbox. CI and docs retain the explicit external-isolation
  boundary for untrusted packages.
- Commands remain argv-only with `shell=False`, an allowlisted pinned
  executable, safe relative paths, and restricted command environments.
- Raw sources are verified and copied only into derived workspaces; raw trees
  are immutable, and run roots cannot be placed below them.
- CI runs only repository-owned synthetic fixtures. Real published-paper
  catalog entries remain metadata-only and `public: false`; no datasets,
  ICPSR material, credentials, or external replication packages are downloaded
  or executed in ordinary PR CI.
- The security model states the secrets, license, and third-party distribution
  boundaries alongside the gate independence rule.

## Verification

```text
uv sync --locked --dev
Resolved 32 packages in 27ms
Audited 30 packages in 6ms

uv lock --check
Resolved 32 packages in 5ms

uv run ruff check .
All checks passed!

uv run mypy src
Success: no issues found in 34 source files

uv run pytest -v
279 passed in 12.33s

uv run pytest --cov=envresearch --cov-report=term-missing --cov-fail-under=80
279 passed in 13.25s
Required test coverage of 80% reached. Total coverage: 91.26%

uv run envresearch benchmark validate benchmarks/fixtures/exact-file/benchmark.yaml
VALID: synthetic-exact-file

uv run envresearch benchmark list --catalog benchmarks/catalog
3 metadata-only catalog entries listed, all Public=no

uv run envresearch benchmark run benchmarks/fixtures/exact-file/benchmark.yaml --case-root benchmarks/fixtures/exact-file --run-root runs/final-smoke
final-smoke / synthetic-exact-file / PASSED / no findings

uv run envresearch run status runs/final-smoke --json
status=passed; completed_tasks=[command-0001, benchmark-finalize];
output_comparisons[0].status=matched

git diff --check
exit 0
```

## Files changed

- `tests/conftest.py`
- `tests/integration/test_seeded_failures.py`
- `.github/workflows/ci.yml`
- `docs/architecture.md`
- `docs/security-model.md`
- `README.md`

All changed Task 14 source, test, documentation, and workflow files are below
400 lines.

## Self-review

The integration tests assert Finding codes (and CRITICAL severity for raw hash
mismatch) rather than only exit statuses. Fixture manifests contain no real
datasets or secrets. The CI recipe uses the lockfile and exactly the mandated
Ruff, mypy, and coverage gates. Documentation does not overclaim sandboxing or
paper-level reproducibility. No Task 14 defects found.

## Fix Round 1: environment secrecy and action pins

### TDD trace

RED added two boundary contracts before the production changes:

1. `CommandSpec` must default-deny arbitrary and secret-shaped environment
   names without echoing values in its validation output.
2. `CommandRunner` must recheck a forged `CommandSpec` before process creation,
   rejecting `API_KEY`, `TOKEN`, and `*_PASSWORD` case-insensitively without
   exposing a value or allowing its child command to touch a marker file.

```text
uv run pytest tests/unit/test_command_runner.py tests/unit/test_benchmark_models.py -v
9 failed, 56 passed in 3.11s

Failures: six secret environment runner cases did not raise ValueError; three
manifest default-deny cases did not raise ValidationError.
```

GREEN after adding the five-name (`LANG`, `LC_ALL`, `TZ`,
`SOURCE_DATE_EPOCH`, `PYTHONHASHSEED`) allowlist to `CommandSpec` and reusing
the same no-value validator in `CommandRunner`:

```text
uv run pytest tests/unit/test_command_runner.py tests/unit/test_benchmark_models.py -v
65 passed in 2.65s

uv run ruff check src/envresearch/models/benchmark.py src/envresearch/runner/command.py tests/unit/test_command_runner.py tests/unit/test_benchmark_models.py
All checks passed!

uv run mypy src
Success: no issues found in 34 source files
```

### Fix Round 1 final verification after remediation

```text
uv lock --check
Resolved 32 packages in 29ms

uv run ruff check .
All checks passed!

uv run mypy src
Success: no issues found in 34 source files

uv run pytest --cov=envresearch --cov-report=term-missing --cov-fail-under=80
288 passed in 21.99s
Required test coverage of 80% reached. Total coverage: 91.30%

git diff --check
exit 0
```

Final self-review: `ValidationError.from_exception_data` receives `input: None`
only after the shared validator has rejected a name; it therefore retains the
field location/message contract without retaining any rejected environment
value. The direct runner still raises its own value-free `ValueError` before
`safe_join` or `Popen`. The change does not broaden command execution,
environment inheritance, fixture scope, or the published-paper catalog policy.

`CommandSpec` also sets Pydantic's `hide_input_in_errors` option. This prevents
a rejected manifest environment value from being rendered in its exception
text; runner errors name only the disallowed key.

### Action pin resolution

The mutable major tags were resolved against their official GitHub repositories
and pinned to immutable full commits with adjacent release comments:

- `actions/checkout@v5` ->
  `08c6903cd8c0fde910a37f88322edcfb5dd907a8` (`v5.0.0`), reviewed at
  `https://github.com/actions/checkout/commit/08c6903cd8c0fde910a37f88322edcfb5dd907a8`.
- `astral-sh/setup-uv@v7` ->
  `37802adc94f370d6bfd71619e3f0bf239e1f3b78` (`v7.6.0`), reviewed at
  `https://github.com/astral-sh/setup-uv/commit/37802adc94f370d6bfd71619e3f0bf239e1f3b78`.

The workflow has no new source-text assertion. Its syntax was checked through
the repository's YAML parser:

```text
uv run python -c "from pathlib import Path; import yaml; assert isinstance(yaml.safe_load(Path('.github/workflows/ci.yml').read_text(encoding='utf-8')), dict); print('ci.yml parsed successfully')"
ci.yml parsed successfully
```

### Final verification

```text
uv lock --check
Resolved 32 packages in 22ms

uv run ruff check .
All checks passed!

uv run mypy src
Success: no issues found in 34 source files

uv run pytest --cov=envresearch --cov-report=term-missing --cov-fail-under=80
288 passed in 17.77s
Required test coverage of 80% reached. Total coverage: 91.29%

git diff --check
exit 0
```

### Fix Round 1 self-review

The manifest and direct runner paths share the exact same allowlist function,
so bypassing Pydantic cannot bypass the pre-process validation. Rejection errors
include the environment key but never its value; tests prove this and prove that
the child marker file remains absent. `TZ` remains an executable safe
environment example, preserving declared deterministic/locale configuration.
The two CI action pins are 40-character immutable commit IDs with reviewed
version comments. No deferred architecture wording was changed.

### Fix Round 1 reviewer remediation

Review found that Pydantic's `hide_input_in_errors` protects the string form but
not the structured `.errors()` or `.json()` representation. A second RED test
extended the manifest rejection contract to those two forms:

```text
uv run pytest tests/unit/test_benchmark_models.py::test_command_spec_default_denies_non_deterministic_environment_names -v
3 failed in 0.13s

Each failure showed the rejected value in ValidationError.errors()[0]["input"].
```

GREEN uses a sanitized nested Pydantic validation error with `input: None`.
The original value is never attached to the structured error:

```text
uv run pytest tests/unit/test_command_runner.py tests/unit/test_benchmark_models.py -q
65 passed in 3.16s

uv run ruff check src/envresearch/models/benchmark.py src/envresearch/runner/command.py tests/unit/test_command_runner.py tests/unit/test_benchmark_models.py
All checks passed!

uv run mypy src
Success: no issues found in 34 source files
```
