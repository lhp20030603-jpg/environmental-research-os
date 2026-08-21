# V0.3 Whole-Branch Hardening Slice A Report

Date: 2026-08-10

## Scope

This slice hardens only the V0.3 replication runtime, CLI, workspace, and
evidence integration boundaries. It does not change V0.2 behavior, the
scientific DiD schema, or execute a real network request, Docker/Podman
process, or R program.

## RED evidence

Thirteen dynamic regressions failed before their corresponding production
changes:

- two stock-CLI cases showed that no configured Docker/Podman engine could be
  selected and that selection authority was not injectable for offline tests;
- malformed preflight evidence reached persistence, a green run recorded no
  orchestration heartbeats, inactivity became EXCEPTION, configured growth
  policy was absent, and input/output roots escaped the operator run root;
- PASSED status survived deletion of an author artifact, mutation of raw
  author output, mutation of the materialized expected input, and deletion of
  the acquired archive;
- a failed PASSED-status re-verification was not retained as a durable sealed
  report; and
- host-observed workspace growth became EXCEPTION when the engine
  underreported storage instead of producing an authenticated PAUSED
  checkpoint.

Strict persisted-runtime schema regressions additionally cover empty versions,
noncanonical hashes, non-boolean flags, negative resources, non-UTC or
unordered timestamps, and extra fields.

## Resulting boundaries

- The stock CLI discovers only the ordered configured Docker/Podman choices,
  validates an absolute executable, and uses an exact no-shell subprocess argv
  with bounded capture and an activity-reset inactivity deadline. There is no
  host-language or unconfigured-engine fallback.
- Runtime observations are exact typed evidence bound to the selected engine
  before persistence, on resume, and during independent verification.
- The service records sealed resource heartbeats before and after each stage.
  It combines engine-reported storage with the confined workspace measurement;
  inactivity and unexpected growth produce a durable PAUSED ledger generation
  plus an exact bounded workspace checkpoint. Resume authenticates every
  retained file before the state transition.
- Acquired materialization and exclusive attempt workspaces are derived from
  the operator-supplied run root and exact approved/attempt identities.
- Each author output retains a sealed manifest and immutable content-addressed
  raw byte copy. Independent verification reopens and rehashes the raw archive,
  every inventory member, the materialized inputs, the retained workspace
  output, its raw blob, and all sealed evidence before repeating the approved
  comparator. Failed terminal re-verifications are persisted before status
  fails closed.

## GREEN evidence

- Focused replication unit/integration suite: `226 passed in 7.07s`.
- Replication, benchmark, CLI, and V0.2 compatibility matrix: `343 passed in
  22.93s`.
- Explicit V0.2 Tier-2 rejection boundary: `1 passed, 29 deselected in 0.17s`.
- Ruff formatting: `44 files already formatted`; Ruff lint: all checks passed.
- Mypy: `Success: no issues found in 30 source files`.
- `git diff --check`: passed.
- Every changed Python file is below 400 lines. The largest are `cli.py` at
  394, `verify.py` at 391, `container.py` at 389, and `service.py` at 388.

## Review notes

The final self-review specifically rechecked executable discovery, no-shell
argv construction, inactivity cleanup, heartbeat transition ordering,
checkpoint/reference binding, content-addressed publication, independent
rehashing, and operator-root confinement. The available team-agent limit
prevented an additional reviewer-agent turn; the controller's planned fresh
independent review remains the external approval gate.

## Fix round 2: recovery, progress, containment, and reviewed control

### TDD evidence

The round-two RED wave reproduced each independent review finding before the
production changes:

- four interrupted ledger-publication/start cases showed that sealed PENDING
  or ownerless RUNNING attempts could remain nonterminal forever;
- three blocking-runtime cases showed that periodic progress was absent and a
  hard storage observation could escape instead of returning its durable
  terminal report;
- three initial containment cases showed missing tracked-container control,
  direct-client-only termination, and untyped cleanup failure;
- seven reviewed-control cases showed mutable PATH, inherited remote/control
  environment, symlink or writable executables, executable replacement, and
  unbound endpoint/digest evidence were not rejected; and
- five focused containment-order/bounds cases showed stubborn descendants,
  unproven removal, terminal publication before cleanup, and an unbounded
  post-SIGKILL wait.

All of these reproductions are now GREEN using only fake executors, fake
container control, local child processes, and fixture engines.

### Resulting boundaries

- The approval-scoped process lock now recovers the immutable first attempt.
  Interrupted PENDING publication resumes through `ledger.start`; an
  ownerless incomplete RUNNING generation is sealed as an exact
  `interrupted-owner` PAUSED checkpoint. A live lock owner still excludes a
  second fetch or execution.
- The blocking subprocess boundary emits bounded periodic progress without
  resetting its output-inactivity deadline. The controller persists ordered
  elapsed, memory, and independently measured workspace-storage observations.
  In-flight resource stops remain RUNNING until containment succeeds, after
  which the exact PAUSED or EXCEPTION generation is published. A hard final
  heartbeat returns the already durable terminal reference.
- Runtime errors terminate the entire process group with bounded SIGTERM to
  SIGKILL escalation. Every workload has an exact deterministic container
  name; failure handling force-removes it and accepts absence only after a
  successful removal plus a known not-found inspection result. Cleanup failure
  becomes the first durable `CONTAINMENT_CLEANUP_FAILED` exception.
- Engine selection no longer consults PATH or inherited Docker/Podman control
  and credential variables. It requires a configured absolute nonsymlink
  executable with reviewed owner, mode, digest, device/inode identity, and an
  explicit local Unix endpoint. Spawns receive only the minimal reviewed
  environment, and preflight/resume/verifier evidence binds engine identity,
  executable digest, and endpoint.

### Final GREEN evidence

- Focused replication and CLI suite: `269 passed in 9.38s`.
- Replication, benchmark, CLI, and V0.2 compatibility matrix: `365 passed in
  25.81s`.
- Explicit V0.2 Tier-2 rejection boundary: `1 passed, 29 deselected in 0.21s`.
- Ruff formatting: `42 files already formatted`; Ruff lint: all checks passed.
- Mypy: `Success: no issues found in 34 source files`.
- `git diff --check`: passed.
- Every touched Python file is at most 400 lines. The largest are `cli.py` at
  400, `service.py` at 398, `container.py` at 395, and
  `test_replication_service_hardening.py` at 382.

### Operational note

The checked-in production engine entries intentionally remain unavailable
until an operator provisions the reviewed absolute executable and replaces
the placeholder digest with that executable's approved SHA-256. This is the
designed fail-closed state, not an automatic host fallback.

### Final containment review closure

The independent round-two review produced three additional dynamic REDs:

- an interrupted RUNNING generation retained a sealed runtime owner but
  recovery did not contain its exact PGID/container before checkpointing;
- a successful direct process exit did not prove that the process group had
  no residual descendants; and
- successful execution did not prove the deterministic container absent
  before clearing ownership and allowing completion.

A final two-test RED reproduced a pause-publication atomicity gap: a final
inactivity observation could publish PAUSED before checkpoint persistence,
and public ledger recovery accepted a PAUSED generation with no checkpoint.

The runtime now seals its exact PGID, deterministic container name, engine,
and start time before workload execution. Recovery contains that owner before
publishing `interrupted-owner`; normal completion verifies both the full
process group and container absent before clearing ownership. Cleanup failure
retains the owner in the first durable `CONTAINMENT_CLEANUP_FAILED` exception.
The service also selects the newest internally sealed ledger generation when
an in-flight signal and cleanup callback both advance the run, while preserving
the measured heartbeat that caused a pause and performing a final resource
measurement for raw inactivity.
Final and post-stage resource observations now remain RUNNING until the exact
bounded checkpoint is sealed; only then does the ledger publish PAUSED. A
checkpoint write failure instead becomes the first durable
`PERSISTENCE_FAILURE`. Public PAUSED recovery authenticates exactly one sealed
checkpoint, its full producer identity, exact predecessor generation, attempt,
output root, and payload schema.

Fresh final gates after these changes:

- pause-atomicity REDs: `2 passed in 0.69s`; final ledger/container/runtime
  regression set: `74 passed in 4.05s`;
- focused replication and CLI suite: `274 passed in 10.27s`;
- replication, benchmark, CLI, and V0.2 compatibility matrix: `370 passed in
  27.20s`;
- explicit V0.2 Tier-2 rejection boundary: `1 passed, 29 deselected in 0.25s`;
- Ruff formatting: `48 files already formatted`; Ruff lint: all checks passed;
- Mypy: `Success: no issues found in 38 source files`;
- `git diff --check`: passed; and
- all touched Python files remain below 400 lines; the largest are `service.py`
  and `test_replication_service_hardening.py` at 390 lines each.

The final independent narrow re-review approved the containment and
pause-atomicity closures with no remaining blocking finding.

## Fix round 3: non-reusable identity, truthful resources, and bounded evidence

### TDD evidence

Five additional offline regression files exposed six remaining failures after
the round-two closure:

- closing both child pipes could disable the inactivity monitor or enter an
  unbounded wait;
- PID/PGID reuse and deterministic container-name reuse were not protected by
  a kernel birth identity, unpredictable launch nonce, full container ID,
  image digest, and exact mount bindings;
- an `OSError` while releasing a contained owner produced an unreadable
  terminal generation, and interrupted recovery called a drifted runtime
  control before reopening its persisted executable digest and endpoint;
- resource evidence was hard-coded to zero instead of distinguishing measured,
  unknown, and OOM-killed observations; and
- every heartbeat generation retained the full observation history, causing
  unbounded generation size and quadratic aggregate ledger growth.

The initial restored worktree was also missing `_subprocess_capture.py` and
`_runtime_stats.py`: the previous worker had accidentally written those two
files under a look-alike local repository path whose name omitted one segment.
Their contents were
restored into the real worktree before the RED matrix was rerun. After verifying
that both real copies existed (and that the resource parser still had the same
SHA-256), only those two exact accidental files were deleted; no other file or
directory under the look-alike path was changed.

### Resulting boundaries

- A `RuntimeLaunchIdentity` with an unpredictable 256-bit nonce, nonce-derived
  name, exclusive cidfile, image digest, and hashed input/output mount bindings
  is sealed before spawn. The active `RuntimeOwnership` extends it with exact
  PID, PGID, kernel birth digest, and full container ID.
- Recovery and normal containment perform read-only process/container identity
  checks before mutation. Container removal targets only the verified full CID,
  never a reusable name; a PID birth mismatch issues no signal and a container
  binding mismatch issues no removal.
- Interrupted recovery first reopens the sealed runtime observation and matches
  engine identity, executable SHA-256, and local endpoint to the selected
  control. Drift or unavailable authority touches no runtime and yields a
  readable `CONTAINMENT_CLEANUP_FAILED` report with the owner retained.
- Owner release clears launch and owner atomically. If release persistence
  fails after verified cleanup, the readable authenticated terminal retains
  ownership under `CONTAINMENT_CLEANUP_FAILED` so a later recovery can safely
  prove absence and release it.
- Runtime sampling uses the reviewed no-shell engine boundary. Measured memory,
  writable storage, and OOM state remain typed; failed or incomplete probes are
  `unknown`, never converted to a successful zero measurement. Unknown and OOM
  evidence fail the approved resource boundary closed.
- Each heartbeat generation retains at most eight recent observations plus a
  cumulative observation count and SHA-256 chain. The current ledger remains
  directly recoverable and budget checks still use the latest exact sample,
  while per-generation evidence size stays bounded.

### Fresh GREEN evidence

- Original round-three five-file matrix: `10 passed in 2.82s`.
- Expanded runtime, ledger, container, and service compatibility set:
  `122 passed in 13.26s`.
- All replication unit and integration tests: `264 passed in 17.12s`.
- Explicit V0.2 catalog/Tier-2 rejection boundary: `3 passed in 6.31s`.
- Full repository suite: `1,532 passed, 5 skipped in 226.41s`.
- Ruff: `uv run ruff check .` passed.
- Mypy: no issues in 216 source files.
- `uv lock --check` and `git diff --check`: passed.
- Every source Python file is at most 400 lines; `container.py` is 396 lines.
- All verification used fixture engines, fake controls, or local child
  processes. No network request, Docker/Podman daemon, container, or R program
  was invoked.

### Review status

The controller will run the authoritative independent review against the exact
round-three commit. A nested no-edit review was requested but did not return a
verdict before the commit gate, so no unreceived verdict is claimed here.
