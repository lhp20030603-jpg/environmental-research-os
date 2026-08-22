# Agent Working Principles

## Product purpose

This repository is a practical assistant for researching and writing academic
papers. It is not a security-attack paper, a hostile-environment sandbox, or a
project whose value is measured by how many defensive mechanisms it contains.

Agents may run checks and validations needed to establish that a feature works.
Validation must remain proportional to realistic use, observable failures, and
the needs of paper writing. Do not turn validation into open-ended hardening.

## Mandatory constraints

1. **Do not over-defend.** Prefer the smallest reliable implementation that
   makes the paper-writing workflow usable. Once the requested function works
   and ordinary failures are handled, stop unless the user explicitly expands
   the scope.
2. **Do not add hash or SHA-256 work.** Do not introduce or expand hash-related
   requirements, implementation, tests, acceptance criteria, or documentation.
   Existing mechanisms may remain for compatibility, but they are not a reason
   to create more work unless the user explicitly requests it.
3. **Do not repeatedly defend against practically impossible cases.** Add a
   regression only for a reproduced problem, a reasonably likely failure, or a
   clear user-facing risk. Do not multiply near-duplicate adversarial cases.
4. **Do not over-mechanize rubrics.** When a rubric is useful, keep it concise,
   judgment-friendly, and tied to the actual research or writing decision.
   Avoid elaborate scoring formulas, excessive subcriteria, and false
   precision.

## Working standard

- Prioritize end-to-end usefulness: finding material, selecting an appropriate
  method, producing evidence-backed analysis, drafting, revising, and exporting
  a paper.
- Treat tests as evidence that important functions work, not as the product
  itself.
- Report real limitations plainly and repair them iteratively; do not block the
  whole system merely because a nonessential or highly unlikely case remains.
- Do not invent extra security, validation, benchmark, or governance tasks after
  the requested usable workflow is complete.
- A direct user instruction overrides a more defensive interpretation of older
  plans or specifications.
