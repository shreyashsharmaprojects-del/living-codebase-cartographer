"""Living Codebase Cartographer — analyzer plugin contract.

An analyzer is a small, self-contained module that contributes deterministic
tool evidence for one technology area (a language, a framework, a database
family, configuration, infrastructure...). Analyzers NEVER guess: every node
and edge they emit must cite its source file, line, and confidence.

The core owns the generic graph (see `analyzers/graph.py`), technology detection,
file walking, incremental sync, resolution, views, and CLI. Analyzers only
implement this interface:

    NAME = "java"            # unique kebab-case analyzer name
    KIND = "language"        # language | framework | database | config | infra

    # File selection. At least one must be non-empty.
    EXTENSIONS = {".java"}   # matched against the file's lowercase suffix
    FILENAMES = set()        # matched against the basename (e.g. "Dockerfile")
    PATH_HINTS = ()          # substring hints, e.g. ("db/migration",)

    # Ordering: lower runs first so framework analyzers can build on language
    # nodes. Core/generic analyzers use 0-19, language 10, framework 20+,
    # database 30+, config/infra 40+.
    PRIORITY = 10

    def can_handle(path, text=None):
        # Optional fast pre-check beyond extensions (e.g. content sniffing).
        # Return True/False. Default implementation returns True.
        return True

    def scan(ctx, path, text):
        # Emit nodes/edges via ctx.node(...) / ctx.edge(...). Must never raise
        # for a single file: catch per-construct errors internally and record
        # them with ctx.scan_error(exc).
        ...

The `ctx` object (see `analyzers/context.py`) exposes:

    ctx.node(id, kind, name, line=None, confidence="HIGH", meta=None)
    ctx.edge(src, dst, type, line=None, confidence="MEDIUM", meta=None)
    ctx.scan_error(exc)          # per-file robustness
    ctx.path                   # current relpath being scanned
    ctx.commit                 # last-verified commit for this scan

Generic node kinds (closed vocabulary — see `analyzers/graph.py::NODE_KINDS`):
generic edge types (closed vocabulary — see `analyzers/graph.py::EDGE_TYPES`).

Technology specificity belongs in `meta` (e.g. meta={"lang": "java",
"framework": "spring", "stereotype": "RestController"}) — NEVER in new node
kinds or edge types. If no existing kind fits, use the closest generic one
(class/function/file/configuration/...) and record the nuance in meta.

Confidence rules for analyzers:
  HIGH   — direct declaration evidence (class/function/endpoint/route/table
           definitions, config keys, manifest dependencies).
  MEDIUM — name-resolved references (DI injection, resolved call targets,
           frontend -> endpoint consumption with matching route).
  LOW    — heuristic/textual matches, unsupported-language fallback output.
           Investigative leads only; never present as confirmed architecture.
  UNKNOWN — reserved for agent assertions without evidence. Analyzers never emit
           UNKNOWN; they emit LOW with meta={"reason": ...} instead.

To add support for a new language/framework (e.g. Kotlin, Ruby, PHP):
  1. Create `analyzers/<name>.py` implementing the interface above.
  2. Register it in `analyzers/__init__.py::ANALYZERS` (append to the list).
  3. Add a fixture + test in `tests/run_tests.py::test_closed_schema`
     (plus a same-file `calls` case in `test_same_file_calls` when the
     language has callable functions/methods).
No core changes are needed. New node kinds or edge types require a core
schema change and must be justified as genuinely technology-independent.

Every language analyzer with callable units SHOULD emit same-file `calls`
edges at HIGH confidence (`meta={"via": "same-file"}`): pass 1 collects the
defined function/method names in the file, pass 2 links call sites —
including calls on the definition line itself (one-liner bodies). This is
what makes `map-flow` / `map-impact` work on every stack, not just Java.
"""
