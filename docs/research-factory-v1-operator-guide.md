# Research Factory V1.0 Governed-Run Operator Guide

This guide operates the first V1.0 slice: one exact approved V0.2 design and
one exact current V0.4 paper release are assembled into an immutable
`ResearchFactoryRun`, then handed to an independent human for an
individual-run decision.

This slice does not run hidden evaluation or promote the Research Factory
product. Even an approved run retains `hidden_evaluation_status="not-run"` and
`product_release_status="scientific_release_pending"`.

## Required sealed inputs

Use an operator-reviewed directory with this interface:

```text
acceptance-root/
├── design-reference.json
├── release-reference.json
├── research/
├── v031/
├── paper/
└── factory/
```

Each reference file contains only one strict `ArtifactRef` JSON object:

```json
{"artifact_id":"...","artifact_version":1,"content_hash":"<64 lowercase hex characters>"}
```

Do not replace the fields with a path or select a newer object. The design
reference must identify the current `approved-design-handoff` version 1 in the
supplied factory root. The release reference must identify the exact current
`PaperReleaseCandidate` in the supplied paper root. Preserve the complete
reference returned by each command as the next command's input.

No sealed `design-reference.json` or `release-reference.json` was supplied to
the final repository gate run, so this guide does not publish invented
reference hashes. The environment-gated formal case remains pending until an
operator supplies `ENVRESEARCH_V04_ACCEPTANCE_ROOT`.

## Four-root and protected-control layout

The CLI requires four existing absolute roots:

- `research`: the reviewed research envelope;
- `v031`: the reviewed V0.3.1 accepted-evidence root;
- `paper`: the reviewed V0.4 Paper Builder registry;
- `factory`: the V1.0 handoff, run, event, and promotion registry.

The research envelope contains two authorities and their derived controls:

```text
research/
├── design/
├── .design.worker-queue-control/
└── citation/
    ├── research/
    └── .research.worker-queue-control/
```

The four supplied roots must be pairwise separate. Within the research
envelope, both derived controls and both research authorities must be pairwise
separate from one another and from `v031`, `paper`, `factory`, and the
registered citation-source roots. Equal protected roots, protected-root
nesting, symlinks, aliases, missing directories, or same-path inode replacement
fail closed.

For a sealed acceptance run, snapshot and copy all four reviewed roots first.
Run mutating commands only against those copies. Promotion also writes
protected request/decision anchors beneath the copied design control, so
copying only `factory/` is insufficient. Keep `design-reference.json` and
`release-reference.json` read-only.

## Preserve exact references

Successful mutating commands emit one canonical JSON object with `reference`,
`payload`, and `status`. `assemble` returns the exact `ResearchFactoryRun`,
`request-promotion` returns the exact reopened `FactoryPromotionContext`, and
an approved `record-promotion` returns the exact reopened
`FactoryRunPromotion`. Save the whole response for audit, then extract only
`reference` into the next exact-reference file:

```bash
uv run python -c \
  'import json,sys; print(json.dumps(json.load(sys.stdin)["reference"], separators=(",", ":"), sort_keys=True))' \
  < assemble-output.json > run-reference.json
```

Use the same extraction for `context-reference.json` from
`request-output.json` and `promotion-reference.json` from
`promotion-output.json`. Never edit a returned artifact ID, version, or hash.

## Commands

The examples below use copied roots at `/absolute/copied`. Replace that prefix
with the actual reviewed copy location; keep the five JSON filenames exact.

### 1. Assemble

```bash
uv run envresearch factory assemble \
  design-reference.json release-reference.json \
  --research-root /absolute/copied/research \
  --v031-root /absolute/copied/v031 \
  --paper-root /absolute/copied/paper \
  --factory-root /absolute/copied/factory \
  > assemble-output.json
```

On success, preserve the emitted `reference` as `run-reference.json`. The
returned state is `promotion-required`.

### 2. Read run status

```bash
uv run envresearch factory status \
  run-reference.json \
  --research-root /absolute/copied/research \
  --v031-root /absolute/copied/v031 \
  --paper-root /absolute/copied/paper \
  --factory-root /absolute/copied/factory
```

A valid assembled run intentionally returns exit 1 with
`FACTORY_PROMOTION_REQUIRED`. This command does not create or recover state.

### 3. Request promotion

```bash
uv run envresearch factory request-promotion \
  run-reference.json \
  --requested-by factory-agent \
  --research-root /absolute/copied/research \
  --v031-root /absolute/copied/v031 \
  --paper-root /absolute/copied/paper \
  --factory-root /absolute/copied/factory \
  > request-output.json
```

Use the canonical requester principal assigned to the run. The payload is the
exact reopened `FactoryPromotionContext`; preserve the emitted `reference` as
`context-reference.json`.

### 4. Record the independent decision

Create `decision.json` only after reviewing the exact context. It is a strict
`GateDecision` with terminal `status` (`approved` or `rejected`), the canonical
`decided_by` principal, a rationale, an optional conditions object that can
only narrow scope, and an aware UTC `decided_at` later than the request. The
decider must match the existing protected gate assignment and must differ from
the requester, run producer, and contributing worker principals. Unknown
decision fields are rejected at the CLI boundary with exit 2 and
`finding_kind="decision-input-invalid"`.

Pass the existing protected capability file; do not copy its secret into the
decision or logs:

```bash
uv run envresearch factory record-promotion \
  context-reference.json run-reference.json decision.json \
  --principal-capability-file \
    /absolute/copied/research/.design.worker-queue-control/principals/gate.capability \
  --research-root /absolute/copied/research \
  --v031-root /absolute/copied/v031 \
  --paper-root /absolute/copied/paper \
  --factory-root /absolute/copied/factory \
  > promotion-output.json
```

An approval exits 0 with the exact reopened `FactoryRunPromotion` as payload;
preserve its emitted `reference` as `promotion-reference.json`. A rejection is
durably terminal but returns exit 1 with `FACTORY_PROMOTION_REJECTED` alongside
the same exact promotion `reference`, `payload`, and `status`. Extract that
reference and pass it directly to `promotion-status`; rerunning either exact
command confirms the same outcome without creating a different decision.

### 5. Read terminal promotion status

```bash
uv run envresearch factory promotion-status \
  promotion-reference.json run-reference.json \
  --research-root /absolute/copied/research \
  --v031-root /absolute/copied/v031 \
  --paper-root /absolute/copied/paper \
  --factory-root /absolute/copied/factory
```

An approved current exact promotion returns state `promoted` and exit 0. A
rejected exact promotion returns `FACTORY_PROMOTION_REJECTED` and exit 1 while
retaining the exact promotion `reference`, `payload`, and `status` in JSON.
Status never repairs a missing pointer, event, lock, assignment, capability, or
authority root.

## Deterministic exit contract

| Exit | Meaning |
|---|---|
| `0` | The requested mutation or approved terminal reopen succeeded. |
| `1` | Expected scientific workflow state: promotion is required or the independent decision rejected the exact run. |
| `2` | CLI input, authority, integrity, support, scope, root, pointer, recovery, or reconstruction failed. |

Every exit emits JSON. Stable error codes are
`FACTORY_AUTHORITY_INVALID`, `FACTORY_INTEGRITY_INVALID`,
`FACTORY_SUPPORT_INVALID`, `FACTORY_SCOPE_EXCEEDED`,
`FACTORY_PROMOTION_REQUIRED`, and `FACTORY_PROMOTION_REJECTED`.
`finding_kind` supplies the narrower diagnosis.

## Recovery

After interruption of `assemble`, `request-promotion`, or `record-promotion`,
rerun the same command with the same exact references, requester, decision,
capability, and roots. Object publication and prepared/committed pointers bind
the durable intent; identical retries converge and conflicting retries fail
closed. Factory authority events publish atomically, so process death during
event bytes leaves the last complete JSONL history recoverable.

Do not delete or edit objects, pointers, `factory-events.jsonl`, protected
request/decision anchors, assignments, capabilities, or lock files. A missing
or changed authority is a forensic failure, not a request for status to heal
it. Use `status` or `promotion-status` only after the exact mutating retry has
completed.

## Read-only expectations

`status` and `promotion-status` open existing roots and protected controls.
They do not create directories, locks, keys, objects, events, capabilities, or
pointers; do not chmod state; and do not complete prepared transactions. They
reopen canonical bytes, current pointers, upstream evidence and release
lineage, principal authority, event anchors, and pinned physical root
identities. Any stale or substituted authority fails closed.

## Promotion boundary and next slice

`FactoryRunPromotion` authorizes only one exact retrospective-coherence run.
It does not show that the approved design caused the historical execution, and
it does not change product state from `scientific_release_pending`.

The next V1.0 slice must separately implement and pass:

1. hidden evaluation on held-out cases;
2. benchmark-controlled capability-pack evolution; and
3. replication-package evaluation under its own access, license, provenance,
   runtime, and scientific gates.

Only that later product-level evidence can support a V1.0 scientific release
claim.

## Current repository evidence

The last full post-fix gates before final documentation review recorded:

- affected Task 1--4: 216 passed, 1 skipped, 11 warnings in 620.82s;
- unit: 1,440 passed, 17 warnings in 15.48s;
- integration: 1,264 passed, 39 skipped, 27 warnings in 1,046.39s;
- combined coverage: 2,704 passed, 39 skipped, 45 warnings in 2,019.88s;
  58,955 statements, 3,989 missed, 93.233822%; and
- scoped pre-document reviews: 0 Critical, 0 Important, 0 Minor; final
  documentation review is pending.

After the promotion handoff and strict decision-input review fixes, the focused
CLI/promotion/acceptance suite passed 32 tests with 1 honest environment skip
in 142.57s, and the affected factory suite passed 219 tests with 1 honest skip
and 11 warnings in 836.78s. Per the review protocol, the full unit,
integration, and coverage gates were not repeated before documentation
re-review.

The formal sealed-root acceptance case is one honest environment skip because
`ENVRESEARCH_V04_ACCEPTANCE_ROOT` was absent. These results establish the
implemented governed-run slice, not the pending product-level scientific
release.
