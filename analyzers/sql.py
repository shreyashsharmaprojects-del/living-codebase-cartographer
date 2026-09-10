"""Generic SQL analyzer: DDL/DML across migration dialects.

Databases are NOT assumed: the analyzer records tables/views/sequences/
procedures generically and stores the dialect hint (flyway, alembic,
django, rails, raw) in meta. Document/NoSQL specifics are handled by the
language analyzers (pymongo, redis) and the manifest analyzer; this module
covers SQL text in .sql files.
"""

import os
import re

NAME = "sql"
KIND = "database"
EXTENSIONS = {".sql"}
FILENAMES = set()
PATH_HINTS = ("migration", "migrate", "schema", "ddl", "sql")
PRIORITY = 30

# The object-type word is captured (group 1) so classification never depends
# on substring tests like "VIEW" in stmt (a table named `review` is a table).
CREATE_RE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?"
    r"(?:TEMP(?:ORARY)?\s+|UNLOGGED\s+|GLOBAL\s+|LOCAL\s+)?"
    r"(TABLE|MATERIALIZED|VIEW|SEQUENCE|FUNCTION|PROCEDURE|TRIGGER"
    r"|INDEX|UNIQUE)"
    r"(?:\s+VIEW|\s+INDEX)?"
    r"(?:\s+IF\s+NOT\s+EXISTS)?\s+(?:ONLY\s+)?([\"']?)([\w.]+)\2",
    re.IGNORECASE)
ALTER_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?([\"']?)([\w.]+)\1", re.IGNORECASE)
INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+([\"']?)([\w.]+)\1", re.IGNORECASE)
# Group 1 is the object kind (TABLE vs VIEW/... select the id namespace);
# groups 2/3 are quote/name. (An old revision read group(1) as the name,
# producing empty `table:` edge targets on every DROP.)
DROP_RE = re.compile(
    r"DROP\s+(TABLE|VIEW|SEQUENCE|FUNCTION|PROCEDURE|TRIGGER|INDEX)"
    r"(?:\s+IF\s+EXISTS)?\s+([\"']?)([\w.]+)\2",
    re.IGNORECASE)
ADD_COL_RE = re.compile(
    r"ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?([\"']?)([\w]+)\1",
    re.IGNORECASE)
_IDENT_RE = re.compile(
    r'"([^"]+)"|\'([^\']+)\'|`([^`]+)`|\[([^\]]+)\]|([\w]+)')
_CONSTRAINT_WORDS = frozenset({
    "CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "KEY",
    "INDEX", "LIKE", "EXCLUDE", "PERIOD",
})
_MAX_COLUMNS = 100
_MAX_ID = 512


def can_handle(path, text=None):
    return path.endswith(".sql")


def _sid(*parts):
    """Single-line node/edge id, capped at 512 chars."""
    s = " ".join("".join(parts).split())
    return s if len(s) <= _MAX_ID else s[:_MAX_ID]


def _dialect(rel):
    if re.match(r"(V\d+|U\d+)__", os.path.basename(rel)):
        return "flyway"
    if "alembic" in rel or re.match(r"[0-9a-f]{12}_", os.path.basename(rel)):
        return "alembic"
    if "migrations" in rel and "django" in rel:
        return "django"
    if re.match(r"\d{14}_", os.path.basename(rel)):
        return "rails"
    return "raw-sql"


def _tail_name(text, m, group):
    """Last dot-segment of a possibly schema-qualified/quoted object name.

    The bare `([\"']?)([\\w.]+)\\1` shape cannot span `"public"."claim"`,
    so quoted/bare `.segment` continuations past the match end are consumed
    here. Returns (lowercased name, end offset after the full name).
    """
    raw = m.group(group)
    end = m.end()
    while end < len(text) and text[end] == ".":
        nm = _IDENT_RE.match(text, end + 1)
        if not nm:
            break
        raw = nm.group(0)
        end = nm.end()
    seg = raw.strip("\"'`[]").split(".")[-1].strip("\"'`[]")
    return seg.lower(), end


def _columns_of(text, start):
    """Column names from a CREATE TABLE (...) body; [] when absent.

    Balanced-paren scan (quote-aware, bounded) so types like NUMERIC(10,2)
    and CHECK constraints do not split the list; constraint clauses
    contribute no column names.
    """
    i = start
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    if i >= len(text) or text[i] != "(":
        return []
    depth, j, n = 0, i, len(text)
    quote = None
    while j < n and j - i < 20000:
        ch = text[j]
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"', "`"):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    if depth != 0:
        return []
    cols = []
    part, pdepth, pquote = [], 0, None

    def flush():
        seg = "".join(part).strip()
        del part[:]
        if not seg:
            return
        m = _IDENT_RE.match(seg)
        if not m:
            return
        name = next((g for g in m.groups() if g), "")
        if name and name.upper() not in _CONSTRAINT_WORDS \
                and len(cols) < _MAX_COLUMNS:
            cols.append(name.lower())

    for ch in text[i + 1:j]:
        if pquote:
            part.append(ch)
            if ch == pquote:
                pquote = None
        elif ch in ("'", '"', "`"):
            pquote = ch
            part.append(ch)
        elif ch == "(":
            pdepth += 1
            part.append(ch)
        elif ch == ")":
            pdepth -= 1
            part.append(ch)
        elif ch == "," and pdepth == 0:
            flush()
        else:
            part.append(ch)
    flush()
    return cols


def scan(ctx, path, text):
    rel = ctx.path
    dialect = _dialect(rel)
    base = os.path.basename(rel)
    fid = _sid(f"file:{rel}")
    ctx.node(fid, "file", base, 1, "HIGH",
             {"role": "source", "dialect": dialect})
    if dialect == "flyway":
        m = re.match(r"((?:V|U)\d+)__(.+)\.sql", base)
        version = m.group(1) if m else base
        mid = _sid(f"migration:{version}")
        ctx.node(mid, "migration", base, 1, "HIGH",
                 {"dialect": dialect, "version": version})
    else:
        mid = _sid(f"migration:{base}")
        ctx.node(mid, "migration", base, 1, "MEDIUM",
                 {"dialect": dialect})
    ctx.edge(fid, mid, "defines", 1, "HIGH", {})
    nodes = {}  # node id -> node dict, for same-file column enrichment
    for sm in CREATE_RE.finditer(text):
        kind_word = sm.group(1).upper()
        if kind_word in ("INDEX", "UNIQUE"):
            continue  # indexes are performance metadata, not architecture
        name, end = _tail_name(text, sm, 3)
        line = text[:sm.start()].count("\n") + 1
        meta = {"dialect": dialect, "migration": base}
        if kind_word == "TABLE":
            nkind, nid = "table", _sid(f"table:{name}")
            cols = _columns_of(text, end)
            if cols:
                meta["columns"] = cols
        elif kind_word in ("VIEW", "MATERIALIZED"):
            nkind, nid = "view", _sid(f"dbobj:{name}")
        elif kind_word == "SEQUENCE":
            nkind, nid = "sequence", _sid(f"dbobj:{name}")
        else:
            nkind, nid = "procedure", _sid(f"dbobj:{name}")
        nodes[nid] = ctx.node(nid, nkind, name, line, "HIGH", meta)
        ctx.edge(mid, nid, "creates", line, "HIGH", {})
        ctx.edge(fid, nid, "defines", line, "HIGH", {})
    for sm in ALTER_RE.finditer(text):
        name, end = _tail_name(text, sm, 2)
        line = text[:sm.start()].count("\n") + 1
        ctx.edge(mid, _sid(f"table:{name}"), "modifies", line, "HIGH",
                 {"dialect": dialect})
        # ALTER ... ADD COLUMN enriches this file's own table node only;
        # nodes owned by other files are never mutated.
        nid = _sid(f"table:{name}")
        node = nodes.get(nid)
        if node is not None and node.get("file") == rel:
            semi = text.find(";", end)
            if semi < 0 or semi - end > 2000:
                semi = min(len(text), end + 2000)
            am = ADD_COL_RE.search(text[end:semi])
            if am:
                cols = node["meta"].setdefault("columns", [])
                col = am.group(2).lower()
                if col and col.upper() not in _CONSTRAINT_WORDS \
                        and col not in cols \
                        and len(cols) < _MAX_COLUMNS:
                    cols.append(col)
    for sm in INSERT_RE.finditer(text):
        name, _ = _tail_name(text, sm, 2)
        line = text[:sm.start()].count("\n") + 1
        ctx.edge(mid, _sid(f"table:{name}"), "seeds", line, "MEDIUM",
                 {"dialect": dialect})
    for sm in DROP_RE.finditer(text):
        kind_word = sm.group(1).upper()
        if kind_word == "INDEX":
            continue
        name, _ = _tail_name(text, sm, 3)
        line = text[:sm.start()].count("\n") + 1
        if kind_word == "TABLE":
            dst = _sid(f"table:{name}")
        else:
            dst = _sid(f"dbobj:{name}")
        ctx.edge(mid, dst, "modifies", line, "HIGH",
                 {"dialect": dialect, "op": "drop"})
