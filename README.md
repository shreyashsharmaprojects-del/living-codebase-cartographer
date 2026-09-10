# Living Codebase Cartographer — offline codebase map, dependency graph & impact analysis (no LSP, no cloud)

> **Map any codebase offline in seconds: endpoints, call graphs, data lineage,
> blast-radius (impact) analysis, and request-flow tracing — with evidence +
> confidence on every fact. Zero dependencies, zero network calls, zero
> telemetry.** Python stdlib only, works without an LSP or language server,
> generates a portable offline HTML architecture explorer that opens over
> `file://`.

A **language-agnostic, framework-agnostic living codebase intelligence system**.
It builds and maintains a machine-readable map of any software repository —
symbols, API endpoints, call graphs, data stores, dependencies, call edges —
with evidence + confidence on every fact, and keeps it synchronized as the
code evolves. Think: an **offline alternative** to cloud code-graph tools, an
**AI-agent skill for repo mapping**, and a **standalone static-analysis CLI**
in one package.

**Keywords:** codebase map generator, repository mapping tool, static code
analysis python, offline code intelligence, dependency graph visualizer,
call-graph generator, blast-radius / impact analysis, API endpoint inventory,
data-lineage tracker, architecture explorer, monorepo documentation generator,
AI coding-assistant skill, DeepSeek / Claude / agentic-coding skill, no-LSP
code navigation, `file://` HTML code browser.

Supported out of the box: Java, TypeScript/JavaScript, Python, Go, C#, Rust,
SQL (any DDL dialect), manifests/config/CI (generic), plus a LOW-confidence
fallback pass for anything else (Ruby, PHP, Kotlin, Swift, …). Technology
identity lives in `meta` (`lang`, `framework`, `dialect`); the graph schema
itself is closed and technology-independent.

## Requirements

- Python 3.8+ (stdlib only — no third-party dependencies)
- `git` (for change detection; works without git, using file hashes)

## Install

Clone the repo, then copy it into your project as a skill:

```bash
git clone https://github.com/shreyashsharmaprojects-del/living-codebase-cartographer.git
rsync -a --exclude __pycache__ --exclude '*.pyc' \
  living-codebase-cartographer/ <your-project>/.dsh/skills/living-codebase-cartographer/
# (or <your-project>/.claude/skills/ — the scanner excludes its own
# install dir at runtime, so location does not matter)
```

There is nothing to install — the scanner is stdlib-only.

## Quick start

From your repository root:

```bash
# 1. Build the map (detects your stack, writes .codebase-map/)
python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py init

# 2. Check freshness before asking architecture questions
python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py status

# 3. Ask questions
python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py query --name ClaimService
python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py impact ClaimService
python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py flow --from "POST /api/claims" --to ClaimService

# 4. After changing code, re-sync (incremental — only changed files rescanned)
python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py sync

# 5. Interactive architecture explorer (offline HTML, works over file://)
python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py visualize --open
```

Other commands: `validate` (integrity + schema conformance), `detect`
(evidence-based stack report).

## Layout

```
living-codebase-cartographer/
├── SKILL.md                 # agent operating manual (used by AI assistants)
├── README.md                # this file
├── LICENSE                  # MIT
├── scripts/
│   ├── cartographer.py      # thin CLI entry point
│   ├── core.py              # engine: walking, dispatch, resolve, sync, CLI
│   ├── viz.py               # graph → read-only view-model projection
│   ├── viz_gen.py           # view-model → portable HTML artifact
│   ├── viz_template_head.html / viz_template_p2.js / viz_template_p3.js
├── analyzers/               # one module per technology area
│   ├── README.md            # plugin contract (add Kotlin/Ruby/PHP/… here)
│   ├── graph.py             # closed generic schema (kinds, edge types)
│   ├── context.py           # per-file scan context
│   ├── __init__.py          # registry + evidence-based tech detection
│   ├── java|typescript|python|go|csharp|rust|sql|config|fallback.py
├── references/              # runbooks: operations, schema, flows, visualization
└── tests/
    ├── run_tests.py         # analysis suite (schema, detection, e2e)
    └── run_viz_tests.py     # visualization suite (projection, templates, layout)
```

Run the suites before publishing or after any analyzer change:

```bash
python3 tests/run_tests.py        # 166 checks
python3 tests/run_viz_tests.py    # 81 checks
```

## Design rules

1. **Tool evidence over assumptions.** Unknown relationships stay unknown.
2. **Confidence is explicit:** HIGH (declaration), MEDIUM (resolved
   reference), LOW (heuristic/fallback lead), UNKNOWN (agent assertion only).
   LOW is never presented as confirmed.
3. **Analysis-only.** The skill never modifies production code.
4. **No secrets.** `.env` files are skipped entirely — nothing from them is
   stored, not even key names.
5. **Source wins.** When map and source conflict, the map is updated.
6. **Read-only visualization.** The HTML explorer is a view over
   `.codebase-map/graph.json`, never a second source of truth.

## Adding a language

See `analyzers/README.md` — implement `NAME/KIND/EXTENSIONS/can_handle/scan`,
register in `analyzers/__init__.py`, add fixtures to `tests/run_tests.py`.
Emit same-file HIGH `calls` edges so `flow`/`impact` work on your stack.

## FAQ — is this what you're looking for?

**A DeepSeek skill for codebase mapping?**
Yes — this repo ships a `SKILL.md` agent manual plus a deterministic scanner,
built for agentic-coding loops (DeepSeek, Claude, or any tool-using LLM):
`status` → `query`/`impact`/`flow` → read source → answer with evidence links.
It also works fully standalone with no AI involved.

**A codebase cartographer / repo-map generator?**
That's the core job: `init` scans any repo into `graph.json` (nodes + edges +
evidence + confidence), `sync` keeps it fresh incrementally, `visualize`
renders it as an interactive map.

**A standalone offline tool (no cloud, no LSP, no telemetry)?**
Yes. Python 3.8+ stdlib only — no `pip install`, no language server, no
network calls, no telemetry, no build step. The HTML explorer opens directly
over `file://`. Air-gapped environments welcome.

**A dependency-graph / call-graph visualizer?**
Yes — nine graph modes (architecture, dependency, call-graph, data-flow, api,
database, external, impact, flow) plus eight structured views (Overview,
Endpoints, Data, Dependencies, Symbols, Flows, Graph, Issues), with
left-to-right layered layout, confidence-encoded edges, and editor deep-links.

**An impact-analysis / blast-radius tool?**
Yes — `impact <Symbol>` computes reverse-reachability (callers, consumers,
entry points) before you change code; `flow --from A --to B` traces request
paths from frontend through API, logic, and data layers.

**Which languages are supported?**
Java, TypeScript/JavaScript (incl. React/Vue patterns), Python
(FastAPI/Flask/Django/Celery), Go (HTTP/gRPC/Kafka), C# (ASP.NET/gRPC),
Rust, SQL DDL dialects, manifests/config/CI — plus a LOW-confidence fallback
pass for anything else (Ruby, PHP, Kotlin, Swift, …). The schema itself is
closed and technology-independent, so new languages plug in as analyzers.
