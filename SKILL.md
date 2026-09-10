---
name: living-codebase-cartographer
description: Build and maintain a living, evidence-based map of any software repository — technology-agnostic init, incremental sync, impact analysis, flow tracing, and validation. Use when asked about architecture, request tracing, blast radius, API/database inventory, or keeping repo documentation synchronized with code.
---

# Living Codebase Cartographer

> **This is a technology-agnostic codebase mapper.** It works across stacks —
> e.g. Java + Spring Boot + PostgreSQL + Angular, Python + FastAPI +
> PostgreSQL + React, Go + gRPC + PostgreSQL + Kafka, Node.js + TypeScript +
> MongoDB + React, C# + .NET + SQL Server, Rust + REST + PostgreSQL, or
> mixed-language monorepos. Technology names below are examples, never
> assumptions: the scanner detects the actual stack during `map-init` and
> records it in `.codebase-map/stack.md`.

You maintain a **living, machine-readable map** of the repository — a derived
model that stays synchronized with the code. The code is the source of truth;
the map is a continuously maintained index of it.

CODEBASE → TECHNOLOGY DETECTION → SPECIALIZED ANALYZERS →
GENERIC GRAPH → (Query | Impact | Flow) → LLM REASONING.

Specialized analyzers provide evidence; the generic graph is the persistent
derived representation; you (the LLM) interpret that evidence. Unknown
relationships remain unknown rather than being invented.

**Core rules:**

1. Prefer tool evidence over assumptions. Never silently invent relationships.
2. Mark uncertain relationships LOW/UNKNOWN explicitly; never present them as confirmed.
3. Never modify production code as part of mapping. This skill is analysis-only.
4. Never store secret values (`.env` files are skipped entirely).
5. When map and source conflict, trust the source and update the map.
6. Technology claims require evidence: manifests + content agreement (HIGH),
   manifest or strong imports alone (MEDIUM), file-presence only (LOW).

## Layout

Set `$CARTO` to the skill path once per session
(`.dsh/skills/living-codebase-cartographer/`, or wherever it is installed —
the scanner excludes its own install dir at runtime). All commands use `$CARTO`:

- Skill home: `$CARTO`
  - `scripts/cartographer.py` — thin entry point (stdlib-only).
  - `scripts/core.py` — engine (walking, dispatch, resolve, sync, CLI).
  - `analyzers/` — one module per technology area (registry + detection in
    `analyzers/__init__.py`, contract in `analyzers/README.md`, closed
    generic schema in `analyzers/graph.py`).
  - `references/` — operation runbooks and schema notes.
  - `tests/` — conformance + regression suites.
- Map (generated, lives in the repo): `.codebase-map/`
  - `graph.json` — **source of truth** (generic nodes + edges with
    evidence/confidence; tech identity in `meta`, never in kinds/edges).
  - `stack.md` — detected stack with per-item confidence.
  - `state/` — freshness metadata (`sync-state.json`, `file-hashes.json`).
  - Derived views (`*.md` except `architecture.md`,
    `business-flows/_candidates.md`) — regenerated on every sync; do not
    hand-edit. Curated files (`architecture.md`, `business-flows/<flow>.md`,
    `decisions/`) are created once and never overwritten; `changes/` holds
    one record per significant sync.

If the repo already has an architecture/docs directory with equivalent
machine-readable content, you may reuse it; otherwise keep `.codebase-map/`
so `map-status`/`map-validate` stay predictable.

## Operations

Run the scanner from the repo root with python3 (`$CARTO` = skill path):

```bash
python3 $CARTO/scripts/cartographer.py <cmd>
```

| Operation | Command | When to use |
|---|---|---|
| `map-init` | `... cartographer.py init [--full]` | First mapping of a repo, or rebuild of a corrupt map. Runs technology detection. |
| `map-sync` | `... cartographer.py sync` | After any source change; also run when `map-status` says NEEDS_SYNC. Per-file analyzer dispatch. |
| `map-status` | `... cartographer.py status` | Freshness check before answering architecture questions. |
| `map-query` | `... cartographer.py query [--kind K] [--name N] [--lang L]` | Symbol/endpoint inventory lookups (`--lang` filters by `meta.lang`). |
| `map-impact` | `... cartographer.py impact <Symbol>` | Blast-radius / reverse-traversal analysis. |
| `map-flow` | `... cartographer.py flow --from <A> --to <B>` | Trace a path between two symbols/endpoints. |
| `map-validate` | `... cartographer.py validate` | Integrity + generic-schema conformance check. |
| `map-detect` | `... cartographer.py detect` | Show the detected stack (`stack.md`). |
| `map-visualize` | `... cartographer.py visualize [--kind M] [--symbol S] [--impact S] [--flow F] [--open] [--serve]` | Generate interactive HTML architecture explorer (9 views: Overview, Endpoints, Data, Dependencies, Symbols, Flows, Capabilities, Graph, Issues — read-only view over `graph.json`). |
| `map-intent-import` | `... cartographer.py intent import` | Parse `docs/requirements.md`, `plan.md`, `decisions.md` into ASSERTED intent nodes (idempotent). |
| `map-intent-bind` | `... cartographer.py intent bind --slice S --realizes R --nodes N,... --why "..."` | Attach code nodes to intent nodes (programmatic write path). |
| `map-why` | `... cartographer.py why <node>` | Reverse lookup: which requirements/slices/decisions claim this code. |
| `map-responsible-for` | `... cartographer.py responsible-for <intent-id>` | Forward lookup: every code node realizing a requirement/capability. |

There are no slash-command wrappers to install: invoke the skill (`/living-codebase-cartographer`
or the `skill` tool), then run the operation above and interpret the output.
The operation names (`map-init`, `map-sync`, …) are the vocabulary you use in
reports and in `decisions/` records.

### map-init (first mapping — detail: `references/operations.md`)

1. Run `init`: inventory (respects `.gitignore`, skips build/vendor dirs and
   secrets) → stack detection → per-file analyzer dispatch (unsupported
   languages get the LOW-confidence fallback pass — never a failure).
2. Read `stack.md` plus generated views (`README.md`, `api-map.md`,
   `database.md`, `external-services.md`, `business-flows/_candidates.md`).
3. **Curate** (the LLM part — see `references/flows.md`, `references/decisions.md`):
   fill `architecture.md` from evidence; promote evidence-backed end-to-end
   flows to `business-flows/<flow>.md` (each step cites file/symbol/edge);
   record judgment calls in `decisions/`.
4. Run `validate`; fix or explain every finding. Large repos: chunk by area
   (backend/frontend/data/infra), delegating to subagents only if context
   demands it; small repos inline.

### map-sync (incremental — the most important operation)

`sync`: git-change → changed-files → **analyzer-per-file** → targeted
re-analysis → re-resolve → derived views. Only changed files are rescanned;
`changes/` gets a record **only for architecturally significant deltas**.
Run after every implementation task, and before architecture questions when
`status` says NEEDS_SYNC.

### map-status / map-validate / map-detect

`status` compares HEAD + working tree against `last_sync_commit`.
`status` and `validate` share one freshness verdict (`Changed (git signal)`
vs `Stale (content differs)`); any content-stale file means NEEDS_SYNC.
`validate` additionally checks broken references, deleted files, duplicate
nodes, no-evidence entries, ID sanity, and **generic-schema conformance**.
`detect` prints the evidence-based stack. Never hand-edit `graph.json`.

### map-visualize (interactive explorer — detail: `references/visualization.md`)

Read-only browser view over `graph.json` (never a second source of truth):
`.codebase-map/visualization/index.html` + `data/graph.js` + `data/boot.js`.
Offline, no CDN/telemetry; works over `file://`. Nine views (Overview,
Endpoints, Data, Dependencies, Symbols, Flows, Capabilities, Graph, Issues); graph modes
include architecture (grouped), dependency, call-graph, data-flow, api,
database, external, impact, flow. Examples:

```bash
python3 .../cartographer.py visualize                       # overview of the repo
python3 .../cartographer.py visualize --kind data-flow      # any graph mode
python3 .../cartographer.py visualize --symbol ClaimService # focus neighborhood
python3 .../cartographer.py visualize --impact ClaimService # blast radius
python3 .../cartographer.py visualize --flow "POST /api/claims"  # flow focus
python3 .../cartographer.py visualize --open                 # + open browser
python3 .../cartographer.py visualize --serve                # + local server
```

Confidence is visual (HIGH solid / MEDIUM lighter / LOW dashed lead /
UNKNOWN faint); LOW is never confirmed. Deep links: `?node=`, `?mode=`,
`?impact=`, `?endpoint=…`, `?editor=vscode|jetbrains|none`. Regenerate after
`sync` when architecture changed (`visualize` refuses stale maps unless
`--allow-stale`).

### Answering questions / implementation tasks (detail: `references/operations.md`)

1. `status` first — sync if stale. `query`/`impact`/`flow` for the
   deterministic skeleton (`--lang` scopes mixed-language repos).
2. **Read the actual source** the map points to. Answer with evidence links
   (`file:line`), confidence levels, explicit UNKNOWNs.
3. For risky changes: inspect `visualize --impact <S>` blast radius first;
   after the change, re-run tests, `sync`, and report the `changes/` impact.
   Regenerate the visualization only when architecture changed.

### Self-healing (detail: `references/operations.md`)

- No map → `init`. Corrupt `graph.json` → tool reports CORRUPT; `init --full`
  rebuilds, preserving curated files. Stale schema → migrate via `sync` (or
  clean rebuild with `init --full`). Partial staleness → `sync` rescans only
  affected files.

## Confidence model

- **HIGH** — direct declarations. **MEDIUM** — name-resolved references.
- **LOW** — heuristics, ambiguous names, all fallback output. Never confirmed.
- **UNKNOWN** — agent-asserted without evidence; never architecture.

## Schema

The graph schema is closed and technology-independent — see
`references/schema.md` for the full field reference. Technology identity
lives in `meta` (`lang`, `framework`, `stereotype`, `dialect`, …), never in
kinds or edge types.

Full field reference: `references/schema.md`; runbooks: `references/operations.md`,
`references/flows.md`, `references/decisions.md`, `references/visualization.md`;
analyzer contract: `analyzers/README.md`; regression tests: `tests/`.
