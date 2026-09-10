# Decision records — living-codebase-cartographer

Record agent judgment calls the scanner cannot make: flow promotions,
architecture classifications, ambiguity resolutions, exclusion choices.

One file per decision in `.codebase-map/decisions/`, e.g. `0001-fnol-flow.md`:

```markdown
# 0001 — Promote FNOL filing to a curated flow

- Date: <yyyy-mm-dd>
- Status: accepted
- Evidence: `endpoint:POST /api/claims` (HIGH), handler edge (HIGH),
  `ClaimService.fileFnol` (HIGH), `table:claim` writes (MEDIUM)
- Decision: promote skeleton → `business-flows/fnol-filing.md`
- Alternatives: …
- Consequences: …
```

Number sequentially (`0001-`, `0002-`, …). Keep entries short; link evidence,
don't paste code.
