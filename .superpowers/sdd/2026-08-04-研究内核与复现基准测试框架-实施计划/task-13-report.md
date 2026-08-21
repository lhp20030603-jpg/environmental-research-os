# Task 13 Implementation Report

## Historical status before the 2026-08-05 ruling (superseded)

Blocked at the external provenance gate. No catalog data was guessed and no
knowingly failing commit was created.

## TDD trace

1. Added the catalog family/provenance integration contract and the direct
   flat-catalog discovery contract.
2. Confirmed RED: the registry returned no direct catalog entries and all
   three environmental catalog assertions failed.
3. Implemented the minimal registry extension: immediate `*.yaml` files are
   loaded alongside nested `**/benchmark.yaml` packages. Arbitrary nested YAML
   remains ignored; duplicate IDs retain deterministic rejection.
4. Confirmed the focused registry tests pass.
5. Added three provisional non-public manifests containing only official
   DOI/title/V1/source metadata supplied as verified evidence.
6. Confirmed the catalog identity/provenance test passes. The remaining test
   fails only at `all(item.public ...)`, because authenticated license/archive
   evidence is still unavailable.

## Files changed

- `src/envresearch/benchmarks/registry.py`
- `tests/unit/test_benchmark_models.py`
- `tests/integration/test_benchmark_catalog.py`
- `benchmarks/catalog/energy-efficient-stoves-rct.yaml`
- `benchmarks/catalog/clean-identification.yaml`
- `benchmarks/catalog/flood-buyout-hedonic.yaml`
- `docs/benchmark-onboarding.md`

## Verified metadata recorded

- `energy-efficient-stoves-rct`: DOI `10.3886/E166661V1`, official openICPSR
  V1 landing page, supplied verified title.
- `clean-identification`: DOI `10.3886/E192280V1`, official openICPSR landing
  page, supplied verified title.
- `flood-buyout-hedonic`: DOI `10.3886/E189021V1`, official openICPSR V1
  landing page, supplied verified title.

DataCite `rightsList` is empty for these records. The official download/terms
endpoint requires ICPSR authentication. Therefore `license_name`,
`license_url`, `source_archive`, and `source_sha256` remain unset and
`public` remains `false`.

## Commands and current results

```text
uv run pytest <four focused registry tests> -v
4 passed

uv run pytest tests/integration/test_benchmark_catalog.py -v
1 passed, 1 failed (expected provenance gate: public is false)

uv run envresearch benchmark list --catalog benchmarks/catalog --json
3 provisional entries listed successfully
```

## Former remaining completion steps (superseded)

For each official replication package, obtain authenticated evidence for:

1. the displayed license name and official license/terms URL;
2. the official archive filename;
3. the SHA-256 computed from the downloaded, unmodified archive.

Then populate those exact values, set `public: true`, run the focused and full
verification suites, and commit only when all tests are green.

## Human ruling applied (2026-08-05)

The human ruling supersedes the former public-replay exit gate. For v0.1, the
three published-paper records are intentionally metadata-only, non-runnable
catalog references. Each stays `public: false` and contains only the verified
DOI, title, V1 source version, and official source URL. The unverified fields
`source_archive`, `source_sha256`, `license_name`, and `license_url` remain
unset, while `commands` and `expected_outputs` are empty. No license, archive
name, or hash has been guessed.

The runnable v0.1 benchmark surface is the repository-owned synthetic fixtures.
The onboarding guide now requires a real official package only before claiming
paper-level reproducibility, validating adapters against original paper
code/data, or publishing comparative benchmark scores. At that point, the
official package, displayed license, archive name, and SHA-256 must be manually
verified before the catalog item can become public and executable.

## Completion evidence (2026-08-05)

```text
uv run pytest tests/unit/test_benchmark_models.py tests/integration/test_benchmark_catalog.py -v
26 passed in 0.26s

uv run envresearch benchmark list --catalog benchmarks/catalog --json
3 entries listed: clean-identification, energy-efficient-stoves-rct,
flood-buyout-hedonic; every entry has public=false, commands=[],
expected_outputs=[], source_archive=null, source_sha256=null,
license_name=null, and license_url=null.
```

The registry's deterministic union of immediate catalog `*.yaml` files and
nested `benchmark.yaml` package manifests is retained. Tests cover alphabetical
flat discovery, nested discovery, ignoring unrelated nested YAML, and duplicate
ID rejection both within packages and across flat/nested sources.

## Final verification evidence (2026-08-05)

```text
uv run pytest -q
275 passed in 12.62s

uv run ruff check .
All checks passed!

uv run mypy src
Success: no issues found in 34 source files

uv run mypy --strict src/envresearch/benchmarks/registry.py \
  src/envresearch/models/benchmark.py tests/unit/test_benchmark_models.py \
  tests/integration/test_benchmark_catalog.py
Success: no issues found in 4 source files

git diff --check
exit 0
```

`uv run mypy --strict src tests` also reports 24 pre-existing errors in five
unrelated test files. They do not affect the plan-mandated `uv run mypy src`
result, which is clean; Task 13's strict source and test scope is also clean.

## Self-review

No Task 13 defects found. The catalog contains no unverified licensing,
archive, or checksum values; its non-runnable status is enforced by integration
tests; and registry discovery cannot silently mask a duplicate ID across the
flat catalog and nested package forms.

## Final status

Complete under the 2026-08-05 v0.1 metadata-only ruling. The prior
public-replay provenance gate and its known failing assertion are historical
only and no longer define Task 13 completion.
