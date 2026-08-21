# Task 3 Report: Research-owned correct-stop inspection

## Status

Implemented the read-only Research correct-stop inspector, exact Personal input/system/root inventory builders, and versioned cross-root absence validation. All focused and affected read-only regression tests pass, and all requested static gates are clean.

## Files

- `src/envresearch/research/stop_inspection.py` (created)
- `src/envresearch/research/__init__.py` (modified)
- `src/envresearch/personal_validation/snapshots.py` (created)
- `tests/integration/test_research_stop_inspection.py` (created)
- `tests/unit/test_personal_validation_snapshots.py` (created)

## TDD Evidence

### Research stop inspection RED

Command:

```text
uv run pytest tests/integration/test_research_stop_inspection.py -q
```

Observed before production code:

```text
ModuleNotFoundError: No module named 'envresearch.research.stop_inspection'
1 error during collection
```

### Research stop inspection GREEN

Command:

```text
uv run pytest tests/integration/test_research_stop_inspection.py -q
```

Observed after the minimal implementation:

```text
3 passed in 1.71s
```

The integration test uses a real blocked Research root and forbids calls to `ResearchOrchestrator.initialize`, `ResearchOrchestrator.advance`, `ResearchOrchestrator._summarize`, `RevisionTransaction.recover_pending`, `ResearchAuditState.sync`, and `FilesystemWorkerQueue.issue`. It also compares the complete Research tree before and after inspection, including bytes, metadata, links, and inode identity.

### Personal snapshots RED

Command:

```text
uv run pytest tests/unit/test_personal_validation_snapshots.py -q
```

Observed before production code:

```text
ModuleNotFoundError: No module named 'envresearch.personal_validation.snapshots'
1 error during collection
```

### Personal snapshots GREEN

Command:

```text
uv run pytest tests/unit/test_personal_validation_snapshots.py -q
```

Observed after the initial minimal implementation:

```text
24 passed
```

### Adversarial RED/GREEN refinements

An omitted-scope attack placed a stray empirical result under `research-design`. Before broadening the empirical predicate to every governed root, the focused matrix reported:

```text
11 passed, 1 failed
Failed: DID NOT RAISE PersonalValidationIntegrityInvalid
```

After the predicate correction:

```text
12 passed in 0.61s
```

An empty forbidden output namespace initially evaded the file-only predicate:

```text
Failed: DID NOT RAISE PersonalValidationIntegrityInvalid
```

After applying the namespace policy to files, directories, and symlinks:

```text
1 passed in 0.88s
```

## Final Verification

Brief-required focused suite, rerun against the final staged implementation:

```text
uv run pytest tests/integration/test_research_stop_inspection.py tests/unit/test_personal_validation_snapshots.py tests/integration/test_factory_design_resolver.py -q
50 passed in 16.07s
```

Combined focused and affected Research/Factory read-only regressions:

```text
uv run pytest tests/integration/test_research_stop_inspection.py tests/unit/test_personal_validation_snapshots.py tests/integration/test_factory_design_resolver.py tests/integration/test_factory_root_safety.py tests/integration/test_factory_run.py tests/integration/test_research_orchestrator.py tests/integration/test_research_orchestrator_integrity.py tests/integration/test_research_orchestrator_recovery.py -q
104 passed in 47.07s
```

Static checks:

```text
uv run ruff check src/envresearch/research/stop_contracts.py src/envresearch/research/stop_inspection.py src/envresearch/personal_validation/snapshots.py tests/integration/test_research_stop_inspection.py tests/unit/test_personal_validation_snapshots.py
All checks passed!

uv run mypy src/envresearch/research/stop_contracts.py src/envresearch/research/stop_inspection.py src/envresearch/personal_validation/snapshots.py
Success: no issues found in 3 source files

uv run ruff format --check src/envresearch/research/stop_inspection.py src/envresearch/research/__init__.py src/envresearch/personal_validation/snapshots.py tests/integration/test_research_stop_inspection.py tests/unit/test_personal_validation_snapshots.py
5 files already formatted

git diff --check
(no output; exit 0)
```

All changed Python files remain within the 400-line limit: 310, 397, 162, and 310 lines respectively.

## Self-review

- Research inspection reopens existing authority and uses the pure `summarize` path; it does not initialize, advance, recover, synchronize audit state, issue work, or import Personal validation contracts.
- The inspection binds the exact unresolved DesignReview findings and review ref, active rejected gate/context payloads, completed checkpoint bytes plus input/output artifact refs, and descriptor-relative Research evidence.
- Input snapshots cover files, directories, symlinks, modes, untracked content, and effective configuration.
- System snapshots bind the repository commit/status, execution tree, lockfile, capability/method digests, protocol ref, and sorted runtime versions without taking Git locks.
- Root snapshots require the exact nine governed roots, pin each root identity, record complete sorted entry evidence, and reject identity/metadata changes observed during traversal.
- Correct-stop absence validation rejects each specified Factory, Paper, LocalAnalysis, V0.3, V0.3.1, and stray empirical result category with a typed finding kind. It also rejects missing roots, extra roots, digest/count inconsistencies, and malformed inventories.
- Adversarial coverage includes hidden Factory pointer/object material, Paper results, changed checkpoints, root replacement, omitted-root attacks, forbidden mutator spies, and empty forbidden namespaces.

## Concerns

No implementation concerns remain. The controller explicitly reserved independent review, so no reviewer was dispatched from this task agent; the implementation supplies the requested adversarial cases for that review boundary.

---

## Fix Round 1

Base: `f7563268015a79e460c70eba304c2edc169a1f19`

### Paper namespace coverage

Verified that Paper publishes claim-ledger, argument-map, revision, draft, audit, and release pointers/objects beneath `exit/current` and `exit/objects`. Four real omitted namespaces were added before the policy change.

RED:

```text
uv run pytest tests/unit/test_personal_validation_snapshots.py -k forbidden_result_namespace -q
4 failed, 12 passed, 14 deselected in 0.54s
```

All four failures were `DID NOT RAISE PersonalValidationIntegrityInvalid` for claim-ledger, argument-map object, revision pointer, and revision object attacks.

GREEN:

```text
16 passed, 14 deselected in 1.72s
```

The Paper predicate now rejects every child beneath its real `exit/current` and `exit/objects` output namespaces while allowing the empty namespace roots themselves.

### Nine distinct governed roots

No strict `AttemptRoots` contract exists in the Task 2 codebase, so `snapshot_roots` now accepts only an exact `Mapping[str, Path]`; the former arbitrary attribute-object fallback was removed. All nine roots are opened and retained together, then checked pairwise with descriptor-based overlap detection before traversal.

RED:

```text
uv run pytest tests/unit/test_personal_validation_snapshots.py -k 'same_physical_root or nested_governed_roots or attribute_object_with_extra_root' -q
3 failed, 30 deselected in 0.99s
```

Each attack failed with `DID NOT RAISE PersonalValidationIntegrityInvalid` before the production change.

GREEN:

```text
3 passed, 30 deselected in 0.49s
```

Same-inode and nested roots now raise typed `attempt-root-authority-overlap`; extra/non-contract objects raise `attempt-root-inventory-incomplete`.

### Coherent filesystem generation

Inventory root-level insertion RED:

```text
uv run pytest tests/unit/test_personal_validation_snapshots.py -k root_entry_inserted -q
1 failed, 33 deselected in 0.54s
Failed: DID NOT RAISE PersonalValidationIntegrityInvalid
```

GREEN after exact initial/final directory-name and metadata comparison:

```text
1 passed, 33 deselected in 0.44s
```

Research review/checkpoint replacement RED:

```text
uv run pytest tests/integration/test_research_stop_inspection.py -k authority_replaced_before_tree -q
2 failed, 3 deselected in 1.36s
```

Both failures were `DID NOT RAISE ValueError`. GREEN after single-read authenticated review derivation plus exact final review/gate/context/checkpoint observations:

```text
2 passed, 3 deselected in 1.32s
```

Research root insertion after the first traversal RED:

```text
uv run pytest tests/integration/test_research_stop_inspection.py -k root_entry_inserted_after_tree -q
1 failed, 5 deselected in 1.03s
Failed: DID NOT RAISE ValueError
```

GREEN after a final complete tree comparison:

```text
1 passed, 5 deselected in 0.93s
```

The inspector now derives unresolved findings and the exact review ref from one authenticated canonical byte read, retains a pinned Research root, rechecks exact review/gate/context/checkpoint bytes and stable metadata, reruns the pure summary, and compares a final complete tree generation. It introduces no mutation lock and does not change production lock order.

### Zero-write and resource coverage

Success, missing authority, nonblocked authority, malformed review, and changed checkpoint paths now run repeatedly while comparing complete bytes/metadata/inodes for both the public Research workspace and sibling protected worker-control root. Descriptor counts under `/dev/fd` must return to their exact starting value after each success or exception. The real mid-inspection mutation tests separately prove the sibling control root remains unchanged and descriptors close on failure.

This was a missing-test-surface finding; the strengthened tests passed against the hardened implementation without an additional production resource-management change:

```text
uv run pytest tests/integration/test_research_stop_inspection.py -q
5 passed in 3.38s
```

### Final verification for fix round 1

```text
uv run pytest tests/integration/test_research_stop_inspection.py tests/unit/test_personal_validation_snapshots.py tests/integration/test_factory_design_resolver.py -q
61 passed in 19.01s

uv run pytest tests/integration/test_research_stop_inspection.py tests/unit/test_personal_validation_snapshots.py tests/integration/test_factory_design_resolver.py tests/integration/test_factory_root_safety.py tests/integration/test_factory_run.py tests/integration/test_research_orchestrator.py tests/integration/test_research_orchestrator_integrity.py tests/integration/test_research_orchestrator_recovery.py -q
115 passed in 50.15s

uv run ruff check src/envresearch/research/stop_contracts.py src/envresearch/research/stop_inspection.py src/envresearch/personal_validation/snapshots.py tests/integration/test_research_stop_inspection.py tests/unit/test_personal_validation_snapshots.py
All checks passed!

uv run mypy src/envresearch/research/stop_contracts.py src/envresearch/research/stop_inspection.py src/envresearch/personal_validation/snapshots.py
Success: no issues found in 3 source files

uv run ruff format --check src/envresearch/research/stop_inspection.py src/envresearch/research/__init__.py src/envresearch/personal_validation/snapshots.py tests/integration/test_research_stop_inspection.py tests/unit/test_personal_validation_snapshots.py
5 files already formatted

git diff --check
(no output; exit 0)
```

Final Python line counts are 383 (`stop_inspection.py`), 400 (`snapshots.py`), 242 (integration test), and 394 (unit test).

### Fix-round concerns

No implementation concerns remain. Independent review was not dispatched because the controller explicitly prohibited subagents/reviewers for this fix round.

---

## Fix Round 2

Base: `d29409a04bb5a066b9d4a8635eae253587897f51`

### Coherent generation across all nine roots

The round-1 same-root mutation test did not exercise the interval after one logical root had completed but while a later root was still being scanned. The new attack completes the first sorted root (`citation-control`), begins a real file read under the next root (`factory`), and then inserts a forbidden empirical result into the already-observed first root.

RED:

```text
uv run pytest tests/unit/test_personal_validation_snapshots.py -k first_root_changed_during_later_root_scan -q
1 failed, 34 deselected in 0.50s
Failed: DID NOT RAISE PersonalValidationIntegrityInvalid
```

GREEN:

```text
1 passed, 34 deselected in 1.02s
```

`snapshot_roots` now retains the first exact observation for every simultaneously pinned and pairwise-disjoint root, then performs a second complete descriptor-relative snapshot of every retained root and requires exact equality before constructing the inventory. All `PinnedRoot` and child descriptors remain context-managed; the attack test asserts the exact `/dev/fd` count is restored on failure.

### Review provenance-path authentication

The new fixture parses the real current review artifact, changes only `envelope.provenance.artifact_path`, clears its digest, reseals the complete artifact, and writes canonical compact JSON. It therefore remains schema-valid, canonical, content-authenticated, and bound to the expected artifact ID while claiming the wrong authoritative path.

RED:

```text
uv run pytest tests/integration/test_research_stop_inspection.py -k wrong_provenance_path -q
1 failed, 6 deselected in 0.84s
Failed: DID NOT RAISE ValueError
```

GREEN:

```text
1 passed, 6 deselected in 0.83s
```

Single-read review reconstruction now requires `envelope.provenance["artifact_path"]` to equal the exact descriptor-relative `artifacts/design-review-findings.json` path before deriving or returning the review reference.

### Final verification for fix round 2

```text
uv run pytest tests/integration/test_research_stop_inspection.py tests/unit/test_personal_validation_snapshots.py -q
42 passed in 4.86s

uv run pytest tests/integration/test_research_stop_inspection.py tests/unit/test_personal_validation_snapshots.py tests/integration/test_factory_design_resolver.py -q
63 passed in 18.95s

uv run pytest tests/integration/test_research_stop_inspection.py tests/unit/test_personal_validation_snapshots.py tests/integration/test_factory_design_resolver.py tests/integration/test_factory_root_safety.py tests/integration/test_factory_run.py tests/integration/test_research_orchestrator.py tests/integration/test_research_orchestrator_integrity.py tests/integration/test_research_orchestrator_recovery.py -q
117 passed in 50.10s

uv run ruff check src/envresearch/research/stop_contracts.py src/envresearch/research/stop_inspection.py src/envresearch/personal_validation/snapshots.py tests/integration/test_research_stop_inspection.py tests/unit/test_personal_validation_snapshots.py
All checks passed!

uv run mypy src/envresearch/research/stop_contracts.py src/envresearch/research/stop_inspection.py src/envresearch/personal_validation/snapshots.py
Success: no issues found in 3 source files

uv run ruff format --check src/envresearch/research/stop_inspection.py src/envresearch/research/__init__.py src/envresearch/personal_validation/snapshots.py tests/integration/test_research_stop_inspection.py tests/unit/test_personal_validation_snapshots.py
5 files already formatted
```

Final Python line counts are 400 (`snapshots.py`), 385 (`stop_inspection.py`), 392 (snapshot unit test), and 273 (inspection integration test).

### Fix-round concerns

No implementation concerns remain. Independent review was not dispatched because the controller explicitly prohibited subagents/reviewers for this fix round.
