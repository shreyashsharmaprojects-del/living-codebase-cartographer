# Visualization — living-codebase-cartographer

Read-only interactive browser view over `.codebase-map/graph.json`.
Generation never modifies the graph; UI interaction never writes back.
Regenerate (`visualize`) after `sync` whenever the architecture changed.

## Command

```bash
python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py visualize [options]
```

| Option | Effect |
|---|---|
| `--kind M` | Default mode: `architecture` (default) \| `dependency` \| `call-graph` \| `data-flow` \| `api` \| `database` \| `external` \| `impact` \| `flow` |
| `--symbol S` | Focus a symbol's neighborhood (`--depth N`, default 2) |
| `--impact S` | Blast-radius focus — reuses the `impact` engine (same logic as CLI) |
| `--flow F` | Flow focus: `"POST /api/claims"` (seed neighborhood) or `"A -> B"` (evidenced path; warns when incomplete) |
| `--depth N` | Focus neighborhood depth |
| `--allow-stale` | Generate despite a stale map (staleness is bannered in the UI) |
| `--open` | Open in the default browser (`file://`, no server needed) |
| `--serve [--serve-port P]` | Serve `127.0.0.1:P` via a stdlib-only local server (default 8734) |

Without `--allow-stale`, generation refuses when tracked source files changed
(“run `sync` first”). Output:

```text
.codebase-map/visualization/
  index.html      # self-contained app (CSS+JS inline, no CDN)
  data/graph.js   # view model as window.__CARTO_GRAPH__ (JSONP → file:// safe)
  data/boot.js    # window.__CARTO_BOOT__ (mode/focus/freshness)
```

Copy/share the directory as-is; it works offline.

## Modes

- **architecture** (default): grouped nodes (`claims.claim`, `claims.policy`,
  `db`, `config`, … derived from `meta.package`/paths, never hard-coded
  stacks). Double-click drills into a group; right-click focuses a node.
- **dependency / call-graph / data-flow**: raw-node views over the generic
  edge sets (`depends-on/imports/injects/…`, `calls/handled-by/…`,
  `reads/writes/queries/…`), capped at 400 nodes / 1200 edges by
  significance ordering.
- **api / database / external**: seeded views (endpoints, tables/collections,
  external-services + their neighborhoods).
- **impact / flow**: focus views from `--impact`/`--flow`; impact bands
  (target/direct/transitive/entry-points) come from the shared core engine;
  incomplete flows show “Flow inference incomplete — source inspection
  required” and render evidenced segments only.

## Confidence (never silently confirmed)

HIGH solid · MEDIUM lighter · LOW dashed lead + halo · UNKNOWN faint/dashed.
LOW/UNKNOWN default off; the filter panel shows per-level counts. Node glyphs
use shape+letter as well as color (□ service/controller · ◇ endpoint/route ·
⬣ group · ▬ table/db · ⬔ external · ▲ event/queue · ✕ test).

## Interaction

Pan (drag), zoom (wheel/+/-/buttons), click select → inspector (metadata,
file:line source link, incoming/outgoing relations grouped by type, click to
traverse), double-click drill/focus-deeper, right-click focus, `F` fit,
`/` search, `Esc` clear. Search matches name/kind/file/endpoint/lang.
SVG export captures the current view.

Deep links: `?node=ClaimService` `?mode=data-flow` `?impact=X`
`?flow=…` `?endpoint=POST /api/claims` `?depth=N`
`?editor=vscode|jetbrains|none` (source-link scheme, default vscode).

## Performance

Default view renders ~dozens of groups, never thousands of nodes. Focus views
neighborhood-expand with depth caps; force layout relaxes only the visible
subgraph; viewport culling skips off-screen draw. View-model caps keep even
3000-node graphs interactive (tested).

## Limits

- Static-analysis view: shows only evidenced relations; method→table links
  need resolvable SQL/JPQL — otherwise the inspector says so.
- Single-seed `--flow "POST /api/claims"` shows the handler neighborhood,
  not a proven end-to-end chain; use `"A -> B"` for path search.
- `file` nodes and `defines` edges are excluded from graph views (noise).
- `table:`/`entity:`-style bare refs resolve only when their node exists;
  otherwise they are validation-unverified leads, not drawn as confirmed.
