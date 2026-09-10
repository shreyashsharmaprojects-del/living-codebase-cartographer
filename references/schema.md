# graph.json schema — living-codebase-cartographer (v2, generic)

`graph.json` is the source of truth. All Markdown (except curated files) is derived.
The schema is **closed and technology-independent**: analyzers MUST use only
the kinds/edge types below. Technology identity lives in `meta`
(`lang`, `framework`, `stereotype`, `dialect`, …) — never in new kinds/types.
`validate` rejects violations.

```json
{
  "version": 2,
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
      "meta": {"lang": "java", "framework": "spring",
               "package": "com.example", "stereotypes": ["Service"]}
    }
  ],
  "edges": [
    {
      "src": "<node id>", "dst": "<node id>",
      "type": "contains | imports | references | calls | implements | extends | depends-on | injects | exposes | consumes | publishes | reads | writes | queries | invokes | transforms | configures | authenticates | authorizes | tests | deploys-to | defines | handled-by | guarded-by | navigates | creates | modifies | seeds | triggered-by",
      "file": "<evidence file>", "line": 123,
      "evidence": "source-code",
      "confidence": "HIGH | MEDIUM | LOW | UNKNOWN",
      "last_verified_commit": "<sha | null>",
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
navigates creates modifies seeds triggered-by`

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
