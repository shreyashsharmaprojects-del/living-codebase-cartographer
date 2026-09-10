"""Python analyzer (FastAPI/Flask/Django detected as metadata).

Generic output: function/method, class/interface, endpoint (REST decorators),
handler (message/CLI), job (scheduled), table (ORM), configuration.
"""

import re

NAME = "python"
KIND = "language"
EXTENSIONS = {".py"}
FILENAMES = set()
PATH_HINTS = ()
PRIORITY = 10

LANG = "python"

CLASS_RE= re.compile(r"^\s*class\s+(\w+)\s*(?:\(([^)]*)\))?\s*:", re.M)
FUNC_RE= re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(", re.M)
FASTAPI_DEC = re.compile(
    r"@(?:app|router|api)\.(get|post|put|delete|patch|head|options)"
    r"""\s*\(\s*["']([^"']+)["']""")
FLASK_DEC = re.compile(
    r"@(?:app|bp|blueprint)\.route\s*\(\s*[\"']([^\"']+)[\"']"
    r"(?:[^)]*methods\s*=\s*\[([^\]]*)\])?")
DJANGO_PATH = re.compile(
    r"""\bpath\s*\(\s*["']([^"']+)["']\s*,\s*([\w.]+)""")
TABLE_RE = re.compile(r"__tablename__\s*=\s*[\"']([\w]+)[\"']")
CELERY_RE = re.compile(r"@(er\s*\.\s*)?task\b|@shared_task|@celery_app\.task")
SCHED_RE = re.compile(r"@.*(?:periodic_task|crontab|schedule|every)\b")
CLICK_RE = re.compile(r"@click\.(command|group)")
# Single-line module lists only: newlines are excluded so one import can
# never swallow following statements into an "unresolved:module:..." id.
IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.,\t ]+))",
                       re.M)
IMPORT_SKIP = frozenset({
    "os", "sys", "re", "json", "datetime", "typing", "pathlib"})
HTTP_CALL_RE = re.compile(
    r"""\b(?:requests|httpx|aiohttp|urllib)\s*\.\s*(get|post|put|delete|patch)\s*\(\s*["']([^"']+)["']""")
MONGO_RE = re.compile(r"""\.\s*(?:get_collection|Collection)\s*\(\s*["']([\w-]+)["']""")
REDIS_RE = re.compile(
    r"""\bredis\w*\s*\.\s*(get|set|hget|hset|lpush|rpush|publish|expire)\s*\(""")
# Same-file call detection (mirrors java's same-class HIGH pattern):
# pass 1 collects defined function/method names; pass 2 links call sites.
CALL_RE = re.compile(r"\b([a-zA-Z_][\w]*)\s*\(")
CALL_KEYWORDS = frozenset({
    "if", "for", "while", "return", "import", "from", "def", "class",
    "with", "as", "elif", "else", "try", "except", "finally", "raise",
    "assert", "lambda", "yield", "pass", "break", "continue", "del",
    "global", "nonlocal", "print", "len", "range", "str", "int", "list",
    "dict", "set", "super", "isinstance", "getattr", "setattr",
})
# SQL data-access surface: string literals inside DB sinks (DML verbs) plus
# ORM usage (SQLAlchemy session/query, Django managers).
SQL_SINK_RE = re.compile(
    r"\.\s*(execute|executemany|raw|query)\s*\(|"
    r"\b(text|createQuery|create_query)\s*\(")
STR_LIT_RE = re.compile(
    r"""[fFrRbBuU]{0,2}('((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)")""")
SQL_VERB_RE = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
SQL_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+([A-Za-z_][\w]*)",
    re.IGNORECASE)
SQL_SKIP_WORDS = frozenset({
    "select", "where", "set", "values", "order", "group", "by", "and",
    "or", "on", "as", "limit", "having", "offset"})
SESSION_QUERY_RE = re.compile(r"\.\s*query\s*\(\s*([A-Za-z_][\w]*)\s*\)")
MODEL_QUERY_RE = re.compile(r"\b([A-Za-z_][\w]*)\.query\s*\.")
DJANGO_MGR_RE = re.compile(r"\b([A-Za-z_][\w]*)\.objects\s*\.")


def can_handle(path, text=None):
    return path.endswith(".py")


def _framework_of(text):
    if "fastapi" in text or "FastAPI" in text:
        return "fastapi"
    if "flask" in text or "Flask" in text:
        return "flask"
    if "django" in text or "DJANGO" in text:
        return "django"
    if "celery" in text or "Celery" in text:
        return "celery"
    if "click" in text:
        return "click"
    return None


def _indent_of(line):
    return len(line) - len(line.lstrip())


def _clean_id(s, limit=512):
    """Guard shared-convention id hygiene: single-line, no control chars."""
    s = (s or "").strip()
    if not s or len(s) > limit or re.search(r"[\x00-\x1f]", s):
        return None
    return s


def _pre_scan(lines):
    """Indentation-aware pre-scan.

    Returns (class_names, file_tables, name_to_id) where name_to_id maps
    every defined function/method name to its node id. Method ids mirror
    the typescript analyzer shape: <class-node-id>#<method>, i.e.
    ``py:class:<Class>#<method>``.
    """
    cls_stack = []  # (indent, class-name)
    class_names = set()
    file_tables = set()
    name_to_id = {}
    for idx, line in enumerate(lines):
        cm = CLASS_RE.match(line)
        if cm:
            ind = _indent_of(line)
            while cls_stack and cls_stack[-1][0] >= ind:
                cls_stack.pop()
            cname = cm.group(1)
            class_names.add(cname)
            cls_stack.append((ind, cname))
            tm = TABLE_RE.search("\n".join(lines[idx + 1:idx + 6]))
            if tm:
                file_tables.add(tm.group(1).lower())
            continue
        fm = FUNC_RE.match(line)
        if fm:
            ind = _indent_of(line)
            while cls_stack and cls_stack[-1][0] >= ind:
                cls_stack.pop()
            fname = fm.group(1)
            if cls_stack:
                nid = f"py:class:{cls_stack[-1][1]}#{fname}"
            else:
                nid = f"py:function:{fname}"
            name_to_id.setdefault(fname, nid)
    return class_names, file_tables, name_to_id


def _emit_py_calls(ctx, src, body, i, name_to_id):
    """Same-file calls edges for call sites found in `body` (HIGH)."""
    src_name = (src.rsplit("#", 1)[-1] if "#" in src
                else src.split(":")[-1])
    for cm in CALL_RE.finditer(body):
        target = cm.group(1)
        if target in CALL_KEYWORDS or target == src_name:
            continue
        dst = name_to_id.get(target)
        if dst and dst != src:
            ctx.edge(src, dst, "calls", i, "HIGH",
                     {"via": "same-file", "lang": LANG})


def _model_dst(model, class_names):
    if model in class_names:
        return f"py:class:{model}"
    return f"unresolved:class:{model}"


def _emit_sql_edges(ctx, src, line, i, class_names, file_tables):
    """DML verbs in sink string literals + ORM model usage.

    Literal tables resolve against in-file ``__tablename__`` tables
    (``table:<name>``) and fall back to ``unresolved:table:<name>`` —
    a bare ``table:`` id is never invented. Dynamic SQL (verb but no
    literal table) becomes a LOW ``queries`` edge.
    """
    if SQL_SINK_RE.search(line):
        contents = []
        for sm in STR_LIT_RE.finditer(line):
            c = sm.group(2) if sm.group(2) is not None else sm.group(3)
            if c:
                contents.append(c)
        vm = SQL_VERB_RE.search(" ".join(contents))
        if vm:
            verb = vm.group(1).upper()
            etype = "reads" if verb == "SELECT" else "writes"
            seen, tables = set(), []
            for t in SQL_TABLE_RE.findall(" ".join(contents)):
                if t.lower() in SQL_SKIP_WORDS or t.lower() in seen:
                    continue
                seen.add(t.lower())
                tables.append(t)
            if tables:
                for t in tables:
                    tid = _clean_id(t.lower())
                    if not tid:
                        continue
                    dst = (f"table:{tid}" if tid in file_tables
                           else f"unresolved:table:{tid}")
                    ctx.edge(src, dst, etype, i, "MEDIUM",
                             {"via": "sql-literal", "lang": LANG})
            else:
                ctx.edge(src, "unresolved:query:dynamic", "queries", i,
                         "LOW", {"via": "sql-dynamic", "lang": LANG})
    for qm in SESSION_QUERY_RE.finditer(line):
        model = _clean_id(qm.group(1))
        if not model:
            continue
        ctx.edge(src, _model_dst(model, class_names), "queries", i,
                 "MEDIUM", {"via": "orm-query", "lang": LANG})
    for qm in MODEL_QUERY_RE.finditer(line):
        model = _clean_id(qm.group(1))
        if not model:
            continue
        ctx.edge(src, _model_dst(model, class_names), "queries", i,
                 "MEDIUM", {"via": "orm-query", "lang": LANG})
    for dm in DJANGO_MGR_RE.finditer(line):
        model = _clean_id(dm.group(1))
        if not model:
            continue
        ctx.edge(src, _model_dst(model, class_names), "reads", i,
                 "MEDIUM", {"via": "orm-manager", "lang": LANG})


def scan(ctx, path, text):
    rel = ctx.path
    fw = _framework_of(text)
    ctx.node(f"file:{rel}", "file", rel.split("/")[-1], 1, "HIGH",
             {"lang": LANG, "framework": fw,
              "role": "test" if ("test" in rel or "conftest" in rel) else "source"})
    lines = text.splitlines()
    # pass 1: indent-aware collection of classes, ORM tables, def name->id
    class_names, file_tables, name_to_id = _pre_scan(lines)
    defined = set(name_to_id)
    for m in IMPORT_RE.finditer(text):
        line = text[:m.start()].count("\n") + 1
        if m.group(1):
            mod = m.group(1)
            if re.fullmatch(r"\.+", mod):
                # `from . import x` — the module is the imported name.
                after = text[m.end():].split("\n", 1)[0]
                targets = [n.strip() for n in after.split(",")
                           if n.strip()]
            else:
                targets = [mod]
        else:
            targets = []
            for part in (m.group(2) or "").split(","):
                # `import x as y` records the real module x.
                part = re.split(r"\s+as\s+", part.strip(),
                                maxsplit=1)[0].strip()
                if part:
                    targets.append(part)
        for target in targets:
            target = _clean_id(target)
            if not target:
                continue
            if target.split(".")[0] in IMPORT_SKIP:
                continue
            ctx.edge(f"file:{rel}", f"unresolved:module:{target}",
                     "imports", line, "MEDIUM",
                     {"lang": LANG, "framework": fw})
    pending_endpoint = None
    pending_job = None
    cls_stack = []  # (indent, class-name, class-node-id)
    current_sym = None  # node id of enclosing function/method, if any
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        fm = FASTAPI_DEC.search(stripped)
        if fm:
            route = "/" + fm.group(2).strip("/")
            pending_endpoint = (fm.group(1).upper(), route, i)
            continue
        fm = FLASK_DEC.search(stripped)
        if fm:
            methods = re.findall(r"['\"](GET|POST|PUT|DELETE|PATCH)['\"]",
                                 fm.group(2) or "", re.I) or ["GET"]
            route = "/" + fm.group(1).strip("/")
            pending_endpoint = ("/".join(m.upper() for m in methods),
                                route, i)
            continue
        for dm in DJANGO_PATH.finditer(line):
            route = "/" + dm.group(1).strip("/")
            handler = dm.group(2).split(".")[-1]
            eid = f"endpoint:GET {route}"
            ctx.node(eid, "endpoint", f"GET {route}", i, "MEDIUM",
                     {"lang": LANG, "framework": "django",
                      "handler": handler})
            ctx.edge(eid, f"unresolved:handler:{handler}", "handled-by",
                     i, "LOW", {"lang": LANG})
        if CELERY_RE.search(stripped) or SCHED_RE.search(stripped):
            pending_job = i
            continue
        if CLICK_RE.search(stripped):
            hid = f"handler:{rel}:{i}"
            ctx.node(hid, "handler",
                     f"cli@{rel.split('/')[-1]}:{i}", i, "MEDIUM",
                     {"lang": LANG, "framework": "click"})
            ctx.edge(f"file:{rel}", hid, "defines", i, "HIGH", {})
            continue
        cm = CLASS_RE.match(line)
        if cm:
            ind = _indent_of(line)
            while cls_stack and cls_stack[-1][0] >= ind:
                cls_stack.pop()
            cname, bases = cm.group(1), cm.group(2) or ""
            cid = f"py:class:{cname}"
            nkind = "class"
            if "Test" in cname or "test" in rel:
                nkind = "test"
            elif "Base" in bases and "Model" in bases:
                nkind = "class"
            ctx.node(cid, nkind, cname, i, "HIGH",
                     {"lang": LANG, "framework": fw,
                      "bases": [b.strip() for b in bases.split(",") if b.strip()]})
            ctx.edge(f"file:{rel}", cid, "defines", i,
                     "HIGH", {})
            tm = TABLE_RE.search("\n".join(lines[i:i + 5]))
            if tm:
                tn = tm.group(1).lower()
                tid = f"table:{tn}"
                ctx.node(tid, "table", tn, i, "HIGH",
                         {"lang": LANG, "framework": fw,
                          "via": "orm-model", "model": cname})
                ctx.edge(f"file:{rel}", tid, "defines", i, "HIGH", {})
                ctx.edge(cid, tid, "writes", i, "MEDIUM",
                         {"via": "orm", "lang": LANG})
            cls_stack.append((ind, cname, cid))
            current_sym = None
            continue
        fm = FUNC_RE.match(line)
        if fm:
            fname = fm.group(1)
            ind = _indent_of(line)
            while cls_stack and cls_stack[-1][0] >= ind:
                cls_stack.pop()
            if cls_stack:
                sym = f"{cls_stack[-1][2]}#{fname}"
                skind = "method"
            else:
                sym = f"py:function:{fname}"
                skind = "function"
            current_sym = sym
            if pending_endpoint:
                method, route, mline = pending_endpoint
                eid = f"endpoint:{method} {route}"
                ctx.node(sym, skind, fname, i,
                         "HIGH", {"lang": LANG, "framework": fw})
                ctx.edge(f"file:{rel}", sym, "defines", i, "HIGH", {})
                if skind == "method":
                    ctx.edge(cls_stack[-1][2], sym, "defines", i,
                             "HIGH", {})
                ctx.node(eid, "endpoint", f"{method} {route}", mline,
                         "HIGH", {"lang": LANG, "framework": fw,
                                  "handler": fname})
                ctx.edge(eid, sym, "handled-by",
                         mline, "HIGH", {})
                pending_endpoint = None
            elif pending_job:
                ctx.node(sym, "job", fname, i,
                         "HIGH", {"lang": LANG, "framework": fw})
                ctx.edge(f"file:{rel}", sym, "defines", i, "HIGH", {})
                if skind == "method":
                    ctx.edge(cls_stack[-1][2], sym, "defines", i,
                             "HIGH", {})
                pending_job = None
            else:
                ctx.node(sym, skind, fname, i,
                         "HIGH", {"lang": LANG, "framework": fw})
                ctx.edge(f"file:{rel}", sym, "defines",
                         i, "HIGH", {})
                if skind == "method":
                    ctx.edge(cls_stack[-1][2], sym, "defines", i,
                             "HIGH", {})
            # calls on the definition line itself (one-liner bodies)
            if ":" in line:
                _emit_py_calls(ctx, sym, line.split(":", 1)[1], i,
                               name_to_id)
            continue
        for hm in HTTP_CALL_RE.finditer(line):
            url = hm.group(2)
            if url.startswith("/"):
                eid = f"endpoint:{hm.group(1).upper()} {url}"
                ctx.node(eid, "endpoint",
                         f"{hm.group(1).upper()} {url}", i, "LOW",
                         {"lang": LANG, "observed": "backend-consumer"})
                ctx.edge(f"file:{rel}", eid, "consumes", i, "MEDIUM",
                         {"http": hm.group(1).upper(), "lang": LANG})
        for mm in MONGO_RE.finditer(line):
            ctx.edge(f"file:{rel}", f"collection:{mm.group(1)}",
                     "reads", i, "MEDIUM",
                     {"via": "pymongo", "lang": LANG})
        # same-file calls: enclosing function/method -> defined symbol (HIGH)
        # (decorator/@app lines and def lines themselves are skipped)
        if current_sym and not stripped.startswith(("@", "def ", "class ")):
            _emit_py_calls(ctx, current_sym, line, i, name_to_id)
        if (SQL_SINK_RE.search(line) or SESSION_QUERY_RE.search(line)
                or MODEL_QUERY_RE.search(line)
                or DJANGO_MGR_RE.search(line)):
            _emit_sql_edges(ctx, current_sym or f"file:{rel}", line, i,
                            class_names, file_tables)
        if REDIS_RE.search(line):
            ctx.edge(f"file:{rel}", "cache:redis", "writes", i, "LOW",
                     {"via": "redis-client", "lang": LANG})
