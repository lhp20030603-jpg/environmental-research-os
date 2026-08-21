# Personal Usability Validation Design

> **Personal Pilot implementation note (2026-08-21):** The foundational
> canonical-case and three-role advisory review/report path is implemented
> through plan Task 6. The owner intentionally deferred the public Personal
> Validation CLI, automated repair closure, extended Agent dry run, and final
> validation baseline (Tasks 7–10). Core Research/Econometrics/Paper/Factory use
> does not depend on those deferred features.

**Status:** Approved for implementation; independent specification review passed
**Date:** 2026-08-21
**Product boundary:** Personal advisory validation only; Research Factory remains `scientific_release_pending`

## 1. Purpose

The Research Factory is implemented and can support supervised personal
research, but the owner does not yet have a real dataset and does not need a
formal 16-case hidden evaluation. The immediate goal is to finish a useful
personal workflow without pretending that internal Agent review is independent
human validation or product-level scientific release.

This slice adds a thin, repeatable Personal Usability Validation layer over the
existing Research Factory. It runs four repository-owned canonical cases,
freezes exact artifacts for review, dispatches two role-separated Reviewer
Agents followed by one Synthesis Reviewer Agent, reports concrete problems, and
supports user-approved repair and rerun.

Testing is advisory. It never disables the system or a method family. A failed
case remains usable under owner judgment, but it cannot be labeled a passed
personal baseline while a material finding remains open. An explicitly
accepted unresolved issue remains visible as `needs-revision` or
`review-required`; owner acceptance does not erase the finding.

## 2. Goals

The slice shall:

1. provide a one-command personal usability check over four canonical research
   behaviors;
2. bind every review to one exact completed-run or correct-stop target, input
   snapshot, system snapshot, protocol, and Reviewer role;
3. use Scientific, Evidence, and Synthesis Reviewer Agents instead of requiring
   the owner to score every case;
4. produce structured, non-compensatory findings rather than a weighted score;
5. explain problems and propose repairs without silently modifying scientific
   choices or paper artifacts;
6. preserve before/after evidence for every approved repair;
7. keep validation artifacts in an owner-private root outside Git and Obsidian;
8. allow necessary, read-only network or Zotero verification with complete
   provenance; and
9. leave product release, hidden evaluation, capability-pack promotion, and
   replication-package evaluation unchanged.

## 3. Non-goals

This slice does not:

- execute formal held-out evaluation;
- create expert or adjudicator principals;
- claim independent human review;
- calculate a product score, success percentage, family mean, or release
  verdict;
- call or weaken the formal blind `ReleaseEvaluator`;
- acquire a real dataset or invent empirical results;
- publish, submit, message, pay for, upload, or mutate an external service;
- automatically modify an estimand, method, data, analysis configuration,
  numerical result, claim, or citation;
- promote a capability pack or regression fixture; or
- change `hidden_evaluation_status="not-run"` or
  `product_release_status="scientific_release_pending"`.

Formal hidden evaluation, benchmark-controlled capability-pack evolution, and
replication-package evaluation remain separate future slices. Real-data
validation begins only when the owner starts an actual project and approves a
specific data source after access, license, provenance, and cost review.

## 4. Design principles

### 4.1 Thin adapter

Personal validation is a separate adapter over current Factory, Paper Builder,
and benchmark read interfaces. It does not fork their artifact models,
authority services, or release semantics.

### 4.2 Diagnostic, not blocking

Validation results guide owner decisions but never disable commands, methods,
packs, or the Factory. An unresolved finding remains visible in the report. The
owner decides whether to use the affected output and which repairs to approve.

### 4.3 Exact evidence before prose

Every finding binds an exact artifact or review-bundle location. Reviewer prose
without exact input and evidence references cannot become a durable finding.

### 4.4 No composite score

Method, identification, data, evidence, and citation defects are
non-compensatory. Paper usefulness cannot offset a scientific defect. The
protocol reports findings and case states, not a weighted average.

### 4.5 Immutable repair history

An approved repair creates a successor attempt. Original attempts, reviews,
and findings remain readable. Repair closure is evidence about a changed
version, never an overwrite of the prior record.

## 5. Architecture

```text
four canonical case manifests
             |
             v
existing Research and Factory services
             |
             v
exact frozen PersonalValidationAttempt
             |
             v
         ReviewBundle
        /            \
       v              v
Scientific Agent   Evidence Agent
        \            /
         v          v
        Synthesis Reviewer Agent
                 |
                 v
      PersonalValidationReport
                 |
                 v
optional owner-approved repair -> successor attempt
```

The Python package is isolated from the already-large Factory modules:

```text
src/envresearch/personal_validation/
  __init__.py
  contracts.py
  private_store.py
  review_bundle.py
  report.py
  service.py
  cli.py
```

The personal-validation domain receives an injected `FactoryRunService` and
read-only Research inspection interfaces. Only `personal_validation.cli`
composes explicit filesystem roots; it may reuse the Factory CLI composition
adapter but does not move that adapter into the Factory domain API.

Execution has two explicit modes:

1. **Existing governed target.** The service opens every authority with
   `create=False`, calls read-only status/inspection operations, and never
   assembles, recovers, or changes Factory/Research/Paper/product state.
2. **New canonical run or rerun.** All mutable Research, Paper, and Factory
   roots are disposable per-attempt roots beneath the owner-private validation
   root. They may use existing public mutation services, but they are
   validation-only and can never be supplied as product-authority roots.

The authoritative product roots are snapshotted byte-for-byte and
metadata-for-metadata before and after validation. Writes are permitted only
inside the disposable attempt root. A case that correctly stops before Factory
assembly binds a research-owned read-only stop inspection instead of
fabricating a `ResearchFactoryRun`.

Codex orchestration launches Reviewer Agents. The Python package does not embed
a model SDK. It prepares strict bundles, validates returned review JSON, and
publishes immutable local reports.

## 6. Canonical case set

The first protocol generation contains exactly four repository-owned cases.
They test behavior rather than claim method-family coverage.

### 6.1 Successful end-to-end

A supported synthetic or trusted-local fixture has a clear estimand, compatible
data, a defensible method, reconstructed analysis evidence, and paper
artifacts. The expected behavior is a useful, evidence-bound result with no
material scientific or integrity finding.

### 6.2 Correct stop

The case lacks sufficient identification or data support. The expected behavior
is an explicit stop, limitation, or request for missing evidence. Choosing a
plausible method and fabricating a result is a failure. A correct stop is a
successful personal baseline outcome.

### 6.3 Data/method incompatibility

The case appears runnable but violates a material support condition such as
variation, timing, sample, overlap, instrument, bandwidth, or diagnostic
requirements. The expected behavior is exact detection and a useful data or
design repair proposal.

### 6.4 Evidence/citation challenge

The case contains a deliberate citation, number, table, claim-strength, or
source mismatch. The expected behavior is exact localization, scientific
impact classification, and a repair proposal that does not invent replacement
evidence.

Case inputs and expected behavior are immutable repository fixtures. A failed
observed output is never copied into expected results. Changing a canonical
case requires a new protocol version and explicit review.

## 7. Contracts

All durable models are strict, frozen, canonical, and reject unknown fields.

### 7.1 `PersonalValidationProtocol`

Fields include:

- schema and protocol version;
- exactly four ordered canonical case references and case kinds;
- Scientific, Evidence, and Synthesis review policy digests;
- rubric and report-schema digests;
- network/Zotero policy digest;
- literal `scope="personal-advisory-only"`;
- literal `blocks=()`;
- literal `hidden_evaluation_status="not-run"`; and
- literal `product_release_status="scientific_release_pending"`.

### 7.2 `InputSnapshot` and `SystemSnapshot`

`InputSnapshot` is a canonical manifest over every executed case input byte,
fixture, bound configuration, data/schema file, and expected-behavior contract.
It records path-independent logical names, content digests, file kinds, modes,
symlink targets, and submodule/object identities where applicable. An attempt
and every repair closure bind the same exact `InputSnapshot` ref; changing an
input creates a different case/protocol generation, not a repair.

The snapshot identifies the version being compared, without becoming a formal
product candidate authority:

- Git commit;
- canonical execution-tree inventory covering tracked changes, untracked
  execution files, modes, symlinks, submodules, fixtures, and effective
  configuration;
- `uv.lock` digest;
- sorted capability-pack manifest and method-profile digests;
- protocol, policy, rubric, and schema digests; and
- runtime versions required to reproduce the canonical case.

A dirty tree is permitted for development attempts but is explicitly visible.
Only a clean snapshot may be labeled `personal-baseline-passed`.

### 7.3 `PersonalCanonicalCase`

The case binds:

- stable case ID and one of the four canonical kinds;
- exact input and expected-behavior references;
- intended use;
- allowed synthetic/trusted-local data boundary;
- required Factory stages;
- expected terminal kind `factory-run | correct-stop`;
- prohibited claims or outcomes; and
- expected correct-stop or success behavior.

### 7.4 `PersonalValidationAttempt`

An attempt binds:

- protocol and case refs;
- exact `InputSnapshot` ref;
- exact `SystemSnapshot` ref;
- one strict `AttemptTarget` discriminated union;
- exact start-event ref; and
- predecessor attempt ref when the attempt follows an approved repair.

Artifact construction is a one-way DAG: start event -> Attempt object ->
completion event -> ReviewBundle -> ReviewAssignment -> AgentReview -> report
and optional repair closure. The completion event binds the Attempt ref, and
the ReviewBundle binds the Attempt ref. The Attempt never binds its completion
event or ReviewBundle, so no content-address cycle or forward reference exists.

`AttemptTarget` is exactly one of:

- `CompletedFactoryRunTarget`, which binds only the exact run ref and the
  freshly reopened `ResearchFactoryRun`. Design, release, analysis, evidence,
  and paper refs are derived from that canonical run closure and must equal any
  report convenience fields; callers never supply them independently; or
- `CorrectStopTarget`, which binds an exact `ResearchStopInspection` ref and an
  exact attempt-root inventory covering the Research, Paper, and Factory
  namespaces.
  `ResearchStopInspection` is produced by a research-owned, strictly read-only
  interface and reconstructs the terminal gate/finding/checkpoint state. The
  Personal validator independently derives absence of a Factory run, Factory
  current pointer, Paper result, and empirical-result artifact from the exact
  cross-root inventory. Caller prose or a boolean assertion cannot establish a
  correct stop.

The case contract determines which target kind is expected. A data/method
incompatibility case may use `CorrectStopTarget`; a challenge intended to test
downstream audit detection uses `CompletedFactoryRunTarget`.

### 7.5 `ReviewAssignment` and `AgentReview`

Before dispatch, the service issues one immutable, role-specific
`ReviewAssignment` per attempt and bundle. It binds the attempt, bundle, policy,
role, and unique invocation ID/nonce. This is dispatch provenance only; it is
not a human identity, signature, expert principal, or independent blind-review
authority.

An Agent review binds:

- exact `ReviewAssignment` ref and raw response digest;
- exact attempt and ReviewBundle refs;
- reviewer role;
- model and runtime identity;
- review-policy digest;
- ordered findings;
- external-access records; and
- completion status.

Reviewer roles are `scientific`, `evidence`, and `synthesis`. The service
rejects replay, cross-role reuse, duplicate assignments, and substituted
attempts/bundles. A “forged review” means invalid schema/canonical content,
wrong content hash, wrong assignment/attempt/bundle/role/policy binding, or
duplicate/replayed dispatch. It does not mean cryptographically proving model
or human authorship and must not recreate the blind principal subsystem.

### 7.6 `PersonalFinding`

Each finding includes:

- stable full-digest finding ID;
- domain;
- severity `minor | important | critical`;
- exact target and evidence refs;
- problem statement;
- scientific or writing impact;
- repair proposal;
- source Reviewer refs.

Findings are immutable and initially unresolved; they carry no mutable status.
Resolution is represented only by a separate `RepairClosure` keyed by the
original finding ref.

Domains cover research question/estimand, method/identification, data
compatibility, assumptions/threats, diagnostics/robustness,
evidence/numbers/citations, and paper usefulness.

### 7.7 `PersonalValidationReport`

The report binds all three reviews, preserves disagreement, carries the literal
personal-only and product-pending fields, and records one advisory case state:

- `personal-baseline-passed`;
- `review-required`; or
- `needs-revision`.

No report field represents product release, method availability, or an
aggregate suite score.

### 7.8 Repair contracts

`RepairProposal` binds the exact failed attempt, exact finding IDs, proposed
changes, affected targets, expected verification, and scientific fields that
must remain unchanged.

`RepairApproval` is a local owner decision over one exact proposal. A narrow
local approval-capture operation records this artifact only after explicit
owner consent. The repair executor can consume an approval but cannot create
one. It does not reuse Factory promotion or human-expert authority.

`RepairClosure` binds the before attempt, approval, successor attempt, exact
rerun evidence, and every original finding ref with a resolution
`closed | limited | reopened`, plus new finding refs. An old finding cannot
disappear from a successor report without an exact closure entry.

## 8. Deterministic status semantics

Case state is reduced from the immutable findings and review state:

- `needs-revision`: the union of all three Agent reviews contains at least one
  unresolved Critical or Important known defect;
- `review-required`: Reviewer disagreement, insufficient evidence, incomplete
  review, or `external-verification-pending` prevents a conclusion; and
- `personal-baseline-passed`: all three reviews are complete, the expected
  canonical behavior occurred, and no unresolved Critical or Important finding
  remains.

Minor findings may remain in a passed personal baseline. A correct refusal or
stop may pass when it exactly matches the case contract.

Every finding is unresolved unless an exact `RepairClosure` marks it `closed`
or `limited` and binds verified successor/rerun evidence. `reopened` remains
unresolved. Synthesis cannot downgrade, delete,
deduplicate away, or close a Critical/Important finding from either primary
Reviewer. It may merge equivalent findings only when every source finding ref
remains bound into the merged record.

A disclosed limitation closes a material finding only when exact successor
artifacts narrow the claim or intended use enough that the defect is no longer
material, and the rerun verifies that boundary. Disclosure text alone is not
closure.

These states never block commands. They describe what the owner should inspect.
The service exposes no method-disable or product-release operation.

## 9. ReviewBundle and Agent panel

`prepare()` reopens the exact attempt target and projects only review-relevant
material. Completed targets are reopened through Factory status; stop targets
are reconstructed through `ResearchStopInspection`. The bundle
contains:

- research question and estimand;
- method selection and alternatives;
- data schema, support, and diagnostics;
- assumptions, threats, falsification, and robustness;
- analysis outputs and exact evidence lineage;
- citations, numbers, tables, figures, and claims;
- paper artifacts; and
- the behavioral contract necessary to judge the case.

Scientific and Evidence bundles omit seeded defect locations, the full oracle,
prior reviews, prior findings, and predecessor repair answers. Deterministic
evaluation and Synthesis may compare their immutable findings against the full
canonical oracle only after both primary reviews are published. Repair reruns
use fresh projections with the same exclusions.

The Scientific and Evidence Reviewer Agents run concurrently. Neither receives
the producer transcript or the other review.

The Scientific Reviewer covers question/estimand, method/identification, data
compatibility, assumptions/threats, and diagnostics/robustness.

The Evidence Reviewer covers lineage, numerical consistency, tables/figures,
citations, reproducibility, and claim strength.

The Synthesis Reviewer runs only after both reviews are immutable. It receives
the original bundle and both reviews. It may deduplicate findings while
retaining every source Reviewer ref. It must preserve material disagreement and
cannot downgrade, close, or silently delete a primary finding. Case status is
computed over the complete union after synthesis, not over the synthesis output
alone. It also reports paper usefulness as a descriptive outcome that cannot
compensate for a core defect.

## 10. Network and Zotero policy

Review is local-first. A Reviewer Agent may proactively make a read-only
network or Zotero call only when missing external evidence can materially alter
a finding.

Allowed uses include:

- citation metadata and DOI verification;
- reading an already-authorized Zotero paper;
- checking whether a source supports a claim;
- official method or software documentation; and
- data-source, license, provenance, or version verification.

The Reviewer must minimize the query and must not upload raw data, private-root
content, or a complete unpublished draft. Each external access record binds
provider/tool, exact allowlisted operation, canonical request digest, source
locator or Zotero item key, an authorization ref when applicable,
response/evidence digest, retrieval time, policy ref, and finding IDs that use
it. Unknown or mutating operations are rejected before dispatch. Request
material is scanned to exclude private-root paths and seeded private values.

Network failure yields `external-verification-pending`, not a guessed result.
Zotero mutation, external upload, paid acquisition, messaging, or any other
external state change is always rejected inside this protocol. If such an
operation is ever needed, the current validation session ends and a separate
workflow obtains new owner authorization; that operation is not represented as
Personal Validation external access.

## 11. Private storage and recovery

The validation root is an explicit absolute local path outside the repository,
Git common directory, every linked worktree, and the Obsidian vault. It is
owner-only and opened through the existing private pinned-root contract.

Raw case artifacts, prompts, Agent reviews, reports, logs, paths, and private
digests remain in that root. CLI output contains only case IDs, artifact refs,
finding codes, advisory states, and redacted messages.

The store composes existing generic authority/storage primitives rather than
reimplementing them:

- `PinnedRoot(private=True)` for the owner-only root;
- the canonical object model extracted from `ExitRegistry`, operating through
  a retained pinned directory descriptor rather than reopening a lexical path;
  this may be implemented by injecting a pinned storage backend into
  `ExitRegistry`; and
- `SecureJournal` for authenticated append-only session events.

Composition passes explicit, separate `storage_root` and `control_root`
directories beneath the validated private root, for example `objects/` and
`control/journal/`. Both are owner-only and descriptor-pinned. The default
`SecureJournal` sibling control-root derivation is forbidden because it would
place keys, heads, locks, or private digests outside the declared validation
boundary. Both roots receive the same physical-overlap, root-swap, and
read-only zero-write checks.

If a small adapter is required, it extracts/reuses those generic primitives;
it does not create parallel hashing, locking, object, or journal machinery.
All object and journal operations use the retained pinned root identity; a
lexical root replacement after open must fail closed rather than redirect I/O.
The storage package must first expose
`SecureJournal.open_existing(..., reconcile=False)` (or an equivalent named
seam) for `status` and `report`. Missing key/head, a lagging head, truncation,
or an orphan is reported as incomplete/corrupt without any write. Existing
recovering readers may run only in explicit mutation/recovery operations,
never during a read-only command. Personal Validation does not implement a
second journal parser.
Each canonical event ID is derived from session, operation, object ref,
predecessor event ref, and sequence. Under the single session lock an exact
duplicate is idempotent; divergent reuse, reordering, truncation, or a broken
predecessor chain fails closed. `status` and `report` expose incomplete or
corrupt history without healing it.

The report object is written before its completion event. A crash between them
leaves an explicit incomplete/orphan attempt. Exact retry may reconcile the
same object and event. Recovery cannot create a review, approve a repair, close
a finding, admit a regression fixture, or alter Factory state.

The slice does not copy the Factory two-pointer transaction machinery.

## 12. Diagnose, repair, and rerun

Diagnosis is read-only. Reviewer Agents can propose repairs but cannot apply
them.

The owner explicitly approves a repair proposal before implementation. The
repair entrypoint freshly reopens the failed attempt, finding set, proposal,
and approval. A changed proposal or different attempt requires a new approval.

Approved repair produces a new `SystemSnapshot`, an exact successor attempt,
and, where applicable, a new Factory run inside a disposable private
per-attempt root. It retains the same exact `InputSnapshot`. The prior attempt
remains readable. Relevant case reruns happen immediately; the full four-case
suite runs at final completion.

Closure requires exact successor evidence, disappearance or justified
limitation of the original finding, and classification of new or reopened
findings. No repair changes canonical case inputs to make a failing case easier.

Private cases are not part of this slice. In a future real-data project, a
private task may use the same attempt/review/report structure. A private failure
can become a repository regression only through separate opt-in sanitization
and explicit regression admission. The observed failing output is never the
expected baseline.

## 13. CLI and operator experience

The CLI group is `personal-validation`:

```text
envresearch personal-validation prepare
envresearch personal-validation assign-review
envresearch personal-validation record-review
envresearch personal-validation synthesize
envresearch personal-validation finalize-report
envresearch personal-validation approve-repair
envresearch personal-validation apply-repair
envresearch personal-validation status
envresearch personal-validation report
envresearch personal-validation verify-repair
```

Every command uses explicit reference files and an explicit private root. No
command scans for the latest case, run, review, report, or repair.

`prepare` creates or resumes one exact validation session and returns its
reference plus ReviewBundle references.

`assign-review` issues an exact role-bound assignment for orchestration.
`record-review` validates and stores strict returned review JSON.
`synthesize` requires both primary reviews and records the synthesis review.
`finalize-report` deterministically reduces the complete review union into a
report. `approve-repair` is the only operation that durably captures explicit
owner consent over one exact proposal; it never applies the repair.
`apply-repair` consumes an existing exact approval and creates the successor in
the disposable validation roots; it cannot mint or broaden an approval.

`status` and `report` are strictly read-only. They do not create directories,
locks, objects, events, reviews, or recovery state.

Codex orchestration consumes ReviewBundle refs, dispatches the two parallel
Reviewer Agents and then the Synthesis Reviewer, and returns strict review JSON
to the service. The operator normally starts the workflow by asking Codex to
run Personal Usability Validation; direct CLI use remains documented for
recovery and inspection.

## 14. Error boundary

The public layer uses stable typed errors for:

- invalid protocol or canonical case;
- private-root overlap or unsafe permissions;
- stale or substituted Factory run/input evidence;
- forged, duplicate, wrong-role, or incomplete Agent review;
- missing or noncanonical external-access provenance;
- Synthesis deletion of a material disagreement;
- repair without exact approval;
- changed case inputs during before/after comparison; and
- attempted product, pack, benchmark-release, or regression promotion.

Errors do not disable the system. They stop only the invalid validation
operation and leave prior artifacts readable.

## 15. Testing strategy

### 15.1 Contracts

- strict/frozen/no-extra models;
- canonical ordering and full-digest IDs;
- construction-order checks prove Attempt/Bundle/Event form the declared
  acyclic DAG and reject forward/circular refs;
- exact nested artifact revalidation;
- literal personal-only, no-block, hidden-not-run, and product-pending fields;
- deterministic case-state reduction; and
- absence of aggregate score or release fields.

### 15.2 Four canonical behaviors

- supported end-to-end success;
- correct stop proven from a read-only terminal inspection and complete
  authoritative artifact inventory, without calling initialize/recover or
  issuing new work;
- exact data/method incompatibility detection; and
- exact evidence/citation challenge localization.

### 15.3 Agent separation

- Reviewer roles are distinct;
- role-specific assignments bind exact attempt, bundle, policy, invocation,
  and raw-response digest;
- Scientific and Evidence inputs exclude producer transcript and peer review;
- primary and repair projections exclude seeded oracle locations, prior
  reviews, findings, and repair answers;
- Synthesis requires both immutable reviews;
- missing, duplicate, forged, wrong-bundle, wrong-role, or stale reviews fail;
- material disagreement remains visible; and
- external calls require exact provenance;
- mutating Zotero/network operations are rejected before dispatch; and
- requests containing a seeded private canary or private-root path are rejected
  before dispatch.

### 15.4 Storage and authority

- reject private roots that are missing, symlinked, unsafe, or physically
  overlap repository/worktrees/Obsidian;
- reject explicit object/control roots that escape the private root or overlap
  repository/worktrees/Obsidian, and forbid default sibling control roots;
- replace the lexical private-root path after pinning and prove object/journal
  operations fail closed or remain on the retained inode;
- preserve repository and authoritative product-root bytes, inventory,
  ownership, modes, links, and current pointers;
- prove every mutable run/rerun write is confined to disposable per-attempt
  roots beneath the private validation root;
- bind the same exact `InputSnapshot` across before/after repair while allowing
  a new exact `SystemSnapshot`;
- bind the exact completed-run or correct-stop target, case inputs, system
  snapshot, and before/after refs;
- reject a changed case byte during closure; and
- keep Factory, benchmark release, method-pack, and product pointers unchanged.

### 15.5 Recovery and repair

- object publication before event append;
- exact orphan reconciliation;
- reject divergent, reordered, duplicated-with-different-content, truncated,
  or broken-predecessor journal events without healing from status/report;
- no automatic review, repair approval, finding closure, or regression
  admission;
- exact event ordering `failed attempt -> proposal -> owner approval ->
  mutation/rerun`;
- `status/report` against missing key/head, lagging head, orphan object, or
  truncated journal performs zero writes and reports exact incomplete/corrupt
  state;
- wrong-attempt or changed-proposal approval rejection;
- successor attempt and closure reconstruction; and
- every predecessor finding persists unless an exact closure records its
  `closed | limited | reopened` state;
- full four-case regression after the final repair.

### 15.6 Integration and static gates

- focused Personal Validation suite;
- existing Factory, Paper, benchmark, and CLI regressions;
- real Agent review dry run over all four canonical bundles;
- Ruff, formatter, Mypy, lock, diff, line, payload, and process cleanup; and
- independent design/code review with zero Critical or Important findings.

## 16. Completion criteria

This slice is complete when:

1. all four canonical cases produce exact attempts and three Agent reviews;
2. all findings are visible with evidence and repair proposals;
3. owner-approved material repairs are rerun and have exact closure evidence;
4. the final four-case run has no unresolved Critical or Important findings;
5. correct-stop behavior is represented as a successful personal baseline;
6. the operator guide supports one-command use and read-only recovery;
7. full relevant tests and static gates pass;
8. independent review reports zero Critical and zero Important findings;
9. no real dataset or external expert is required; and
10. product and hidden-evaluation states remain unchanged.

Completion means the Research Factory is ready for supervised personal use on
the four canonical behaviors. It does not mean the product is scientifically
released or validated on unseen real data.

## 17. Future real-data trigger

When the owner begins a real project, the system may help search for data.
Before acquisition or use, the owner approves the exact source after access,
license, provenance, cost, and intended-use review.

The Factory then executes analysis from exact data and runtime artifacts. Paper
results may be written only from exact analysis outputs. The same three-Agent
panel reviews method/identification, estimates/diagnostics/robustness,
evidence/citations/tables, and paper claims. That future task is a new personal
validation session, not retroactive evidence for this canonical-only slice.
