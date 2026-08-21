# V0.4 Paper Builder Design

**Status:** Implemented and independently reviewed; V1.0 handoff-ready
**Date:** 2026-08-12
**Upstream authority:** one current `econometrics.v031-transition.v1` marker

## 1. Objective and boundary

V0.4 turns accepted research artifacts into an auditable paper. It consumes
exact references already accepted by the Research OS; it does not refit an
estimator, reinterpret failed analyses as evidence, acquire new data, install a
package, or register another econometric method. The only accepted valuation
input is the current V0.3.1 transition whose independently reproduced report is
`passed` with nine matched outcomes.

Spatial, Exposure, environmental forecasting, Wave-3 structural models, and
Stata remain capability-gated. V0.4 does not open those gates. The roadmap is
V0.3.1 -> V0.4 -> V1.0; it has no invented V0.5--V0.9 milestones.

## 2. Input contract

The entry point receives an exact transition `ArtifactRef`, not a path search or
"latest" selector. Reopening it must authenticate:

- the current `V031TransitionMarker` and its manifest, run, catalog binding,
  catalog, and report references;
- a reproduced `ValuationExitReport(status="passed")` with exactly 9/9 matched;
- the reviewed runtime and frozen package closure bound by the transition; and
- each accepted green `LocalAnalysisReference` and independently reconstructed
  `LocalAnalysisReport`, including its exact spec, snapshot, outputs, execution,
  package authorities, result, units, uncertainty, diagnostics, and welfare
  transformation.

The consumer must compare the full transition/report/run/catalog-binding
current chain both before and after reconstruction, then repeat that comparison
after accepted-report materialization. CV probability and bid-response claims
must use the independently reconstructed typed probabilities and bid-level
yes-share artifact, not an estimator-produced diagnostic summary.

Other V0.1--V0.3 evidence may enter through the same pattern: an authenticated
accepted-artifact reference plus a schema-validated payload. An exception,
superseded generation, unresolved exit outcome, missing raw output, failed
verification, or non-current transition is not writable evidence.

## 3. Artifact stages

### 3.1 Claim-evidence ledger

Create one immutable row per proposed claim. Every row binds a stable claim ID,
claim type, estimand or descriptive quantity, exact source artifact refs, raw
table/figure/output refs, reconstruction status, unit/population/time/price
basis, uncertainty, allowed strength, and explicit limitations. A claim cannot
be promoted when its evidence is absent, contradictory, superseded, or broader
than the accepted estimand.

### 3.2 Argument map

Build a typed directed graph from research question to contribution, mechanism,
empirical claims, robustness, limitations, and policy implications. Every
empirical node cites ledger claim IDs; every edge records its reasoning type and
whether it is evidence-backed, interpretive, or conditional. The graph rejects
cycles that masquerade as support and conclusions unsupported by an incoming
accepted claim.

### 3.3 Section writing

Generate section drafts only from the argument map and ledger. Each paragraph
emits a machine-readable claim-span map that binds prose spans to claim IDs,
citations, tables, and figures. Method and result prose preserves the declared
model, units, sign, uncertainty, sensitivity, and failure boundaries. Failed or
integrity cases may explain scope and validation but never become substantive
findings.

### 3.4 Audit and revision

Run citation, number, table/figure, claim-strength, policy-language, scope, and
cross-section consistency audits. Findings are immutable artifacts with exact
draft and evidence inputs. Revision creates a new draft generation and closes a
finding only when the affected claim-span map and upstream refs revalidate.

## 4. Minimum contracts

V0.4 should introduce the smallest frozen schemas needed for:

- `ClaimEvidenceLedger` and exact `ClaimEvidenceRow`;
- `ArgumentMap` with typed nodes and edges;
- `PaperDraft` plus claim-span, citation, table, and figure bindings;
- `PaperAuditReport` with typed findings and exact input references; and
- a final release candidate that is current only when every upstream accepted
  artifact, ledger row, graph node, draft span, and audit closure is current.

The stable handoff object is a pair of the exact accepted `ArtifactRef` and its
reopened typed payload. Paper Builder must not receive mutable filesystem paths,
untyped dictionaries, prose-only evidence summaries, or evaluator expectations.

## 5. Fail-closed rules

- Never scan for a latest analysis, report, transition, draft, or citation.
- Never silently substitute a revised artifact or a different method result.
- Never infer statistical significance as a release condition.
- Never change an estimand, model, threshold, unit, or welfare transformation in
  the writing layer.
- Never write evidence-grounded prose without an accepted claim reference.
- Never permit a policy recommendation to outrun identification, external
  validity, population, time, price, or uncertainty boundaries.
- Revision or mutation of any bound input invalidates affected descendants.

## 6. Acceptance matrix

The V0.4 implementation plan should cover at least: one green end-to-end paper
slice; stale transition; superseded analysis; mutated table/figure bytes;
contradictory numeric prose; unsupported claim; citation mismatch; unit or
population overreach; policy-language overclaim; concurrent identical build;
process-death recovery; and upstream revision invalidation. All acceptance runs
must reconstruct from exact refs and preserve V0.2/V0.3 artifact compatibility.

## 7. V1.0 handoff

V0.4 ends with a current audited paper/reproduction candidate whose evidence
lineage can be independently reopened. V1.0 may compose V0.1--V0.4 into governed
end-to-end research runs and hidden evaluation. V1.0 promotion remains a
separate accountability decision; V0.4 cannot self-approve it.

## 8. Implementation status

V0.4 implements the five artifact stages and the exact-reference CLI. A release
candidate binds the complete clean audit payload and, for every later draft
generation, the ordered service-authenticated revision ancestry back to
generation 1. Release identity includes that entire ancestry.

Publication uses an authenticated pending/commit pair with process-death
recovery and conflict-safe concurrency. `paper status` is strictly read-only:
existing public, protected-control, and Paper registry authorities are reopened
without creating or repairing directories, keys, catalog anchors, locks,
objects, or pointers. Root aliases, unsafe protected metadata, torn pointers,
mutated raw outputs, superseded inputs, missing revision hops, and current
mismatches fail closed.

The final independent review reproduced the attack-focused release, ancestry,
read-only, root-separation, concurrency, and recovery gates and returned **0
Critical, 0 Important, and 0 Minor findings**. This is software and artifact
handoff readiness only; scientific approval and V1.0 promotion remain external
human-accountability gates.

Final formal evidence used the sealed V0.3.1 v3 root: 1,390 unit tests and 1,120
integration tests passed, with 15 expected integration skips and no failures.
Combined coverage was 39,584/44,409 statements (89.1350852304713%). Ruff,
changed-file formatting, mypy over 338 source files, lock, diff, 400-line, and
payload gates passed. The stable V1.0 input is the exact current
`(ArtifactRef, PaperReleaseCandidate)` pair; this evidence does not itself grant
V1.0 scientific promotion.
