# graph.json schema — living-codebase-cartographer (v3, generic + intent)

`graph.json` is the source of truth. All Markdown (except curated files) is derived.
The schema is **closed and technology-independent**: analyzers MUST use only
the kinds/edge types below. Technology identity lives in `meta`
(`lang`, `framework`, `stereotype`, `dialect`, …) — never in new kinds/types.
`validate` rejects violations.

## Provenance (5.2 — the most important distinction)

Every node/edge carries `provenance` (`"derived"` or `"asserted"`; absent
means derived — pre-4a graphs stay valid):

- **DERIVED** = analyzer output from code. Carries file/line evidence plus a
  `HIGH`/`MEDIUM`/`LOW`/`UNKNOWN` confidence. Everything the scanner
  produced before Wave 4a is DERIVED.
- **ASSERTED** = human/agent claim (intent layer). Carries `author`,
  `asserted_at`, `asserted_commit`, and `source` (doc file+line, e.g.
  `docs/requirements.md:23`). ASSERTED entries carry **no confidence
  level** (`confidence: null`) — confidence is the wrong axis for a claim;
  it is either the current human position (`status: active`) or it is not.

Intent is ASSERTED by humans/agents, never inferred: no LLM-based
extraction anywhere. The `intent import` parser matches explicit Markdown
structure (headings, bullets) deterministically; `intent bind` records a
human/agent's explicit `--why`.

Every intent node/edge is visually distinguishable in CLI output with the
`[ASSERTED]` prefix/suffix marker (vs unmarked DERIVED entries).

```json
{
  "version": 3,
  "root": "/abs/repo/path",
  "init_commit": "<sha | null>",
  "last_sync_commit": "<sha | null>",
  "last_sync_time": "<utc iso>",
  "nodes": [
    {
      "id": "java:class:com.example.OrderService",
      "kind": "service",
      "name": "OrderService",
      "file": "backend/src/main/java/com/example/OrderService.java",
      "line": 49,
      "relationship": "defines",
      "evidence": "source-code",
      "confidence": "HIGH | MEDIUM | LOW | UNKNOWN",
      "last_verified_commit": "<sha | null>",
      "provenance": "derived | asserted (absent means derived)",
      "meta": {"lang": "java", "framework": "spring",
               "package": "com.example", "stereotypes": ["Service"]}
    },
    {
      "id": "intent:requirement:flow-1-1-submitting-a-valid-fnol",
      "kind": "requirement",
      "name": "Submitting a valid FNOL returns a claim number",
      "file": "docs/requirements.md",
      "line": 29,
      "relationship": "defines",
      "evidence": "assertion",
      "confidence": null,
      "last_verified_commit": null,
      "provenance": "asserted",
      "title": "Submitting a valid FNOL returns a claim number",
      "body": "",
      "source": "docs/requirements.md:29",
      "author": "intent-import(docs)",
      "asserted_at": "<utc iso>",
      "asserted_commit": "<sha | null>",
      "status": "active | superseded | needs-review",
      "meta": {"binding": "import"}
    }
  ],
  "edges": [
    {
      "src": "<code node id>", "dst": "intent:requirement:...",
      "type": "realizes",
      "file": "<code evidence file>", "line": 123,
      "evidence": "assertion",
      "confidence": null,
      "last_verified_commit": null,
      "provenance": "asserted",
      "author": "<binder>",
      "asserted_at": "<utc iso>",
      "asserted_commit": "<sha | null>",
      "meta": {"binding": "manual", "why": "<plain-language reason>"}
    },
    {
      "src": "<node id>", "dst": "<node id>",
      "type": "contains | imports | references | calls | implements | extends | depends-on | injects | exposes | consumes | publishes | reads | writes | queries | invokes | transforms | configures | authenticates | authorizes | tests | deploys-to | defines | handled-by | guarded-by | navigates | creates | modifies | seeds | triggered-by | realizes | part-of | denotes | motivated-by | delivered-in",
      "file": "<evidence file>", "line": 123,
      "evidence": "source-code",
      "confidence": "HIGH | MEDIUM | LOW | UNKNOWN",
      "last_verified_commit": "<sha | null>",
      "provenance": "derived | asserted (absent means derived)",
      "meta": {"resolved": "name-match", "lang": "java"}
    }
  ],
  "flow_candidates": [{"seed": "<node id>", "kind": "endpoint|route|queue|topic|event|job",
                       "endpoint": "<node id>", "chain": ["<node id>", "..."]}],
  "unresolved": ["unresolved:method:Foo#bar"],
  "detection": {"languages": [{"name": "java", "confidence": "HIGH"}],
                "frameworks": [], "databases": [], "infrastructure": [],
                "unsupported": []},
  "scan_errors": []
}
```

(`endpoint` in flow candidates is a back-compat alias of `seed`.)

## Generic node kinds (closed)

`repository application module package directory file class interface enum
function method component service controller handler endpoint route guard
interceptor state event queue topic job schedule database table view query
procedure sequence collection cache cache-key entity stereotype
external-service configuration configuration-key environment deployment-unit
test test-case migration ci-job`

## Generic edge types (closed)

`contains imports references calls implements extends depends-on injects
exposes consumes publishes reads writes queries invokes transforms configures
authenticates authorizes tests deploys-to defines handled-by guarded-by
navigates creates modifies seeds triggered-by maps-to realizes part-of denotes
motivated-by delivered-in` (`violates` DEFERRED to a later wave)

`maps-to` is a declaration mapping (ORM entity ↔ table), not an access:
it carries no read/write semantics and is excluded from traversal that
answers "what touches this table" (see `REACH_EDGE_TYPES` in core.py).

## Intent layer (5.3 — ASSERTED, never analyzer-emitted)

Intent node kinds (closed): `capability requirement concept slice decision
non-goal`. Intent ids carry the `intent:` prefix
(`intent:<kind>:<slug>`), so bindings and intent nodes never collide with
analyzer-emitted ids.

Intent edge types: `realizes` (code→requirement/capability), `part-of`
(requirement→capability; slice→capability), `denotes` (symbol→concept),
`motivated-by` (code/slice→decision), `delivered-in` (code→slice).

Every intent node stores: `id`, `kind`, `title`, `body`, `source` (doc
file+line), `author`, `asserted_at`, `asserted_commit`, `status`
(`active`/`superseded`/`needs-review`). Every binding stores
`asserted_commit`; when a bound code node changes materially or is deleted,
`sync` marks the binding `needs-review` with the reason recorded
(`meta.review_reason`).

Single source of truth: `docs/*.md` remain authoritative; the graph holds
a parsed projection. `intent import` syncs docs→graph only; docs win on
disagreement. Intent entries survive rescan: they carry `provenance:
asserted` and a `docs/*.md` source path (never a scanned relpath), so the
sync purge — which drops entries file-attributed to rescanned paths —
cannot delete them, and a provenance guard keeps asserted entries
regardless.

Deferred (later waves): coverage, drift, `violates`/non-goal enforcement,
glossary, Capabilities view.

## Node id grammar (analyzer prefixes are namespaces, not kinds)

- `<lang>:class:<path.Name>` / `<lang>:method:<path.Name>#<m>` —
  e.g. `java:class:`, `cs:method:`, `py:function:`, `go:func:`, `rs:fn:`,
  `ts:component:` / `ts:service:` / `ts:guard:`.
- `endpoint:<METHOD> <path>` — e.g. `endpoint:POST /orders`,
  `endpoint:gRPC OrderService` (framework in meta).
- `route:<path>` — any router (Angular/React/Vue/…). `file:<relpath>`.
- `table:<name>` / `entity:<Name>` / `collection:<name>` / `dbobj:<name>` —
  relational / ORM / document / generic DDL objects.
- `migration:<V>` — any migration dialect (Flyway/Alembic/Django/Rails/raw;
  dialect in meta).
- `config-key:<k>` / `env:<K>` / `external:<svc>` / `config:<relpath>` /
  `ci:<file>` / `schedule:cron` / `stereotype:<fw>:<S>` / `queue:<name>` /
  `auth:realm`.
- `unresolved:*` — references with no unambiguous target (`method:`,
  `class:`, `component:`, `guard:`, `handler:`, `module:`, `instance:`).
  Explicit by design: report them as leads, never as confirmed architecture.
- Bare-name refs (`table:<t>`, `entity:<E>`, `sequence:<s>`, …) resolve when
  a node with that id exists, and are unverified leads when it does not
  (`is_placeholder(dst, ids)` in `analyzers/graph.py` — membership-aware,
  never prefix-only). The visualization draws only resolved edges.
- Fallback-analyzer output carries `meta.reason:
  unsupported-language-fallback` at LOW confidence.

## Freshness

`state/sync-state.json` records `last_sync_commit`/`last_sync_time`/counts.
`state/file-hashes.json` records sha256 per scanned file; `sync`/`validate`
use it to detect modifications git does not show (or when git is absent).
stale = file hash differs from last verified scan → record is invalidated,
never presented as authoritative.

## v1 → v2 migration

`sync` auto-migrates v1 graphs (`record→class`, `entity→class`,
`repository→class`, `frontend-component→component`,
`frontend-service→service`, `frontend-route→route`, `frontend-guard→guard`,
`frontend-interceptor→interceptor`, `ts-class→class`, `database-*→table/view/
procedure/sequence`, `environment-variable→environment`,
`authentication→configuration`, `deployment→deployment-unit`;
`stereotyped-as→references`, `instantiates→references`). `validate`
reports STALE_SCHEMA instead; `init --full` always rebuilds cleanly.

## v2 → v3 migration

`sync` (and the intent commands) auto-migrate v2 graphs by stamping
`provenance: derived` on every node/edge (absent already meant derived, so
semantics are preserved exactly). `migrate_v1` bumps 1→2 and `migrate_v2`
bumps 2→3, chained in order. `validate` reports STALE_SCHEMA for any
non-v3 graph; `init --full` always rebuilds cleanly at v3.
