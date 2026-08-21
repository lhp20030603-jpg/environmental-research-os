# V1.0 Governed Research Factory Design

**Status:** Implemented and independently reviewed; product scientific release pending
**Date:** 2026-08-14  
**Stable upstream handoff:** one exact current
`(ArtifactRef, PaperReleaseCandidate)` pair from V0.4

## 1. Objective

The first V1.0 slice turns the independently verified V0.1--V0.4 artifact
chain into one governed end-to-end research run. It composes existing design,
analysis, evidence, paper, revision, and release authorities; it does not
replace them.

The slice must make the following statement independently reproducible:

> This exact approved research design is typed-field coherent with this exact
> accepted evidence chain and this exact audited paper release, and an
> independent human made the terminal release decision over that complete
> retrospective context.

The deliverable is a content-addressed `ResearchFactoryRun` plus a separate
human promotion record. Agent work may assemble, reopen, verify, and recover
the run. It may not approve its own promotion.

Existing V0.3.1/V0.4 artifacts do not contain an exact V0.2 design-provenance
edge. This slice therefore makes a retrospective coherence claim, not the
stronger claim that the approved design caused or directly produced the
existing analysis. A future forward-run slice must bind the design reference at
execution intake before it may claim design-to-execution provenance.

## 2. Scope

### 2.1 Included in the first slice

- exact reopening of the approved V0.2 design authority;
- exact reopening of the current V0.4 paper release and its full V0.3.1
  evidence lineage;
- cross-stage coherence checks between design, methods, evidence, and claims;
- immutable run assembly and read-only status;
- deterministic promotion context generation;
- an independent human promotion decision bound to the exact run;
- process-safe publication, idempotent recovery, stale-authority detection,
  and root separation; and
- an exact-reference CLI for assembly, status, and promotion handoff.

### 2.2 Explicitly excluded

- a new estimator, method family, method threshold, dataset, or data source;
- automatic research-question selection or automatic scientific approval;
- hidden-holdout evaluation, benchmark-driven capability-pack promotion, or
  the final V1.0 product exit claim;
- journal submission, external messaging, or irreversible publication;
- path scanning, `latest` discovery, implicit root creation during status, or
  substitution of a newer artifact generation; and
- mutation of any V0.1--V0.4 artifact.

Hidden evaluation is the next V1.0 slice. Until it exists and passes, the
Research Factory may promote an individual exact run for human-authorized use,
but the product remains `scientific_release_pending` and may not claim the
V1.0 roadmap exit.

## 3. Requirements

### RF-REQ-001: Exact inputs

Every public operation receives caller-supplied exact references. The V0.4
input is the exact `ArtifactRef` and reopened `PaperReleaseCandidate`; the V0.2
input is an exact `ApprovedDesignHandoff` described below. No operation searches
for a current or latest object on behalf of the caller.

### RF-REQ-002: Complete authority reconstruction

Assembly and status independently reopen all bound objects and verify their
canonical bytes, identities, current pointers, predecessor chains, and raw
output authorities. Reusing a typed payload cached by an upstream service is
not sufficient unless that service reauthenticates the underlying bytes.

### RF-REQ-003: Cross-stage coherence

The approved design and paper release must agree on the registered question,
estimand or descriptive quantity, method family, target population, unit, time
basis, price basis, and declared limitations wherever those fields exist.
Absence of a required binding is a failure; the factory never invents a
mapping from similar prose.

### RF-REQ-004: No self-approval

The producer principal that assembles a run or requests promotion cannot be the
principal that decides it. A decision is valid only when it is independently
authenticated and bound to the exact run and promotion context.

### RF-REQ-005: Immutable state

Run and promotion artifacts are immutable. `promotion-required`, `promoted`,
and `invalidated` are derived status results, not fields rewritten into the run
object. An upstream change invalidates the derived status without rewriting
history.

### RF-REQ-006: Fail-closed publication

Immutable objects are published before pointers. Prepared and committed
pointers are authenticated separately. A current run or promotion exists only
when both pointers agree and every upstream authority is current. Torn state,
write-then-raise, process death, and a conflicting retry must never expose a
partially validated handoff.

### RF-REQ-007: Read-only status

Status opens existing roots, keys, locks, objects, and pointers without
creating, chmodding, repairing, or promoting anything. Missing control state
is an error. Status must leave byte-for-byte identical public and protected
roots.

### RF-REQ-008: Physical separation

The research, V0.3.1, paper, factory, and derived protected-control roots must
be pairwise non-overlapping after canonical resolution. Same-root,
ancestor/descendant, symlink, and alias layouts are rejected before any write.

### RF-REQ-009: Concurrency and recovery

Identical concurrent builds converge on one exact reference. Conflicting
builds cannot overwrite a prepared or committed intent. A retry after any
supported crash boundary either completes the same exact transaction or fails
closed while leaving the last accepted current run readable.

### RF-REQ-010: Honest release boundary

A promoted individual run is not evidence that the Research Factory itself has
passed hidden evaluation. The V1.0 product exit additionally requires the
roadmap's hidden-suite, capability-pack, replication-package, and zero-critical-
integrity gates.

## 4. Architecture

The implementation adds a small `envresearch.factory` package. It is an
orchestration layer over existing services, not a second scientific kernel.

```text
V0.2 research root
  approved plan + final human gate
              |
              v
       ApprovedDesignHandoff
              |
              | exact coherence checks
              v
V0.4 PaperReleaseCandidate -------------------+
  full revision ancestry                      |
  V0.3.1 transition + accepted outputs        |
              |                               |
              +-------------> ResearchFactoryRun
                                      |
                                      v
                           FactoryPromotionContext
                                      |
                           independent human decision
                                      |
                                      v
                           FactoryRunPromotion
```

The package has four responsibilities:

1. contracts define strict, frozen, canonical handoffs;
2. resolvers reopen exact upstream authorities through public APIs;
3. services assemble, publish, recover, and report derived status; and
4. the CLI transports exact reference JSON without selecting evidence.

## 5. Contracts

### 5.1 `ApprovedDesignHandoff`

V0.2 does not currently expose one content-addressed terminal handoff. V1.0
therefore creates an adapter artifact after reopening, never rewriting, the
existing V0.2 run. It contains:

- schema and producer identities;
- the canonical research-root identity and `ResearchRunManifest` SHA-256;
- the exact approved analysis-plan `ArtifactRef` and typed payload;
- the exact final-gate context and approved `GateRequest` payload;
- the exact terminal checkpoint inputs;
- method-profile IDs, versions, and content digests from the run manifest;
- intake artifact reference and registered question/estimand scope; and
- the decision-log digest needed to replay the terminal approval.

`V02ApprovedDesignResolver.open_exact(root, handoff_ref)` requires the supplied
handoff to be current and reconstructs every embedded authority. The adapter
must not accept a path-only plan or a `GateDecision` detached from its request,
context, principal registry, and decision log.

### 5.2 `ResearchFactoryRun`

The immutable run contains:

- `schema_version="factory.research-run.v1"`;
- a deterministic `factory_run_id` derived from the complete exact input refs;
- producer identity; operational assembly time is recorded only in the
  append-only publication event;
- exact design-handoff reference and strictly revalidated payload;
- exact paper-release reference and strictly revalidated
  `PaperReleaseCandidate`;
- canonical transitive artifact, analysis, and raw-output references;
- a typed cross-stage binding report;
- the supported-archetype and capability-pack identities; and
- `assembly_verdict="assembled"`.

The cross-stage binding report lists every compared field and its source on
both sides. It cannot encode a silent coercion. A field is either an exact
match, an explicitly allowed one-way narrowing, or a typed finding that blocks
assembly.

### 5.3 `FactoryPromotionContext`

The service derives this object; callers cannot write its checklist. It binds:

- the exact run reference and payload digest;
- the revalidated design and release references;
- all open limitations and declared scope boundaries;
- the human-accountability checklist;
- an explicit `hidden_evaluation_status="not-run"` in the first slice;
- the requested decision kind, `individual-run-release`; and
- requester principal. Operational request time is recorded only in the
  authenticated append-only request event.

The context states that an individual decision does not promote the factory
capability pack.

### 5.4 `FactoryRunPromotion`

This immutable record contains the exact context reference and payload, a
terminal approve/reject decision, the independent human principal, rationale,
conditions, and decision time. It is valid only when:

- request and decision principals differ;
- the decision is authenticated by the protected principal registry;
- the exact run and context are current;
- the decision postdates the authenticated request event;
- conditions do not expand the run's scientific scope; and
- the complete authority chain reopens before and after publication.

Rejected contexts are terminal. A later request requires a new context
generation rather than mutation of the rejection.

## 6. Derived state machine

The public `FactoryRunStatus` is reconstructed on every call:

```text
valid assembled run, no terminal decision -> promotion-required
valid assembled run, exact approved decision -> promoted
valid assembled run, exact rejected decision -> promotion-rejected
any bound authority stale/corrupt/missing -> invalidated (error + no handoff)
```

`assembled` is the immutable artifact verdict. The other labels describe live
authority. `invalidated` is reported with a stable finding code and never
returned as an accepted `(ArtifactRef, payload)` handoff.

## 7. Service API

The first slice exposes:

```python
FactoryRunService.assemble(
    design_ref: ArtifactRef,
    release_ref: ArtifactRef,
) -> ArtifactRef

FactoryRunService.status(
    run_ref: ArtifactRef,
) -> FactoryRunStatus

FactoryRunService.request_promotion(
    run_ref: ArtifactRef,
    requested_by: str,
) -> ArtifactRef

FactoryRunService.record_promotion(
    context_ref: ArtifactRef,
    decision: GateDecision,
    principal_capability: str,
) -> ArtifactRef

FactoryRunService.promotion_status(
    promotion_ref: ArtifactRef,
    run_ref: ArtifactRef,
) -> FactoryRunStatus
```

The service constructor receives exact-root resolvers and protected principal
authority. It does not accept dictionaries, mutable payloads, default roots, or
an estimator callback.

## 8. Authority and lock order

All mutating operations hold one fixed composite lease for their full success
window. The order is:

1. V0.2 research/final-gate authority;
2. V0.3.1 global valuation authority;
3. citation protected authority;
4. paper ledger, map, draft, audit, revision, and release subjects;
5. approved-design-handoff subject;
6. factory-run subject; and
7. promotion-context and promotion subjects.

Subjects within one tier are sorted by canonical full-reference digest. Public
upstream services need package-private already-locked reopen functions where a
public method would reacquire a non-reentrant file lock. No lower-tier writer
may acquire a higher-tier lock.

Status acquires equivalent read authority through open-existing leases. A
missing lock is not created. Locks serialize validation and pointer promotion;
they do not substitute for canonical byte, hash, identity, or current checks.

## 9. Publication and recovery

Each current authority uses two authenticated subjects:

- `prepared`: exact transaction intent; and
- `committed`: the accepted current reference.

The sequence is immutable object publication, prepared pointer, complete
reconstruction, committed pointer, final reconstruction while all writer
leases remain held, then return. If the final check fails, compare-and-restore
may remove only the pointer installed by the current transaction. It cannot
overwrite a different writer's current state.

Recovery rules:

- same prepared candidate: reopen and resume the same exact transaction;
- different candidate while an authenticated intent exists: authority error;
- object-only orphan: reuse only if canonical bytes and identity match;
- prepared-only state: invisible to status and recoverable by the same intent;
- committed-without-matching-prepared: integrity error;
- write-then-raise: read back under lock and accept only the exact intended
  value; and
- crash after commit: retry/status must reconstruct the complete transaction
  before exposing it.

## 10. Cross-stage coherence rules

The first slice compares only typed fields that already exist. It rejects:

- a paper evidence method absent from the approved method-profile closure;
- an estimand, descriptive quantity, population, unit, time, or price basis
  broader than the approved design;
- a paper claim strength broader than the registered identification scope;
- a release whose transition, outputs, or revision ancestry is stale;
- a plan or gate whose approved context is no longer current;
- a claim whose source cannot be traced to accepted raw output bytes; and
- a release limitation omitted from the promotion context.

The factory may accept an explicitly narrower paper claim when the narrowing
is represented by a typed relation and the original limitation remains in the
promotion context. It does not use natural-language similarity to decide
coherence.

The report must label the relationship `retrospective-coherence`; neither its
schema nor its prose may use `produced-by`, `executed-from`, or an equivalent
forward-provenance claim for the existing V0.4 release.

## 11. Error vocabulary

The public stable codes are deliberately small:

- `FACTORY_AUTHORITY_INVALID`: stale, superseded, non-current, wrong-root, or
  independently unauthenticated authority;
- `FACTORY_INTEGRITY_INVALID`: bytes, hash, canonical form, pointer, recovery,
  or typed reconstruction failure;
- `FACTORY_SUPPORT_INVALID`: missing, contradictory, or incomplete design-to-
  evidence support;
- `FACTORY_SCOPE_EXCEEDED`: scientific or policy scope broader than the
  approved design/evidence;
- `FACTORY_PROMOTION_REQUIRED`: a valid assembled run lacks an approved exact
  human decision; and
- `FACTORY_PROMOTION_REJECTED`: the current exact context has a terminal human
  rejection.

Finding kind and exact target details remain typed fields rather than new error
codes.

## 12. CLI

The CLI adds an `envresearch factory` group:

- `assemble DESIGN_REFERENCE RELEASE_REFERENCE`;
- `status RUN_REFERENCE`;
- `request-promotion RUN_REFERENCE --requested-by PRINCIPAL`;
- `record-promotion CONTEXT_REFERENCE RUN_REFERENCE DECISION
  --principal-capability-file FILE`; and
- `promotion-status PROMOTION_REFERENCE RUN_REFERENCE`.

Every command requires exactly four explicit root options: `--research-root`,
`--v031-root`, `--paper-root`, and `--factory-root`. The design and citation
authorities and both required sibling worker-queue controls are derived
deterministically from `--research-root`; they are not additional CLI root
options. Reference files contain only exact `ArtifactRef` JSON. Outputs are
deterministic JSON with stable error code and finding kind. Missing arguments,
unsafe roots, and unavailable authorities fail without filesystem mutation.

## 13. Acceptance matrix

The first implementation plan must include at least:

- one real V0.2-approved-design to V0.4-release assembled run;
- an exact promotion request and independent approved/rejected decision;
- requester/decider identity collision;
- missing, stale, superseded, or mutated V0.2 plan/final gate;
- stale V0.3.1 transition, raw output, audit, revision hop, or release;
- method, estimand, unit, population, time, price, limitation, and strength
  mismatches;
- same/ancestor/descendant/symlink root overlaps, including derived protected
  roots;
- object, prepared-pointer, committed-pointer, and final-window failures;
- synchronous before/after-write exceptions and process death at each boundary;
- identical and conflicting multiprocess assembly and promotion;
- real upstream writer contention under the full lock order;
- read-only status snapshots proving no file, mode, key, lock, or pointer
  mutation;
- nested-model coercion, noncanonical JSON, wrong artifact ID/version/hash, and
  transitive-lineage omission; and
- backward compatibility for V0.1--V0.4 artifacts.

Tests use repository-owned synthetic fixtures for normal CI and retain a
separate formal test against the sealed V0.3.1/V0.4 acceptance root.

## 14. Exit criteria for this slice

The governed-run slice is complete when:

1. a current V0.2 approved design and current V0.4 release assemble into one
   exact, independently reopenable `ResearchFactoryRun`;
2. a separate human principal can approve or reject an exact promotion context;
3. every stale, corrupt, conflicting, or scientifically broader composition
   fails closed without a partial current handoff;
4. status is read-only and all supported crash/concurrency paths are
   deterministic;
5. the complete affected test suite and independent review report zero Critical
   and zero Important findings; and
6. documentation states that hidden evaluation and product-level V1.0
   scientific promotion remain pending.

This slice does not satisfy the full V1.0 roadmap exit. The next design slice
must add hidden evaluation, benchmark-controlled capability-pack evolution,
and replication-package evaluation before the Research Factory may claim
90/10 supported-paper autonomy.

## 15. Planned implementation boundaries

Implementation should proceed in independently reviewable slices:

1. strict contracts and exact V0.2 design resolver;
2. factory-run assembly, status, coherence findings, and recovery;
3. promotion context and protected independent decision;
4. exact-reference CLI, process concurrency, and read-only root safety; and
5. formal acceptance, documentation, and V1.0-next-slice handoff.

Detailed file ownership, RED tests, commands, and commit boundaries belong in
the implementation plan written only after this design is approved.

## 16. Implementation status

The governed-run slice in Sections 1--15 is implemented. Tasks 1--5 deliver
the exact approved-design adapter, retrospective design-to-release coherence,
immutable run assembly, independent human promotion, reference-only CLI,
process recovery, and acceptance surface without changing upstream scientific
methods or accepted evidence.

The final post-fix evidence is:

- scoped pre-document reviews: 0 Critical, 0 Important, 0 Minor; final
  documentation review remains pending;
- affected Task 1--4: 216 passed, 1 skipped, 11 warnings in 620.82s;
- unit: 1,440 passed, 17 warnings in 15.48s;
- integration: 1,264 passed, 39 skipped, 27 warnings in 1,046.39s;
- combined coverage: 2,704 passed, 39 skipped, 45 warnings in 2,019.88s;
  58,955 statements, 3,989 missed, 93.233822%; and
- Ruff, Mypy, lock, diff, changed-file format, 400-line, payload, and process
  gates passed.

The formal sealed-root acceptance branch did not run because
`ENVRESEARCH_V04_ACCEPTANCE_ROOT` was not supplied. Its environment skip is
retained as an explicit evidence limit, not reported as a formal pass. The
synthetic connected acceptance path and all always-running authority,
integrity, recovery, concurrency, and CLI tests passed.

Operational instructions are in
`docs/research-factory-v1-operator-guide.md`. The slice satisfies the governed
individual-run boundary, while RF-REQ-010 remains decisive: product state is
`scientific_release_pending` until the next V1.0 slice completes hidden
evaluation, capability-pack evolution, and replication-package evaluation.
