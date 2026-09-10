"""C# / .NET analyzer: namespaces, classes, methods, MVC/minimal-API endpoints.

Generic kinds only; ASP.NET/Core specifics (attributes, EF DbSets, MediatR,
Hangfire/Quartz jobs) live in meta.
"""

import re

NAME = "csharp"
KIND = "language"
EXTENSIONS = {".cs"}
FILENAMES = set()
PATH_HINTS = ()
PRIORITY = 10

LANG = "csharp"

NS_RE = re.compile(r"^\s*namespace\s+([\w.]+)", re.M)
CLASS_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|sealed|abstract|static|partial)\s+)*"
    r"(?:class|interface|enum|record|struct)\s+(\w+)", re.M)
METHOD_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|virtual|override|async|sealed)[ \t]+)*"
    r"(?:[\w<>\[\]?., \t]+[ \t]+)?(\w+)[ \t]*\([^;{}\n]*\)[ \t]*[{;]?[ \t]*.*$")
HTTP_ATTR_RE = re.compile(
    r"\[(HttpGet|HttpPost|HttpPut|HttpDelete|HttpPatch|Route)"
    r"(?:\s*\(\s*(?:\"([^\"]*)\"|template\s*=\s*\"([^\"]*)\"))?")
MAP_ROUTE_RE = re.compile(
    r"""app\.Map(?:Get|Post|Put|Delete|Patch)\s*\(\s*["']([^"']+)["']""")
EF_DBSET_RE = re.compile(r"DbSet\s*<\s*(\w+)\s*>\s+(\w+)")
# Raw SQL (Dapper-style conn.Execute/Query/SqlQuery, ADO.NET SqlCommand):
# string-literal DML is evidence of table access. Verb decides the edge
# type (mirrors python's _sql_literal_edges and java's SQL_VERB_RE):
# SELECT -> reads, INSERT/UPDATE/DELETE -> writes. MEDIUM = literal.
SQL_VERB_RE = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
SQL_SINK_RE = re.compile(
    r"\b(?:Execute|ExecuteAsync|Query|QueryAsync|QuerySingle|QueryFirst"
    r"|ExecuteScalar|ExecuteReader|SqlQuery|FromSqlRaw|FromSqlInterpolated)"
    r"\s*(?:<[^>]*>)?\s*\(\s*(@?\"(?:[^\"\\]|\\.)*\")", re.IGNORECASE)
SQL_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+([A-Za-z_][\w]*)", re.IGNORECASE)
SQL_SKIP_WORDS = frozenset({
    "select", "where", "set", "values", "order", "group", "by", "and",
    "or", "on", "as", "limit", "having", "offset"})
# [Table("orders")] / [Table("orders", Schema = "dbo")]: order-independent.
TABLE_ATTR_RE = re.compile(r"\[Table\s*\(\s*\"([^\"]+)\"")
GRPC_RE = re.compile(r":\s*([\w.]+)\.([\w.]+)Base\b")
HANGFIRE_RE = re.compile(r"RecurringJob\.AddOrUpdate|BackgroundJob\.(Enqueue|Schedule)")
USING_RE = re.compile(r"^\s*using\s+([\w.]+)\s*;", re.M)
# Same-file call detection (mirrors java's same-class HIGH pattern).
CALL_RE = re.compile(r"\b([A-Za-z_][\w]*)\s*\.\s*([a-zA-Z_][\w]*)\s*\("
                     r"|\b([a-zA-Z_][\w]*)\s*\(")
CALL_KEYWORDS = frozenset({
    "if", "for", "foreach", "while", "switch", "catch", "return", "new",
    "throw", "using", "lock", "else", "do", "try", "nameof", "typeof",
})


def can_handle(path, text=None):
    return path.endswith(".cs")


def _http_of(attr):
    return {"HttpGet": "GET", "HttpPost": "POST", "HttpPut": "PUT",
            "HttpDelete": "DELETE", "HttpPatch": "PATCH"}.get(attr, "GET")


def _calls_in(ctx, src, body, i, ns, cls, defined_methods):
    """Emit same-file calls edges for call sites found in `body`."""
    for _cm in CALL_RE.finditer(body):
        _t = _cm.group(2) or _cm.group(3)
        if not _t or _t in CALL_KEYWORDS:
            continue
        if _t in defined_methods:
            _dst = (f"cs:method:{ns}.{cls}#{_t}" if ns
                    else f"cs:method:{cls}#{_t}")
            if _dst != src:
                ctx.edge(src, _dst, "calls", i, "HIGH",
                         {"via": "same-file", "lang": LANG})
        # cross-class receiver calls stay unresolved (MEDIUM at best)
        elif _cm.group(1) and _cm.group(1)[0].islower():
            ctx.edge(src, f"unresolved:method:{_cm.group(1)}#{_t}",
                     "calls", i, "MEDIUM",
                     {"receiver": _cm.group(1), "lang": LANG})


def scan(ctx, path, text):
    rel = ctx.path
    ctx.node(f"file:{rel}", "file", rel.split("/")[-1], 1, "HIGH",
             {"lang": LANG,
              "role": "test" if ("Test" in rel or "test" in rel) else "source"})
    ns = ""
    m = NS_RE.search(text)
    if m:
        ns = m.group(1)
    for m in USING_RE.finditer(text):
        line = text[:m.start()].count("\n") + 1
        ctx.edge(f"file:{rel}", f"unresolved:module:{m.group(1)}",
                 "imports", line, "MEDIUM", {"lang": LANG})
    cls = None
    pending_route = None
    pending_table_attr = None  # [Table("...")] line awaiting its class
    defined_methods = set()  # method names in this file (for same-file calls)
    # Line-anchored pre-pass: METHOD_RE has no re.M (see def above), so run
    # it per line — a whole-text finditer would let the return-type class
    # swallow newlines and register call-site names (e.g. a nested
    # `Phantom()` on its own line) as defined methods, producing dangling
    # HIGH `calls` edges with no corresponding node.
    for _line in text.splitlines():
        _dm = METHOD_RE.match(_line)
        if _dm and "(" in _line and re.match(
                r"^\s*(?:public|private|protected|internal|static|async|virtual|override)",
                _line) and "=" not in _line.split("(")[0]:
            _mn = _dm.group(1)
            if _mn not in ("if", "for", "while", "switch", "catch",
                           "return", "new"):
                defined_methods.add(_mn)
    current_method = None  # node-id of enclosing method, if any
    pre_lines = text.splitlines()
    lines = pre_lines
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        tm_attr = TABLE_ATTR_RE.search(stripped)
        if tm_attr:
            pending_table_attr = (tm_attr.group(1), i)
        am = HTTP_ATTR_RE.search(stripped)
        if am:
            sub = am.group(2) or am.group(3) or ""
            if am.group(1) == "Route":
                pending_route = ("*", sub, i)
            else:
                pending_route = (_http_of(am.group(1)), sub, i)
            continue
        for mm in MAP_ROUTE_RE.finditer(line):
            route = mm.group(1)
            eid = f"endpoint:* {route}"
            ctx.node(eid, "endpoint", f"* {route}", i, "MEDIUM",
                     {"lang": LANG, "framework": "aspnet-minimal"})
            ctx.edge(f"file:{rel}", eid, "defines", i, "MEDIUM",
                     {"lang": LANG})
        cm = CLASS_RE.match(line)
        if cm and cls is None:
            cls = cm.group(1)
            nkind = ("interface" if "interface " in line
                     else "enum" if "enum " in line else "class")
            if "Controller" in cls:
                nkind = "controller"
            elif "Service" in cls or "Handler" in cls:
                nkind = "service" if "Service" in cls else "handler"
            ctx.node(f"cs:class:{ns}.{cls}" if ns else f"cs:class:{cls}",
                     nkind, cls, i, "HIGH",
                     {"lang": LANG, "framework": "dotnet",
                      "namespace": ns})
            clsid = (f"cs:class:{ns}.{cls}" if ns else f"cs:class:{cls}")
            ctx.edge(f"file:{rel}", clsid, "defines", i, "HIGH", {})
            # DbSets may sit anywhere in the class body (next-line window
            # misses them when methods come first), so scan the whole file
            # and attribute every DbSet<T> to this class. Declaration =>
            # HIGH edges; the DbContext reads and writes each set's table.
            for dm in EF_DBSET_RE.finditer(text):
                dline = text[:dm.start()].count("\n") + 1
                tbl = dm.group(1).lower()
                tid = f"table:{tbl}"
                ctx.node(tid, "table", tbl, dline, "HIGH",
                         {"lang": LANG, "via": "ef-dbset",
                          "model": dm.group(1)})
                ctx.edge(f"file:{rel}", tid, "defines", dline,
                         "HIGH", {})
                ctx.edge(
                    f"cs:class:{ns}.{cls}" if ns else f"cs:class:{cls}",
                    tid, "reads", dline, "HIGH",
                    {"via": "ef-dbset", "lang": LANG})
                ctx.edge(
                    f"cs:class:{ns}.{cls}" if ns else f"cs:class:{cls}",
                    tid, "writes", dline, "HIGH",
                    {"via": "ef-dbset", "lang": LANG})
                ctx.edge(
                    f"cs:class:{ns}.{cls}" if ns else f"cs:class:{cls}",
                    f"entity:{dm.group(1)}", "reads", dline, "MEDIUM",
                    {"via": "ef-dbset", "lang": LANG})
            if pending_table_attr:
                tname, tline = pending_table_attr
                tid = f"table:{tname.lower()}"
                ctx.node(tid, "table", tname.lower(), tline, "HIGH",
                         {"lang": LANG, "via": "ef-table",
                          "model": cls})
                ctx.edge(f"file:{rel}", tid, "defines", tline,
                         "HIGH", {})
                ctx.edge(
                    f"cs:class:{ns}.{cls}" if ns else f"cs:class:{cls}",
                    tid, "reads", tline, "MEDIUM",
                    {"via": "ef-table", "lang": LANG})
                ctx.edge(
                    f"cs:class:{ns}.{cls}" if ns else f"cs:class:{cls}",
                    tid, "writes", tline, "MEDIUM",
                    {"via": "ef-table", "lang": LANG})
                pending_table_attr = None
            gm = GRPC_RE.search(line)
            if gm:
                eid = f"endpoint:gRPC {gm.group(1)}.{gm.group(2)}"
                ctx.node(eid, "endpoint", f"gRPC {gm.group(2)}", i,
                         "MEDIUM", {"lang": LANG, "via": "grpc"})
                ctx.edge(f"file:{rel}", eid, "defines", i, "MEDIUM",
                         {"lang": LANG})
                ctx.edge(
                    f"cs:class:{ns}.{cls}" if ns else f"cs:class:{cls}",
                    eid, "exposes", i, "MEDIUM", {"lang": LANG})
            continue
        if cls is None:
            continue
        mm = METHOD_RE.match(line)
        if mm and "(" in line and re.match(
                r"^\s*(?:public|private|protected|internal|static|async|virtual|override)",
                line) and "=" not in line.split("(")[0]:
            mname = mm.group(1)
            if mname in ("if", "for", "while", "switch", "catch",
                         "return", "new"):
                continue
            mid = (f"cs:method:{ns}.{cls}#{mname}" if ns
                   else f"cs:method:{cls}#{mname}")
            ctx.node(mid, "method", f"{cls}.{mname}", i, "HIGH",
                     {"lang": LANG})
            clsid = (f"cs:class:{ns}.{cls}" if ns else f"cs:class:{cls}")
            ctx.edge(f"file:{rel}", mid, "defines", i, "HIGH", {})
            ctx.edge(clsid, mid, "defines", i, "HIGH", {})
            current_method = mid
            if pending_route:
                http, sub, mline = pending_route
                full = (sub or "/")
                full = "/" + full.strip("/")
                eid = f"endpoint:{http} {full}"
                ctx.node(eid, "endpoint", f"{http} {full}", mline,
                         "HIGH", {"lang": LANG, "framework": "aspnet",
                                  "handler": mid, "controller": cls})
                ctx.edge(f"file:{rel}", eid, "defines", mline,
                         "HIGH", {})
                ctx.edge(eid, mid, "handled-by", mline, "HIGH", {})
                pending_route = None
            if HANGFIRE_RE.search(line):
                ctx.edge(mid, "schedule:cron", "triggered-by", i,
                         "MEDIUM", {"lang": LANG})
            # calls on the definition line itself (one-liner method bodies)
            if "{" in line:
                _calls_in(ctx, mid, line.split("{", 1)[1], i,
                          ns, cls, defined_methods)
            continue
        # same-file calls: enclosing method -> defined method (HIGH)
        if current_method and "(" in line and cls is not None:
            _calls_in(ctx, current_method, line, i,
                      ns, cls, defined_methods)
        # Raw SQL in Dapper/ADO.NET sinks: string-literal DML on this line
        # is table-access evidence. Verb decides reads vs writes; each
        # table resolves as a bare table: id (membership-aware placeholder
        # per graph.is_placeholder — never an invented node).
        if ('"' in line or "'" in line) and re.search(
                r"(?i)\b(select|insert|update|delete|from|join)\b", line):
            for sm in SQL_SINK_RE.finditer(line):
                lit = sm.group(1)
                if lit.startswith("@"):
                    lit = '"' + lit[1:] + '"'
                vm = SQL_VERB_RE.search(lit)
                if not vm:
                    continue
                verb = vm.group(1).upper()
                etype = "reads" if verb == "SELECT" else "writes"
                seen = set()
                for tm in SQL_TABLE_RE.finditer(lit):
                    tbl = tm.group(1).lower()
                    if tbl in SQL_SKIP_WORDS or tbl in seen:
                        continue
                    seen.add(tbl)
                    src = current_method or (
                        f"cs:class:{ns}.{cls}" if ns
                        else f"cs:class:{cls}")
                    ctx.edge(src, f"table:{tbl}", etype, i, "MEDIUM",
                             {"via": "dapper-sql", "lang": LANG})
