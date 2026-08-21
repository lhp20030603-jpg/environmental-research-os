# Changelog

All notable user-facing changes are recorded here. Capability labels refer to
workflow layers; the Python distribution remains version `0.2.0` to preserve
the existing V0.2 artifact contract.

## Unreleased

- Public source repository created at
  <https://github.com/lhp20030603-jpg/environmental-research-os>.
- Annotated release tags are published only after the corresponding public
  `main` CI run and remote file audit are green.

## Personal Pilot - 2026-08-21

### Added

- V0.2 multi-method research-design workflow with immutable artifacts, bounded
  worker handoffs, human decision contexts, recovery, and synthetic benchmarks.
- V0.3/V0.3.1 local-data econometrics for causal-policy, environmental
  measurement, meta-analysis, and valuation methods, with independent evidence
  reconstruction.
- V0.4 evidence-bound Paper Builder with claim ledger, argument map, draft,
  independent audit, revision closure, and release candidate.
- V1 governed Research Factory assembly and per-run promotion workflow.
- Internal Personal Validation foundations through canonical cases and
  three-role Agent advisory reports.
- Public installation, security, contribution, citation, release, and preflight
  documentation.

### Release boundary

- Product status remains `scientific_release_pending`; no formal held-out
  scientific evaluation is claimed.
- Personal Validation Tasks 7–10 are intentionally outside this preview. The
  core Research, Econometrics, Paper, and Factory CLI surfaces do not depend on
  them.
- DiD diagnostics now persist an explicit `base_period`. Reports created by the
  pre-preview schema may require an authenticated rerun; this release does not
  perform an in-place report migration.
- Real datasets, private artifacts, personal project memory, external author
  code, and unapproved replication archives are excluded from the repository.
