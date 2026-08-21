# Task 5 Implementation Report: Audited Release, CLI, and V0.4 Exit

## Outcome

Task 5 implements `PB-REQ-006`, `PB-REQ-007`, `PB-REQ-009`, and
`PB-REQ-010`. It publishes one current release only after reopening the complete
exact audit, draft, argument, ledger, citation, V0.3.1, raw-output, and revision
ancestry. The stable V1.0 handoff is `(ArtifactRef, PaperReleaseCandidate)`.

The CLI accepts explicit JSON `ArtifactRef` inputs only. `status` is read-only;
exit 2 denotes input/authority/integrity failure, exit 1 an audited non-release,
and exit 0 an exact current-green release. No command scans latest artifacts or
executes an estimator.

## RED to GREEN Evidence

The implementation followed observed TDD checkpoints.

1. Initial release RED failed during collection with
   `ModuleNotFoundError: envresearch.paper.release`.
2. The first process-boundary RED expected death after pending publication
   (exit 82) but observed the old single-phase final-current boundary (exit 83).
3. Pointer-pair attacks initially produced four failures: missing pending,
   missing commit, mismatched pending/commit, and forged marker states were not
   consistently rejected.
4. CLI RED was `4 failed, 1 passed in 2.14s` because `paper.cli` and deterministic
   command output did not exist.
5. The revision-bypass RED was `3 failed in 8.87s`: an unrevisioned generation-2
   draft released, while the build API rejected `revision_ref`.
6. The final ancestry attack was `1 failed, 5 deselected in 5.79s`: blocked
   generation 1, directly promoted blocked generation 2, and a legitimate clean
   generation-2-to-3 revision still released. The completed implementation
   rejects the missing earlier closure and binds the full ordered ancestry.
7. Missing CLI arguments returned exit 2 with empty stdout; the RED failed with
   `JSONDecodeError`. They now return deterministic authority-error JSON.
8. The read-only opener RED failed because `FilesystemWorkerQueue.open_existing`
   did not exist. Its initial GREEN was `1 passed, 7 deselected in 0.89s`.
9. Reviewer read-only/root attacks produced `4 failed, 2 passed, 9 deselected in
   2.63s`: missing locks were recreated, unsafe protected root/directory modes
   were accepted, and a derived control-root alias passed validation. The final
   targeted GREEN was `6 passed, 9 deselected in 3.03s`.

## Release boundary

- `PaperReleaseCandidate` is strict, frozen, canonical, and embeds the complete
  clean audit plus exact transitive, analysis, and output references.
- Later generations bind ordered `revision_refs` and strict nested
  `DraftRevision` payloads; release identity hashes the audit and full ancestry.
- One global lease orders valuation authority, citation authority, ledger, map,
  draft, sorted audit subjects, sorted revision subjects, then release
  publication. Each closure and audit is reconstructed under that lease.
- Immutable release publication uses pending and commit pointers. Only an equal,
  authenticated pair is current. Same-input recovery converges; a different
  pending release is an authority conflict.
- Status never recovers. It reopens current pointers, release bytes, all upstream
  payloads/raw outputs, audit, and revision ancestry and fails closed on drift.

## CLI and protected read-only composition

- `paper build` consumes exact audit/draft JSON and optional exact terminal
  revision JSON; `paper status` consumes one exact release JSON.
- Research and derived protected control roots are opened without mkdir, chmod,
  key minting, anchor creation, lock creation, or pointer/object publication.
- Protected roots and directories require exact euid ownership and mode 0700;
  the key requires euid ownership, mode 0600, regular-file type, and one link.
- V0.3.1, Paper, research, and derived control roots must be physically disjoint.
- Any failure after queue open closes its pinned descriptors.

## Final verification

Focused Task 5 revision, acceptance, process-death, concurrency, CLI, read-only,
and root-attack matrix:

```text
41 passed in 41.84s
```

The final formal gate used the sealed V0.3.1 v3 acceptance root. Unit and
integration coverage were recorded separately and then combined to retain
recoverable evidence:

```text
unit:        1,390 passed
integration: 1,120 passed, 15 expected skips
combined:    2,510 passed, 15 expected skips, 0 failed
coverage:    39,584 / 44,409 statements = 89.1350852304713%
```

The formal threshold was 80%. A rare Pilot-8 false rejection discovered during
this gate was traced to a restricted numeric term matching an opaque random
lineage hash. The corrected `blind-leakage-v2` scanner and recommender now share
one immutable public projection; the exact projection policy is bound into the
scanner configuration digest. Focused blind regressions were 57 passed, and the
independent review returned 0/0/0.

```text
ruff check: All checks passed
ruff format --check: 28 changed Python files already formatted
mypy: Success, no issues found in 338 source files
uv lock --check: resolved 35 packages
git diff --check: clean
line cap: 28 changed Python files, maximum 400 lines
payload scan: 30 non-temp files, no binary/archive/>1 MiB findings
```

Every changed Python file is at most 400 physical lines. The largest are
`citation_attestations.py` (400), `control.py` (399), and the main CLI module
(398). No dependency, estimator, method, data, capability gate, or V0.5--V0.9
milestone was added.

## Independent review

The decisive read-only reviewer reproduced revision ancestry bypass rejection,
missing-lock no-write behavior, unsafe protected metadata rejection, derived
control-root separation, release/current/lock ordering, and canonical
reconstruction. Final verdict: **PASS — 0 Critical, 0 Important, 0 Minor**.

## Operational note

The formal sealed V0.3.1 acceptance root was supplied explicitly for the final
gate. Tests never accessed network, Zotero, an estimator, or external mutable
data. This report records the completed pre-commit state; the controller owns
project-memory, Obsidian, SDD progress, staging, and commit publication.
