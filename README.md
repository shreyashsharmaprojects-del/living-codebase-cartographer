# Living Codebase Cartographer

A **language-agnostic, framework-agnostic living codebase intelligence system**.
It builds and maintains a machine-readable map of any software repository —
symbols, endpoints, data stores, dependencies, call edges — with
evidence + confidence on every fact, and keeps it synchronized as the code
evolves.

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
