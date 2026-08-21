# Task 8 report — end-to-end verification and boundary record

## Status

The V0.3-A capability pack is implemented and repository-owned behavior is
verified. The JEL candidate remains dry proposed, non-admitted,
non-downloaded, and non-executed. Docker and Podman are unavailable, and the
stock live runtime boundary remains intentionally fail-closed. V0.2 behavior
is unchanged.

## Compatibility regression

Added an explicit model-level regression proving
`DesignBenchmarkManifest` rejects `tier=2` with `Tier 2 is not allowed in
v0.2`. The requested focused command produced:

```text
1 passed, 29 deselected
```

This is a characterization test for existing V0.2 behavior; it required no
production-code change.

## Complete verification evidence

- `uv sync --locked --dev`: resolved 35 packages and audited 33 packages.
- `uv run pytest --cov=envresearch --cov-report=term-missing`: 1,473 passed,
  5 skipped in 306.52 seconds.
- Coverage: 16,023/17,601 statements, `91.03460030680075%`.
- `uv run ruff check .`: passed.
- `uv run mypy src`: passed, 193 source files.
- Affected container/DiD/intake suites: 52 passed.
- Replication plus V0.2 compatibility matrix: 262 passed.
- Changed-file formatter gate: all 45 changed Python/Markdown files passed.
- Every changed Python file is at or below 400 lines. The largest Task 8
  structural file is `src/envresearch/replication/container.py` at 370 lines.

The full `uv run ruff format --check .` command exits 1 on an inherited
formatter baseline. It identifies 99 files; all 99 are unchanged from
implementation base `5de634f`, and neither `pyproject.toml` nor `uv.lock`
differs from that base. No unrelated historical files were reformatted.

The line-cap cleanup extracted private container execution helpers and
test-only DiD/intake fixtures. Public imports, command construction, archive
inspection behavior, and assertions are unchanged. The post-split full suite
and both focused matrices above passed.

## Runtime and no-external evidence

Read-only executable discovery found neither Docker nor Podman:
`runtime_unavailable`. No engine was installed or invoked.

Replication integration tests inject the repository-owned archive through
`FixtureFetcher` and execute through `FakeEngine`. HTTP-boundary unit tests
replace `urllib.request.build_opener` with offline fakes. The only JEL-named
repository file is `benchmarks/replication/proposals/jel-did-2026.yaml`, and
`artifacts/replication/` contains zero files after verification. The task did
not access an external package, JEL archive, Zotero, R, Docker, or Podman.

## Remaining boundary

The next real run still requires a separately reviewed executable intake and
the documented exact `approve-external` action. A reviewed deployment must
also bind a live Docker or Podman executor. These are external admission and
deployment facts, not missing offline implementation. The later
environmental-policy benchmark promotion remains the second human decision.
