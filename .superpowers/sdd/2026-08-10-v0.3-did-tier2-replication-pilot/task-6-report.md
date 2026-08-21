# Task 6 Report: Autonomous Tier-2 replication service

## Delivered

- Added a read-only `ReplicationVerifier` that reopens the exact current ledger,
  immutable ledger generation, report copy, proposal, approval, acquired
  inventory, runtime observation, every author output, and the derived report.
  It validates seals, references, producers, input chains, payload bindings, and
  one comparison result per declared output. It exposes no persistence or
  promotion API.
- Added the single public `ReplicationService` controller for dry intake,
  explicit external admission, run, resume, and status. The green path acquires
  only after approval, preflights before execution, compares author outputs
  before derived analysis, and reaches `PASSED` automatically only after a
  zero-finding independent verification.
- Known intake, container, comparison, resource, and verification failures are
  converted to durable typed `EXCEPTION` results. The first exception is
  preserved and a repeated request does not reacquire or retry automatically.
- Persisted bounded execution evidence and content-addressed author, derived,
  verification, ledger, and attempt artifacts. The implementation uses the
  existing intake, container, ledger, and R-DiD adapter contracts without
  weakening their reviewed boundaries.

## Authorized scope amendments

- Extended `replication/ledger.py`: author/derived completion remains
  verification-pending in `RUNNING`; `publish_verification()` is the only
  transition to `PASSED` and accepts only a sealed, exact-ref, zero-finding
  `VerificationReport`; `exception()` durably records the first typed failure.
  No caller-controlled pass boolean exists.
- Added private `_service_support.py` only for sealed-artifact
  parsing/persistence, attempt mechanics, and trusted archive materialization.
- Added private `_verify_support.py` only for exact artifact-reference, copy,
  input-chain, and payload checks. Neither private module is publicly exported.
- The authorized ledger contract extension is covered in both focused ledger
  and verifier tests; fixture builders were separated into private test-support
  modules to keep every touched file below the line cap.

## TDD evidence

- Verifier/ledger RED: `uv run pytest tests/unit/test_replication_verify.py
  tests/unit/test_replication_ledger.py -v` failed during collection with two
  import errors because `envresearch.replication.verify` did not exist.
- Verifier/ledger GREEN: the focused verifier/ledger slice passed (`22 passed`)
  after implementation.
- Service RED: `uv run pytest tests/integration/test_replication_service.py -v`
  failed during collection because `envresearch.replication.service` did not
  exist.
- Promotion hardening RED: a caller-forged sealed empty-finding report promoted
  despite the real verifier finding a replaced output (`1 failed`). The ledger
  now independently reruns the read-only verifier and requires payload equality;
  the regression passed (`1 passed`).
- Final focused GREEN: `uv run pytest
  tests/integration/test_replication_service.py
  tests/unit/test_replication_verify.py tests/unit/test_replication_ledger.py -q`
  passed (`34 passed in 1.52s`).

## Final verification

- Broader replication and benchmark compatibility suite passed (`209 passed in
  23.33s`) across contracts, intake, container, ledger, R-DiD, verifier,
  service, benchmark catalog/runner/finalization, and research-quality tests.
- Ruff format and lint passed for every Task 6 implementation and test file.
- Mypy passed over the Task 6 implementation, amended ledger and exports, and
  both new test modules (`Success: no issues found in 8 source files`).
- Every new implementation/test file is within the 400-line cap: `service.py`
  396, `verify.py` 397, `_service_support.py` 400, `_verify_support.py` 160,
  service integration test 400, verifier unit test 389.
- `git diff --check` passed. Test-owned materialized inputs and run outputs were
  cleaned after each integration case.

## Scope and concerns

- No real network, Docker, Podman, container, or R process was invoked. Tests
  use an owned archive fixture, fake fetcher, and fake engine at the real
  orchestration boundaries.
- V0.2 files and behavior were not modified. There is no routine second human
  gate, automatic external reacquisition, `BenchmarkRunner.default()`, or
  `CommandRunner` path in the controller.
- This slice intentionally does not add a CLI or live engine/fetcher wiring;
  those are outside Task 6.

## Independent review fixes

- Engine-raised memory/storage exhaustion and other known execution boundary
  errors now become durable typed exceptions; service-originated typed faults
  retain their original stable codes.
- Approval and proposal producer/input-chain identity is authenticated before
  acquisition, so forged admission cannot trigger the fetcher.
- Resume reopens and authenticates checkpoint evidence before PAUSED-to-RUNNING
  transition; replaced evidence becomes a durable `RESUME_EVIDENCE_INVALID`.
- A verification-pending RUNNING generation is finalized on the next `run`,
  while PASSED/EXCEPTION generations are returned unchanged. Publish races
  become durable verification exceptions.
- Materialized inputs are approval/run-scoped. Service-generated files are
  cleared before exact inventory revalidation; raw archives are rehashed and
  each member is read with the inventory byte bound before persistence.
- Malformed immutable YAML history is converted into a verifier finding rather
  than escaping and leaving the run nonterminal.
- Public ledger reads authenticate the exact current ID, producer, lifecycle,
  immutable history/report copies, and terminal evidence coherence. Durable
  attempt aliases bind the exact subject, typed evidence inputs, and immutable
  content-addressed report copy.
- Independent review approved the final tree with no Critical, Important, or
  Minor findings (`157` replication tests plus Ruff, mypy, and diff checks).

## Controller hardening fix round 1

### Additional authorized scope

- Added private attempt, evidence, ledger-storage, ledger-evidence,
  service-execution, and verification-model helpers. They contain no public
  promotion switch, network/runtime implementation, or policy bypass; public
  imports remain the existing ledger, verifier, and service contracts.
- Extended the ledger run identity with an immutable attempt claim and canonical
  output root, and bound redacted execution logs into output, derived, ledger,
  and verifier evidence chains.

### RED evidence

- Added 13 dynamic controller regressions covering a process-safe first attempt,
  concurrent one-fetch idempotence, pending claim/ledger recovery, exclusive
  attempt output roots, stale resume rejection, runtime authentication, PASSED
  verification/predecessor reopening, durable persistence failures, bounded
  logs, and persisted failed-verifier evidence.
- The first hardening run produced `11 failed, 1 passed`; the passing case was
  corrected to target the actual planted namespace, and the later explicit
  ledger attempt/root binding regression also failed before implementation.
- The failures reproduced unauthorized duplicate acquisition, non-durable
  boundary faults, unauthenticated terminal evidence, stale workspace reuse,
  and missing log/verifier-failure evidence rather than relying on static
  inspection alone.
- Final review reproduced a runtime-artifact persistence error escaping as
  `UnboundLocalError` because its evidence reference was not yet assigned. The
  exact injected runtime-write RED now becomes a durable
  `ADMITTED_EVIDENCE_INVALID` and is idempotent on repetition.

### GREEN evidence

- Task 6 focused suite: `49 passed in 2.37s` across service, hardening,
  verifier, and ledger tests.
- Broader replication and benchmark compatibility suite: `267 passed in
  18.24s`.
- Ruff format check and lint passed for all Task 6 production and test files;
  mypy passed for all 18 replication source modules; `git diff --check` passed.
- Every touched source/test file is at most 400 lines. Largest files are
  `service.py` at 400, `verify.py` at 388, and the hardening integration test at
  381 lines.

### Resulting invariants

- The approval-subject lock is acquired before fetch. A sealed pending claim is
  recovered atomically, the first writer owns the immutable attempt alias, and
  concurrent callers observe the same terminal report with one acquisition.
- Each attempt receives a newly created canonical output root. An existing root
  is rejected; resume accepts only the exact current claim/root and empty
  checkpoint, so planted or stale output files cannot satisfy a new run.
- Every public ledger read performs locked pending recovery and authenticates
  current/history/report copies. PASSED additionally reopens the exact sealed
  verifier artifact, its zero findings, complete verified-ref set, run ref, and
  verification-pending predecessor generation.
- Known orchestration OSError/ValueError and engine boundary failures become the
  first durable typed EXCEPTION; programming errors remain uncaught. Resume
  authenticates admission, inventory, runtime, claim, and workspace before its
  state transition.
- Ledger promotion persists a newly recomputed independent verifier artifact.
  Failed reports are also persisted and referenced by the durable exception.
  Execution logs contain only bounded hashes, truncation flags, and redacted
  placeholders, and are verified as exact evidence inputs.

### Final independent review

- APPROVED with no Critical, Important, or Minor findings remaining. The final
  reviewer independently confirmed all controller invariants, public API
  docstrings, the 400-line cap, `49` focused tests, Ruff, mypy across 18
  replication modules, and `git diff --check`.

## Controller hardening fix round 2

### Scoped RED evidence

- Added eight exact regressions for the three independently reproduced gaps:
  four coherent PASSED-chain forgeries varied the completed predecessor ID,
  producer, lifecycle, and version; three engine results carried a raw secret,
  a noncanonical digest, or a negative resource observation; and one evidence
  mutation occurred after the first positive verification but before the
  ledger's independent recomputation.
- Before the fixes, all eight regressions failed: coherent predecessor
  substitutions were accepted, malformed engine evidence reached persisted
  logs and execution provenance, and the second verifier mismatch produced an
  exception with no evidence reference. Additional cases cover exact boolean
  flags, UTC and ordered timestamps, and both nonnegative resource counters.

### Resulting invariants

- A terminal PASSED ledger now requires its completed predecessor to be a
  sealed `replication-ledger` artifact produced by `replication-ledger`, in the
  VALIDATED lifecycle, at exactly the immediately preceding version, in
  addition to the existing reference, payload, and verification checks.
- Every `ContainerResult` is checked immediately after the engine boundary and
  before logs or execution evidence can be persisted. Both digests must be
  canonical lowercase SHA-256 values, truncation flags must be exact booleans,
  timestamps must be ordered UTC values, and resource counters must be exact
  nonnegative integers; malformed evidence becomes a durable
  `CONTAINER_EVIDENCE_INVALID` exception.
- The ledger persists its independently recomputed verification artifact before
  comparing or rejecting promotion. A typed publication failure carries that
  exact report reference, and the service binds it into the first durable
  `VERIFICATION_FAILED` exception. A stale positive report therefore cannot be
  the sole evidence when the second verifier observes a mutation.

### GREEN evidence

- Focused controller, verifier, and ledger suite: `61 passed in 2.89s`.
- Broader replication and benchmark compatibility suite: `279 passed in
  20.26s`.
- Ruff formatting and lint passed for all replication source and focused test
  files; mypy passed all 18 replication source modules; `git diff --check`
  passed.
- All touched files remain at or below 400 lines. The largest touched source is
  `service.py` at exactly 400 lines; the largest touched test is
  `test_replication_verify.py` at 368 lines.
- Tests used only the fake fetcher and fake engine; no real network, Docker,
  Podman, or R execution was introduced.

## Controller hardening fix round 3

### Scoped RED evidence

- Reproduced seven exact failures before production changes. Coherently
  resealed PASSED predecessor and current ledger copies retained the admitted
  producer component but changed its version and were accepted. At the engine
  boundary, `exit_status=False`, raw and alternate engine names, a wrong image
  digest, and a non-`ContainerResult` impostor either passed, received the wrong
  classification, or escaped later during evidence serialization.
- The tests exercise authenticated public ledger reads and the complete service
  lifecycle with the existing fake fetcher/engine; they do not assert private
  constants or mock-only call behavior.

### Resulting invariants

- Ledger publication and reopening share one exact expected
  `ProducerIdentity(component="replication-ledger", version="0.3.0")`. Both
  the authoritative current generation and a PASSED generation's completed
  predecessor require full identity equality, in addition to existing ID,
  lifecycle, version, seal, copy, payload, and verification-chain checks.
- `run_engine` first requires a canonical persisted preflight engine identity,
  then requires the exact `ContainerResult` type. Before returning any result
  for log or provenance persistence, it binds a canonical result engine to the
  preflight engine, binds an exact string image digest to the plan, rejects
  boolean or non-integer exit statuses, and retains the prior canonical digest,
  exact boolean, UTC/order, and nonnegative-resource checks.
- Resume obtains the expected engine identity from the exact authenticated
  runtime-observation artifact, while a new run uses the same observation value
  that it seals before ledger start.

### GREEN evidence

- Focused controller, verifier, and ledger suite: `68 passed in 4.37s`.
- Broader replication and benchmark compatibility suite: `286 passed in
  24.45s`.
- Ruff formatting and lint passed across replication source and focused tests;
  mypy passed all 18 replication source modules; `git diff --check` passed.
- Every touched source/test file is below 400 lines. The largest touched source
  is `service.py` at 397 lines and the largest touched test is
  `test_replication_verify.py` at 343 lines. Shared completed-ledger setup now
  lives in the 247-line verifier fixture module.
- No real network, Docker, Podman, container, or R execution was used.
