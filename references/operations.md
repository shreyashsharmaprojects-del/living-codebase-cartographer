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
