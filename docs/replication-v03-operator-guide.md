# V0.3-A DiD Tier-2 Operator Guide

## Current boundary

V0.3-A is a fail-closed R-first DiD/event-study replication capability pack.
It is separate from the trusted V0.1/V0.2 benchmark runner. Repository tests
use only the owned tiny archive and a fake container engine; they do not use
the network, Docker, Podman, R, Zotero, or a published replication archive.

The checked-in `jel-did-2026.yaml` is deliberately a **dry proposal**. Its
public bibliographic identity was verified on 2026-08-10 against the
[AEA article record](https://www.aeaweb.org/articles?id=10.1257/jel.20251650):
Journal of Economic Literature 64(2), 498–557, DOI
`10.1257/jel.20251650`. The public
[package landing page](https://github.com/pedrohcgs/JEL-DiD) identifies R and
Stata workflows, but also describes external data retrieval. Therefore the
repository does not yet assert an exact archive locator, archive hash,
complete license scope, self-contained status, expected-output map, or pinned
executable image. No JEL package has been admitted, downloaded, or executed.

## Task 8 verification record

The V0.3-A capability pack is implemented and its repository-owned behavior
was verified on 2026-08-10. The locked dependency audit resolved 35 packages
and audited 33 packages. The complete test suite reported `1,473 passed` and
`5 skipped` in 306.52 seconds, with 16,023 of 17,601 statements covered
(`91.03460030680075%`). The explicit V0.2 compatibility regression also passed:
`DesignBenchmarkManifest` still rejects Tier 2 before execution. Ruff lint
passed, and mypy reported no issues in 193 source files.

The repository-wide formatter check remains a documented inherited baseline,
not a V0.3 regression: 99 files would be reformatted, every one is unchanged
from implementation base `5de634f`, and neither `pyproject.toml` nor `uv.lock`
changed. A separate check of all 45 changed Python/Markdown files passed. All
changed Python files are at or below 400 lines after private implementation
and test-fixture helpers were split without changing their public behavior. No
unrelated historical file was mechanically reformatted.

Read-only executable discovery returned `runtime_unavailable` for both Docker
and Podman. No engine was installed or invoked. Integration tests use the
repository-owned archive, `FixtureFetcher`, and `FakeEngine`; HTTP boundary
tests replace `urllib.request.build_opener` with offline fakes. The only
JEL-named repository file is the dry proposal YAML, and verification left zero
files under `artifacts/replication/`. No external fetch, real container, R,
Zotero, or JEL archive was accessed.

This verifies the implemented offline capability and its fail-closed
boundaries; it is not a real-package result. The live runtime factory remains
intentionally unavailable until a reviewed deployment binds Docker or Podman.
V0.2 is unchanged, and the exact external-admission command below remains the
only human action required before a first real run.

## The two human actions

Only two human decisions are part of this pilot:

1. Approve one exact executable intake, acquisition URL, license and declared
   inputs, runtime image, and resource budget before acquisition.
2. After the JEL pilot passes, approve promotion to a separately reviewed
   environmental-policy replication package.

Everything between those decisions is Agent-operated. A green reproduction
and independent verification completes automatically. Failures become durable
typed exceptions with evidence references; they are not converted into a
routine approval gate.

## Command sequence

All state-changing commands require an explicit durable root. All execution,
resume, and status commands require an exact `ArtifactRef` JSON file; the CLI
never scans for a “latest” artifact.

### 1. Validate without writing

```bash
envresearch replication validate \
  benchmarks/replication/proposals/jel-did-2026.yaml --json
```

Validation reads one YAML file only. A dry proposal returns
`"executable": false` and its unresolved blockers. It creates no proposal,
approval, acquisition, or run artifact.

### 2. Approve an executable intake

Do not run this command against the checked-in dry proposal. First create a
separately reviewed `tier2-intake-v1` file that resolves every blocker.

```bash
envresearch replication approve-external intake.yaml \
  --run-root /absolute/path/to/replication-run \
  --approver-id human-research-owner \
  --rationale "Reviewed the exact package, license, inputs, runtime, and budget." \
  --approved-locator https://exact.example/package.tar.gz \
  --json > external-admission.json
```

The command rejects a dry proposal or locator mismatch before writing any
artifact. On success, `external-admission.json` contains the exact proposal
and approved-intake references. This is the only command that records the
pre-acquisition human decision.

### 3. Run without another prompt

```bash
envresearch replication run \
  --run-root /absolute/path/to/replication-run \
  --approved-ref external-admission.json \
  --json > replication-report.json
```

`run` cannot create or infer approval. It consumes the exact approved
reference, acquires only its exact URL, and routes execution through the
container-only service. The stock checkout keeps the live engine boundary
fail-closed until an operator deployment binds the reviewed Docker or Podman
executor; in that state, a run records `NO_CONTAINER_ENGINE` rather than
falling back to local R or shell execution.

### 4. Resume only an authenticated paused generation

```bash
envresearch replication resume \
  --run-root /absolute/path/to/replication-run \
  --run-ref replication-report.json \
  --json > resumed-report.json
```

Resume accepts only an exact `replication-ledger` reference and reopens its
approved, acquired, and runtime evidence. It neither reacquires authority nor
uses a proposal path.

### 5. Read status without changing state

```bash
envresearch replication status \
  --run-root /absolute/path/to/replication-run \
  --ref replication-report.json \
  --json
```

Use the emitted `status_ref`. Pre-ledger exceptions bind status to the exact
approved reference; ledger-backed runs bind it to the exact ledger generation.

## Machine contract

| Exit | Meaning |
|---:|---|
| `0` | validation/approval succeeded, run passed, or status was read (including exception status) |
| `1` | run or resume returned a durable non-passing report |
| `2` | proposal, authority, locator, or reference was absent or invalid |

JSON application errors use `{"error":{"code":...,"message":...}}`.
Reports explicitly serialize `run_ref`, `status_ref`, `state`, `exception`,
author outputs, derived output, and independent-verification reference.

## Not authorized by this guide

- downloading or opening the JEL replication archive;
- treating a repository or article landing page as an approved direct locator;
- asserting an archive hash or complete license before acquisition review;
- installing R packages or executing R on the host;
- substituting Stata or an unrestricted local command runner;
- calling derived DiD diagnostics an author reproduction or a paper claim.
