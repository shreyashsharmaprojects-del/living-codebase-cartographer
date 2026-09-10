"""Fallback analyzer: generic structure for unsupported languages.

Runs LAST (priority 100). For any source file no other analyzer claimed, it
extracts what is language-independent: file node, import-like lines,
symbol-looking definitions (best effort), and obvious HTTP route strings /
connection strings as LOW-confidence leads with an explicit
meta={"reason": "unsupported-language-fallback"} marker.

It never fails the scan and never emits above LOW for relationships.
Table-name hints stay claims-by-exclusion: bare `unresolved:` edge targets
(never invented `table:` nodes, HIGH, or MEDIUM) and always LOW.
"""

import os
import re

NAME = "fallback"
KIND = "generic"
EXTENSIONS = set()  # claims by exclusion, not extension
FILENAMES = set()
PATH_HINTS = ()
PRIORITY = 100

SOURCE_EXTS = {
    ".py", ".java", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".cs", ".rb", ".php", ".swift", ".kt", ".kts",
    ".scala", ".dart", ".ex", ".exs", ".erl", ".cpp", ".cc", ".cxx",
    ".h", ".hpp", ".c", ".m", ".mm", ".vue", ".svelte", ".sql",
}

_MAX_ID = 512
_ID_WS = re.compile(r"\s+")

# [ \t] (not \s) after ^ under re.M: \s would swallow the preceding
# newlines, attributing every match to the nearest blank line above.
IMPORT_LIKE = re.compile(
    r"^[ \t]*(?:import|include|require|use|using|from|load|#include)\b"
    r"[ \t]*[\"'<]?([\w./:@-]+)[\"'>]?", re.M)
DEF_LIKE = re.compile(
    r"^[ \t]*(?:class|interface|struct|enum|trait|func(?:tion)?|def|fn|sub|"
    r"public[ \t]+(?:class|interface)|type|component)[ \t]+"
    r"([A-Za-z_][\w]*)",
    re.M)
ROUTE_STRING = re.compile(r"""["']((?:/api|/v\d)/[\w/{}\-_.]*)["']""")
# Table-name evidence stays a claim-by-exclusion: lowercase bare words are
# convention-only, and SQL is case-insensitive, so this is LOW at most.
TABLE_HINT = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE)\s+(?:ONLY\s+)?(?:[\w.]+\.)?"
    r"([a-z][a-z0-9_]*)\b", re.IGNORECASE)
# SQL keywords that often follow FROM/JOIN in odd-but-valid syntax; these
# are grammar, not table names.
TABLE_STOPWORDS = frozenset({
    "select", "where", "set", "values", "order", "group", "limit",
    "offset", "returning", "on", "using", "lateral",
})
# Annotation/decorator lines and quoted strings look like definitions or
# routes only to a textual scan; skip both so `@app.route('/x')`-style
# shims in unsupported languages do not mint phantom endpoints.
_ANNOTATION_LINE = re.compile(r"^[ \t]*[@#]")


def can_handle(path, text=None):
    ext = os.path.splitext(path)[1].lower()
    return ext in SOURCE_EXTS


def _sid(text):
    s = _ID_WS.sub(" ", text).strip()
    return s if len(s) <= _MAX_ID else s[:_MAX_ID]


def _line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def scan(ctx, path, text):
    rel = ctx.path
    reason = {"reason": "unsupported-language-fallback"}
    fid = _sid(f"file:{rel}")
    ctx.node(fid, "file", os.path.basename(rel), 1, "HIGH",
             {"role": "source"})
    for m in IMPORT_LIKE.finditer(text):
        target = m.group(1)
        line = _line_of(text, m.start())
        if target and not target.startswith((".", "/", "<")):
            ctx.edge(fid, _sid(f"unresolved:module:{target}"),
                     "imports", line, "LOW", dict(reason))
    seen_defs = set()
    for m in DEF_LIKE.finditer(text):
        line = _line_of(text, m.start())
        line_text = text.splitlines()[line - 1] \
            if line - 1 < len(text.splitlines()) else ""
        if _ANNOTATION_LINE.match(line_text):
            continue
        name = m.group(1)
        key = (name, m.start())
        if key in seen_defs:
            continue
        seen_defs.add(key)
        nid = _sid(f"fallback:{rel}::{name}")
        ctx.node(nid, "class", name, line, "LOW",
                 dict(reason, **{"via": "textual"}))
        ctx.edge(fid, nid, "defines", line, "LOW", dict(reason))
    for m in ROUTE_STRING.finditer(text):
        route = m.group(1)
        line = _line_of(text, m.start())
        eid = _sid(f"endpoint:* {route}")
        ctx.node(eid, "endpoint", f"* {route}", line, "LOW",
                 dict(reason, **{"via": "route-string"}))
        ctx.edge(fid, eid, "references", line, "LOW",
                 dict(reason))
    for m in TABLE_HINT.finditer(text):
        # Skip quoted literals ('claims' as a string is not a table read)
        # and anything on an annotation/comment line.
        q = m.start(1)
        prev = text[max(0, q - 1):q]
        if prev in ("'", '"', "`", "[", "."):
            continue
        name = m.group(1).lower()
        if name in TABLE_STOPWORDS:
            continue
        line = _line_of(text, m.start())
        # Never mint table: nodes (that would invent architecture);
        # point at a bare unresolved target so resolution succeeds only
        # when a real table node already exists in the graph.
        ctx.edge(fid, _sid(f"unresolved:table-ref:{name}"), "references",
                 line, "LOW", dict(reason, **{"via": "sql-text"}))
