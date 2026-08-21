# Paper Builder V0.4 Operator Guide

Paper Builder publishes a release only from exact, current, independently
reopened artifacts. It does not search for a latest artifact, run an estimator,
repair authority state during `status`, or approve scientific promotion.

## Required roots and references

Provide three existing, physically separate roots:

- the sealed V0.3.1 exit root;
- the Paper Builder registry root; and
- the sealed research root, whose derived protected worker-control root must
  also be physically separate from all three roots.

References are JSON files containing one strict `ArtifactRef`. Generation 1
builds require an audit ref and draft ref. A later generation also requires the
exact terminal revision ref; the release reconstructs and binds the complete
ordered revision ancestry back to generation 1.

## Commands

```bash
uv run envresearch paper build AUDIT_REF.json DRAFT_REF.json \
  --v031-root /absolute/v031 \
  --paper-root /absolute/paper \
  --research-root /absolute/research
```

For a revised draft, add:

```bash
  --revision-reference REVISION_REF.json
```

Reopen an exact release without publication or recovery:

```bash
uv run envresearch paper status RELEASE_REF.json \
  --v031-root /absolute/v031 \
  --paper-root /absolute/paper \
  --research-root /absolute/research
```

Both commands emit deterministic JSON. Preserve the returned
`release_reference` and `release` together as the V1.0 handoff pair.

## Exit codes

- `0`: the exact release is current and green.
- `1`: the draft was audited but has open release-blocking findings.
- `2`: input, authority, integrity, scope, root, pointer, or lineage validation
  failed.

Missing required arguments also return deterministic JSON with exit code 2.

## Read-only status boundary

`paper status` opens existing research and worker-control state only. It does
not create directories, keys, catalog anchors, lock files, objects, or pointers;
it does not chmod unsafe state. Missing locks or unsafe protected ownership,
permissions, symlinks, or hardlinks fail closed.

Status reopens the release object, pending/commit pair, current draft, clean
audit, all raw outputs and transitive authorities, and every revision closure.
Any mutation, supersession, torn pointer, ancestry gap, or current mismatch
invalidates the release.

## Crash recovery and concurrency

Build uses immutable publication plus matching pending and commit pointers.
After process death, rerun the same exact build inputs. Recovery authenticates
the prior committed release before restoring or completing publication. A
different pending candidate is an authority conflict. Concurrent identical
builders converge on one release ref; conflicting builders cannot overwrite one
another or expose a torn release.

Do not manually edit objects, pointers, revision envelopes, protected controls,
or lock files. Missing or damaged authority is a failed run requiring forensic
inspection or reconstruction from its upstream sealed workflow, not in-place
repair by Paper Builder.
