# Operations runbook — living-codebase-cartographer

Technology-agnostic: every operation below works on any stack. Technology
identity appears only as `meta.lang` / `meta.framework` filters and labels.

## map-init

```bash
python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py init [--full]
```

- Full scan: walk → **technology detection** (`stack.md`) → per-file analyzer
  dispatch → generic graph → resolution → derived views + `_candidates.md`;
  creates `architecture.md` skeleton only if absent.
- Prints detected languages + unsupported areas (fallback, LOW).
- Review `scan_errors` / `unclaimed_files` in `graph.json` if reported.
- Then curate `architecture.md` + promote flows (see `flows.md`), record
  judgment calls (`decisions.md`), run `validate`.

## map-sync (incremental — preserves unaffected records)

```bash
python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py sync
```

Pipeline: HEAD/working-tree vs `last_sync_commit` (normalized to the scanned
root, so subdirectory scans work) → changed files → **analyzer per file** →
rescan only them → update generic graph → re-resolve → refresh detection if
manifests changed → recompute flow skeletons → rewrite derived views →
append `changes/` record only for significant deltas. Hash check catches
non-git modifications and deletions git cannot see.

Run after every source-changing task, and whenever `status` says NEEDS_SYNC.

## map-status

```bash
python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py status
```

Prints CURRENT / NEEDS_SYNC plus the changed-file list and detected stack.
Cheap; run before answering any architecture question.

Two lists, two meanings:

- `M <path>` — raw git signal: files committed or modified in the working
  tree since `last_sync_commit`. Informational; a file can appear here while
  its content already matches the map (e.g. committed before the last sync,
  or touched without byte changes).
- `S <path>` — hash-verified staleness: file bytes actually differ from what
  `sync` recorded. **Only `S` rows make the map stale.** `visualize` refuses
  exactly these files without `--allow-stale`; `sync` rescans exactly these.

So `Changed files since sync: 28` + `Status: CURRENT` is coherent: git sees
28 touched paths, zero carry unsynced content. If a path looks garbled
(`ackend/...`), widen the terminal — long `M` rows wrap, hiding the leading
character of the continuation line.

## map-query

```bash
python3 .../cartographer.py query --kind endpoint
python3 .../cartographer.py query --kind service --name Claim
python3 .../cartographer.py query --kind table
python3 .../cartographer.py query --lang python
python3 .../cartographer.py query --kind function --lang go
```

Generic kinds (closed vocabulary, see `schema.md`): `class interface enum
function method component service controller handler endpoint route guard
interceptor state event queue topic job schedule database table view query
procedure sequence collection cache entity external-service configuration
configuration-key environment deployment-unit test test-case migration ci-job
file` (+ structural `module package repository application directory`).
`--lang` scopes to one analyzer language in mixed-language repos.

## map-impact (blast radius)

```bash
python3 .../cartographer.py impact ClaimService
```

Prints direct callers/dependencies plus reverse-BFS reachability to entry
points (endpoints, queues, jobs) and consumers (components, services,
routes). Treat LOW-confidence rows as leads: verify in source.

## map-flow

```bash
python3 .../cartographer.py flow --from "POST /api/claims" --to claim
```

Shortest path over resolved edges (any seed kind: endpoint, queue, event,
job, route). Verify each hop in source before reporting.

## map-validate

```bash
python3 .../cartographer.py validate
```

Checks duplicate nodes, dangling edge endpoints, references to deleted files,
hash-stale files, no-evidence nodes, **and generic-schema conformance**
(no technology-specific kinds/edge types). Exit 1 + NEEDS_SYNC when action
is needed; exit 2 + STALE_SCHEMA when the graph predates the tool. Fix by
running `sync` (auto-migrates) or `init --full`; never hand-edit `graph.json`.

## map-detect

```bash
python3 .../cartographer.py detect
```

Prints the evidence-based detected stack (same content as `stack.md`).

## map-visualize

```bash
python3 .../cartographer.py visualize [--kind architecture] [--symbol S]
  [--impact S] [--flow "A -> B"] [--depth 2] [--allow-stale] [--open] [--serve]
```

Generates `.codebase-map/visualization/index.html` (+ `data/graph.js`,
`data/boot.js`) — a read-only, offline, dependency-free browser explorer.
Refuses stale maps unless `--allow-stale` (bannered in the UI when forced).
`--impact`/`--flow` reuse the core impact/flow engine; see
`references/visualization.md` for modes, filters, deep links, and limits.
Generation never modifies `graph.json`.

## Subagent strategy (large repos only)

Fan out only when the repo is too large for one pass: one analyzer per area
(backend / frontend / data / infra), each restricted to `query` output +
targeted source reads for its area, then a synthesis pass curates
`architecture.md` and `business-flows/`. Default for small/medium repos is a
single inline pass.

## Workflow-skill integration contract (one-sided specification)

This section is addressed to the authors of the companion web-app-workflow
skill (phases 01 Requirements → 06 Harden; not present in this workspace).
It specifies, from the cartographer side only, which cartographer commands
each workflow phase invokes, in what order, and the exact `docs/*.md`
Markdown shapes `intent import` parses. Parsing is mechanical —
line-oriented headers, `- [ ]` checkboxes, `| tables |`, matched with
regex/string-split only. Never NLP, never inferred structure.

Assumed commands (built by a parallel effort; specified here by interface
only): `intent import`, `intent bind`, `why`, `responsible-for`, plus the
existing `sync` and `validate`. If a command does not exist yet, the
workflow skill MUST degrade to writing the docs shapes below unchanged —
the docs, not the graph, carry the intent.

Precedence rule (normative): **docs are authoritative, the graph is a
projection.** `intent import` is a one-way docs→graph load. Import never
edits `docs/*.md`. When graph and docs conflict on intent (capabilities,
requirements, slices, decisions), docs win; re-run import after fixing the
docs, never hand-edit the graph.

Stable id conventions: graph node ids are slugified
(`intent:<kind>:flow-<N>-<slug>`, `intent:slice:slice-<N>-<slug>`,
`intent:concept:<slug>`, `intent:non-goal:<slug>`,
`intent:decision:<date>-<slug>`; see `parse_intent_docs` for the exact
scheme). The short forms `REQ-NNN`, `CAP-NNN`, `SLICE-N`, `NG-N` are
resolvable ALIASES, not node ids: `intent bind`, `why`, and
`responsible-for` resolve them by name/substring match (an inline
`[REQ-001]` tag surfaces inside the slug as `...-req-001-...`, so the
alias resolves unambiguously). Anyone reading `graph.json` directly,
scripting against node ids, or debugging a binding MUST use the full
`intent:...` ids; anyone typing CLI commands MAY use the short aliases.
Ids are stable across re-imports for unchanged text; rewording an
untagged criterion re-keys its node (see Requirement rule below), so
tag criteria that will be bound.

### Contract table: workflow phase → cartographer operations

| # | Workflow phase | Workflow skill writes | Cartographer command(s) | Graph effect (before code exists where noted) |
|---|---|---|---|---|
| 01 | Requirements | `docs/requirements.md`; after user approval of Non-goals + Open questions | `intent import docs/requirements.md` | capability nodes (one per `### Flow N` heading), requirement nodes (one per acceptance-criterion checkbox, `part-of` its flow's capability), concept nodes (one per `## Data` table row), non-goal nodes (one per `## Non-goals` bullet). Intent enters the graph before any code exists. Short aliases (`CAP-N`, `REQ-NNN`) resolve to the slugified node ids. |
| 02 | Plan | `docs/plan.md` (with `## Slices`, each `### Slice N — name` + `- Satisfies:` line) | `intent import docs/plan.md` | slice nodes, each `part-of` the capabilities named in its `Satisfies:` line (see shape below). Short alias `SLICE-N` resolves to the slugified node id. |
| 03 | Skeleton | init/scaffold code | `init` (if no map) or `sync`, then `intent bind` skeleton files → `SLICE-0` | Skeleton file/symbol nodes bound to slice 0 (`realizes` edges). Proves the toolchain slice is tracked like any other. |
| 04.1 | Slice step 1 Restate (HIGHEST VALUE) | declare: slice name, acceptance criteria (copied from plan), expected files, planned tests | `intent bind --slice <slice-alias-or-id> --realizes <req-alias-or-id>... --nodes <ids...> --declares-files <paths...>` BEFORE any implementation edit | Records stated intent before code exists. This capture is unrecoverable later — post-hoc binding cannot distinguish planned from speculative files. All `--declares-files` paths are stored on the bind record verbatim. |
| 04.4 | Slice step 4 Verify (drift check — DEFERRED) | full suite green; per-criterion test mapping (manual until built) | `sync`, then declared-vs-actual comparison (§Deferred: drift) | Specified output: `N files changed that this slice did not declare` — the YAGNI enforcement readout. Manual rule until implemented: diff the changed-file list against the step-1 declaration. |
| 04.5 | Slice step 5 Record (coverage — DEFERRED) | `docs/progress.md`, `docs/decisions.md`, `api.http` updates | final `intent bind` node bindings + coverage check (§Deferred: coverage) | Specified check: every acceptance criterion maps to ≥1 test node via `realizes`; criteria with zero covering tests are reported. Manual until implemented. |
| 05 | Review | findings list (no fixes in phase) | `why <symbol>`, `responsible-for <req-alias-or-id>`, `validate` as review inputs | `why` traces file/symbol → slice → requirement → capability; `responsible-for` lists code nodes bound to a requirement. Reviewers use these plus `validate` output; review writes no graph data. |
| 06 | Harden | whole-app checklist fixes | `validate` findings + `needs-review` markers as the checklist input | Each validate finding / needs-review marker is either resolved (fix + `sync`) or recorded in `docs/decisions.md` under Deferred. `sync` after every fix. |

Emphasis: the step-1 declaration is the highest-value capture in this
contract. Stating intent before code is the only point at which "what we
meant to touch" is observable; everything after is reconstruction. A
workflow author implementing this contract should gate implementation edits
on a completed declaration, not the reverse.

### Normative docs shapes (`intent import` input grammar)

All rules are line-oriented. A parser MUST implement them with anchored
regexes / prefix checks / `str.split('|')` and MUST NOT use NLP, stemming,
or fuzzy matching. A line that does not match a rule is ignored (never
an error — docs contain prose between the machine-readable lines).

**`docs/requirements.md`:**

- Capability: a line matching `^### Flow (\d+) — (.+)$` (group 1 = flow
  number, group 2 = display name → `intent:capability:flow-<N>-<slug>`).
  Applies ONLY inside the
  `## Core flows` section (from the `## Core flows` header to the next
  `## ` header). `###` headings elsewhere are ignored.
- Requirement: a line matching `^- \[[ xX]\] (.*)$` inside
  `## Core flows` — every acceptance-criterion checkbox becomes a
  requirement node, positional within its flow:
  `intent:requirement:flow-<N>-<k>-<slug>`, `part-of` the enclosing
  flow's capability. An optional inline id tag `[REQ-NNN]` at the start
  of the criterion text is preserved in the slug (so
  `flow-1-1-req-001-...`); untagged criteria are parsed identically,
  with the slug derived from the criterion text. Consequence:
  rewording an untagged criterion re-keys its node id (old node
  dropped, new node created, bindings to the old id need re-binding);
  tagging with `[REQ-NNN]` keeps the id stable across wording edits.
  Authors MAY use untagged boxes, including the workflow template's
  empty `- [ ]` placeholders — a box with no text after it yields no
  usable slug and is skipped. Tagging before approval is RECOMMENDED
  for any criterion that will be bound. Checked (`[x]`/`[X]`) boxes
  parse identically — checked state is not graph data.
- Concept: inside the `## Data` section, each table body row —
  a line starting with `|` that is not the header row (contains
  `Entity`) and not the separator row (matches `^\|\s*[-| :]+\|$`).
  Column 1 (`split('|')[1].strip()`) is the entity name → concept node;
  column 2 key fields and column 4 notes stored as node meta. The
  `Must survive a restart:` / `Sensitive / regulated:` free-text lines
  are NOT parsed (prose).
- Non-goal: inside the `## Non-goals` section, each line matching
  `^- (.*)$` → one non-goal node (`NG-<position>`, 1-based in section
  order), text = group 1. Numbered-list (`1.`) variants are ignored —
  authors MUST use `-` bullets.
- Ignored sections (never parsed): `## Users`, `## Accounts and access`,
  `## Constraints`, `## Reach`, `## Scale`, `## Definition of done`,
  `## Open questions`, `## One-line summary`.

Example:

```markdown
### Flow 1 — Submit a widget
Acceptance criteria:
- [ ] [REQ-001] Submitting a valid name returns an id immediately.
- [ ] An untagged checkbox is also a requirement (slug from its text).

## Data
| Entity | Key fields | Belongs to | Notes |
|---|---|---|---|
| widget | id, name, owner_sub | user | core record |

## Non-goals
- Multi-tenancy (single workspace for v1).
```

**`docs/plan.md`:**

- Slice: a line matching `^### Slice (\d+) — (.+)$` inside the
  `## Slices` section → `intent:slice:slice-<N>-<slug>`. Only `## Slices` is scanned.
- Membership: the first line after the slice heading matching
  `^- Satisfies: (.*)$`. Format: comma-free flow/criterion refs —
  `Flow <N>` names a capability (`part-of` edge to the capability id),
  `REQ-NNN` tokens name requirements (`part-of` edges, resolved as
  aliases); `none`
  (case-insensitive, e.g. `- Satisfies: none (scaffolding...)`) means
  no edges. A slice with NO `Satisfies:` line binds to nothing and
  import MUST report it (`slice-<N> declares no Satisfies`).
- REQ tokens referencing unknown requirement ids MUST be reported
  (`slice-<N> references unknown REQ-XXX`), never silently dropped.
- Ignored sections: `## Stack`, `## Open forks` / `## Resolved forks`,
  `## Data model`, `## Routes`, `## API`, `## Test strategy`,
  `## Out of scope for this plan`.

Example:

```markdown
### Slice 1 — Submit & id
- Satisfies: Flow 1 (REQ-001, REQ-002).
- Tests: unit — validation; integration — POST /api/widgets.
```

**`docs/decisions.md`:**

- Decision: a line matching `^### (\d{4}-\d{2}-\d{2}) — (.+)$` inside
  the `## Decisions` section → one decision node (date + title).
  The following `**Context:**`, `**Decision:**`, `**Why:**`,
  `**Rejected:**`, `**Revisit if:**` lines are stored as node meta
  (key = lowercased label, value = rest of line); unrecognized
  `**X:**` lines are kept verbatim under meta, never dropped.
- The `## Deferred` section is NOT parsed into decision nodes (it is
  the harden-checklist input; see table row 06).

### Acceptance-criteria-to-tests cross-check (specified, not built)

For each requirement node, the mechanizable part of phase
04-step-4's manual rule ("every acceptance criterion has at least one
test that would fail if it regressed") is: whether any node of kind
`test` / `test-case` reaches the requirement via a `tests` or
`realizes` edge (test → requirement direction). The specified readout
is a per-requirement boolean plus the covering test id list (shown
with short aliases where tagged):

```text
REQ-001: COVERED by test/ClassName/methodName [...]
REQ-004: UNCOVERED (no test node realizes it)
```

Binding tests to requirements stays manual (`intent bind --test ...
--realizes <req-alias-or-id>`, recorded during slice step 2 alongside the tests);
only the readout is mechanizable. No edge inference: a test whose name
merely resembles a requirement does not cover it.

### Deferred items (specified-but-unbuilt interfaces)

- **Drift (phase 04 step 4).** Interface: `sync` output extended with a
  per-active-slice section comparing the step-1 `--declares-files` set
  against files changed since the declaration (hash-verified, same
  mechanism as `status` staleness). Readout format (slice short alias):
  `slice-<N> drift: <K> files changed that this slice did not declare:`
  followed by one path per line. Exact-path match only; renames count
  as undeclared. Unbuilt: no implementation in this wave.
- **Coverage (phase 04 step 5).** Interface: the cross-check readout
  above, run over all requirements of the slice's `Satisfies` set at
  record time; uncovered criteria block slice sign-off. Unbuilt.
- **`violates` edge type (phase 05 review output).** Proposed edge
  `violates` from a code node to a non-goal node (`NG-N`),
  recording review findings that built what Non-goals excluded.
  Requires an `EDGE_TYPES` addition plus review-side write path;
  specified here so a later wave can add it without renegotiating
  semantics. Unbuilt.
