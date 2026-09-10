"""Go analyzer: packages, structs/interfaces, funcs, HTTP/gRPC/scheduler evidence.

All output uses generic kinds; go-specifics (goroutines, struct tags, chi/gin
routes, protobuf services) live in meta.
"""

import re

NAME = "go"
KIND = "language"
EXTENSIONS = {".go"}
FILENAMES = set()
PATH_HINTS = ()
PRIORITY = 10

LANG = "go"

PACKAGE_RE = re.compile(r"^\s*package\s+(\w+)", re.M)
TYPE_RE= re.compile(r"^\s*type\s+(\w+)\s+(struct|interface|[^\s{]+)", re.M)
FUNC_RE = re.compile(
    r"^\s*func\s+(?:\(\s*\w*\s*\*?(\w+)\s*\)\s*)?(\w+)\s*\(", re.M)
HTTP_ROUTE_RE = re.compile(
    r"""\.(?:HandleFunc|Handle|Get|Post|Put|Delete|Patch|Route|Mount)\s*\(\s*["`]([^"`]+)["`]""")
GRPC_RE = re.compile(r"Register(\w+)Server\s*\(\s*\w+\s*,\s*&?(\w+)")
GRPC_METHOD_RE = re.compile(
    r"func\s+\(\s*\w*\s*\*?(\w+)\s*\)\s*(\w+)\s*\(\s*(?:ctx\s+context\.Context|context\.Context)")
SCHED_RE = re.compile(r"""gocron|cron\.(?:New|Schedule)|AddFunc\s*\(""")
KAFKA_RE = re.compile(
    r"""(?:kafka|sarama|confluent)[\w.]*\.|New(?:Consumer|Producer|Reader|Writer)\s*\(""")
IMPORT_RE = re.compile(r'^\s*(?:"([\w./-]+)"|([\w.]+)\s+"([\w./-]+)")',
                       re.M)
SQL_VERB_RE = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
SQL_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+([a-z][a-z0-9_]*)\b", re.IGNORECASE)
# Same-file call detection (mirrors java's same-class HIGH pattern).
CALL_RE = re.compile(r"\b([A-Za-z_][\w]*)\s*\(")
CALL_KEYWORDS = frozenset({
    "if", "for", "switch", "select", "return", "go", "defer", "new",
    "make", "len", "cap", "append", "copy", "delete", "panic", "print",
    "println", "range", "map", "chan", "func", "type", "struct",
    "interface", "import", "package",
})


def _line_of(text, m):
    """Line number of a declaration match.

    Patterns anchored as ``^\\s*`` under re.M let ``m.start()`` sit on an
    earlier blank line (``\\s`` eats newlines), so the whole declaration is
    misattributed upward. The first participating capture always starts on
    the real declaration line — use that instead.
    """
    for i in range(1, m.re.groups + 1):
        try:
            s = m.start(i)
        except IndexError:
            continue
        if s != -1:
            return text[:s].count("\n") + 1
    return text[:m.start()].count("\n") + 1


def _func_id(pkg, recv, fname):
    if recv:
        return f"go:method:{pkg}.{recv}#{fname}"
    return f"go:func:{pkg}.{fname}"


def can_handle(path, text=None):
    return path.endswith(".go")


def scan(ctx, path, text):
    rel = ctx.path
    ctx.node(f"file:{rel}", "file", rel.split("/")[-1], 1, "HIGH",
             {"lang": LANG,
              "role": "test" if rel.endswith("_test.go") else "source"})
    pm = PACKAGE_RE.search(text)
    pkg = pm.group(1) if pm else ""
    defined = set()
    for m in FUNC_RE.finditer(text):
        recv, fname = m.group(1), m.group(2)
        line = _line_of(text, m)
        if fname in ("init", "main"):
            kind = "handler" if fname == "main" else "function"
        else:
            kind = "method" if recv else "function"
        nid = _func_id(pkg, recv, fname)
        defined.add(fname)
        ctx.node(nid, kind, fname,
                 line, "HIGH",
                 {"lang": LANG, "package": pkg,
                  "receiver": recv} if recv else {"lang": LANG,
                                                  "package": pkg})
        ctx.edge(f"file:{rel}", nid, "defines",
                 line, "HIGH", {})
        if recv:
            ctx.edge(f"go:type:{pkg}.{recv}", nid, "defines",
                     line, "HIGH", {})
    # same-file calls: enclosing func -> defined func (HIGH).
    # Walk lines, tracking the current func; braces are not tracked, so a
    # call is attributed to the most recent func definition above it.
    by_name = {}
    for m in FUNC_RE.finditer(text):
        recv, fname = m.group(1), m.group(2)
        by_name[fname] = _func_id(pkg, recv, fname)
    current = None
    for i, line in enumerate(text.splitlines(), start=1):
        fm = FUNC_RE.match(line)
        if fm:
            current = by_name.get(fm.group(2))
            # calls on the definition line itself (one-liner bodies)
            body = line.split("{", 1)[1] if "{" in line else ""
            for cm in CALL_RE.finditer(body):
                target = cm.group(1)
                if target in CALL_KEYWORDS or target == "func":
                    continue
                dst = by_name.get(target)
                if dst and dst != current:
                    ctx.edge(current, dst, "calls", i, "HIGH",
                             {"via": "same-file", "lang": LANG})
            continue
        if current is None or "(" not in line:
            continue
        stripped = line.strip()
        if stripped.startswith(("import ", "package ")):
            continue
        for cm in CALL_RE.finditer(line):
            target = cm.group(1)
            if target in CALL_KEYWORDS or target == "func":
                continue
            dst = by_name.get(target)
            if dst and dst != current:
                ctx.edge(current, dst, "calls", i, "HIGH",
                         {"via": "same-file", "lang": LANG})
    for m in TYPE_RE.finditer(text):
        tname, tkind = m.group(1), m.group(2)
        line = _line_of(text, m)
        nkind = ("interface" if tkind == "interface"
                 else "class" if tkind == "struct" else "class")
        ctx.node(f"go:type:{pkg}.{tname}", nkind, tname, line, "HIGH",
                 {"lang": LANG, "package": pkg, "go_kind": tkind})
        ctx.edge(f"file:{rel}", f"go:type:{pkg}.{tname}", "defines",
                 line, "HIGH", {})
    for m in IMPORT_RE.finditer(text):
        target = m.group(1) or m.group(3) or ""
        line = _line_of(text, m)
        if target and not target.startswith("(") and "/" in target:
            ctx.edge(f"file:{rel}", f"unresolved:module:{target}",
                     "imports", line, "MEDIUM", {"lang": LANG})
    for m in HTTP_ROUTE_RE.finditer(text):
        route = m.group(1)
        line = text[:m.start()].count("\n") + 1
        if not route.startswith("/"):
            continue
        eid = f"endpoint:* {route}"
        ctx.node(eid, "endpoint", f"* {route}", line, "MEDIUM",
                 {"lang": LANG, "via": "net-http-mux"})
        ctx.edge(f"file:{rel}", eid, "exposes", line, "MEDIUM",
                 {"lang": LANG})
    for m in GRPC_RE.finditer(text):
        svc, impl = m.group(1), m.group(2)
        line = text[:m.start()].count("\n") + 1
        eid = f"endpoint:gRPC {svc}"
        ctx.node(eid, "endpoint", f"gRPC {svc}", line, "HIGH",
                 {"lang": LANG, "via": "grpc", "impl": impl})
        ctx.edge(f"file:{rel}", eid, "exposes", line, "HIGH",
                 {"lang": LANG})
    if KAFKA_RE.search(text):
        for i, line in enumerate(text.splitlines(), start=1):
            if "NewReader" in line or "Consumer" in line:
                ctx.edge(f"file:{rel}", "queue:kafka", "consumes", i,
                         "LOW", {"lang": LANG, "via": "kafka"})
            elif "NewWriter" in line or "Producer" in line:
                ctx.edge(f"file:{rel}", "queue:kafka", "publishes", i,
                         "LOW", {"lang": LANG, "via": "kafka"})
    if SCHED_RE.search(text):
        for m in SCHED_RE.finditer(text):
            line = text[:m.start()].count("\n") + 1
            ctx.edge(f"file:{rel}", "schedule:cron", "triggered-by",
                     line, "LOW", {"lang": LANG})
    for tm in SQL_TABLE_RE.finditer(text):
        tbl = tm.group(1).lower()
        if tbl in ("select", "where", "set", "values", "order", "group"):
            continue
        line = text[:tm.start()].count("\n") + 1
        # Verb classification (mirrors python/java): the DML verb decides
        # reads vs writes. The statement's verb is the LAST verb on the
        # match's line (searching from the file start would pin the first
        # verb of an unrelated earlier statement).
        line_start = text.rfind("\n", 0, tm.start()) + 1
        vm = None
        for cand in SQL_VERB_RE.finditer(text, line_start, tm.start()):
            vm = cand
        etype = "reads" if (vm is None
                            or vm.group(1).upper() == "SELECT") \
            else "writes"
        ctx.edge(f"file:{rel}", f"table:{tbl}", etype, line, "LOW",
                 {"via": "sql-string", "lang": LANG})
