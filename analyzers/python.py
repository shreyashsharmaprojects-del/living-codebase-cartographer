"""Python analyzer (FastAPI/Flask/Django detected as metadata).

Generic output: function/method, class/interface, endpoint (REST decorators),
handler (message/CLI), job (scheduled), table (ORM), configuration.

Primary pass parses with stdlib ``ast`` (deterministic: imports, class/def
structure, call sites, SQL/ORM usage come from node visits, so the
import-newline bug class cannot occur). Files that do not parse (py2
sources, fragments) fall back to the legacy line/regex scan and record a
scan error via ``ctx.scan_error`` — a single file never raises.
"""

import ast
import re

NAME = "python"
KIND = "language"
EXTENSIONS = {".py"}
FILENAMES = set()
PATH_HINTS = ()
PRIORITY = 10

LANG = "python"

# ---------------------------------------------------------------------------
# Shared value analysis (operates on AST string-constant values, never on
# raw source lines, so newlines inside literals are content, not structure)
# ---------------------------------------------------------------------------
SQL_VERB_RE = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
SQL_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+([A-Za-z_][\w]*)",
    re.IGNORECASE)
SQL_SKIP_WORDS = frozenset({
    "select", "where", "set", "values", "order", "group", "by", "and",
    "or", "on", "as", "limit", "having", "offset"})

# DB-call surface shared by both passes.
SQL_DOTTED_SINKS = frozenset({"execute", "executemany", "raw", "query"})
SQL_BARE_SINKS = frozenset({"text", "createQuery", "create_query"})

# ---------------------------------------------------------------------------
# Line-local textual patterns (kept as regex in BOTH passes: single-line,
# no newline-spanning character class — safe by audit)
# ---------------------------------------------------------------------------
HTTP_CALL_RE = re.compile(
    r"""\b(?:requests|httpx|aiohttp|urllib)\s*\.\s*(get|post|put|delete|patch)\s*\(\s*["']([^"']+)["']""")
MONGO_RE = re.compile(r"""\.\s*(?:get_collection|Collection)\s*\(\s*["']([\w-]+)["']""")
REDIS_RE = re.compile(
    r"""\bredis\w*\s*\.\s*(get|set|hget|hset|lpush|rpush|publish|expire)\s*\(""")

# ---------------------------------------------------------------------------
# Legacy fallback regexes (SyntaxError path only)
# ---------------------------------------------------------------------------
CLASS_RE = re.compile(r"^\s*class\s+(\w+)\s*(?:\(([^)]*)\))?\s*:", re.M)
FUNC_RE = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(", re.M)
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
SESSION_QUERY_RE = re.compile(r"\.\s*query\s*\(\s*([A-Za-z_][\w]*)\s*\)")
MODEL_QUERY_RE = re.compile(r"\b([A-Za-z_][\w]*)\.query\s*\.")
DJANGO_MGR_RE = re.compile(r"\b([A-Za-z_][\w]*)\.objects\s*\.")

# AST-pass decorator vocabularies (mirror the legacy decorator regexes).
_DECO_SRC = ""
_FASTAPI_OBJS = frozenset({"app", "router", "api"})
_FASTAPI_METHODS = frozenset(
    {"get", "post", "put", "delete", "patch", "head", "options"})
_FLASK_OBJS = frozenset({"app", "bp", "blueprint"})
_KNOWN_HTTP = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH"})


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


def _model_dst(model, class_names):
    if model in class_names:
        return f"py:class:{model}"
    return f"unresolved:class:{model}"


def _table_dst(ctx, name, file_tables):
    """Literal-SQL table target.

    ``table:<name>`` whenever the name matches the shared lowercase
    convention (sql.py ``_tail_name`` lowercases; the python SQL value
    analysis lowercases here) — the generic resolve/validate layer treats
    a bare ``table:`` id as a placeholder only while no node with that id
    exists (``graph.is_placeholder``), and as resolved once any analyzer
    (e.g. a sql.py migration, scanned before or after this file) owns the
    node. Otherwise ``unresolved:table:<name>``.
    """
    tid = _clean_id(name.lower())
    if not tid:
        return None
    if tid in file_tables:
        return f"table:{tid}"
    # Bare table: ids resolve graph-wide at resolve time; unresolved only
    # when the name itself is unusable as an id (handled above).
    return f"table:{tid}"


# ---------------------------------------------------------------------------
# AST helpers (3.8-compatible: no ast.unparse)
# ---------------------------------------------------------------------------

def _dotted(node):
    """Dotted name for Name/Attribute chains, else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        if base:
            return base + "." + node.attr
    return None


def _is_str(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _const_strs(node):
    """String-constant fragments of an expression (f-strings contribute
    their literal parts, matching the legacy literal join)."""
    if _is_str(node):
        return [node.value]
    if isinstance(node, ast.Constant) and isinstance(node.value, bytes):
        try:
            return [node.value.decode("ascii")]
        except Exception:
            return []
    if isinstance(node, ast.JoinedStr):
        parts = [v.value for v in node.values if _is_str(v)]
        return ["".join(parts)] if parts else []
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _const_strs(node.left) + _const_strs(node.right)
    return []


def _sql_literal_edges(ctx, src, frags, line, file_tables):
    """DML verbs in sink string fragments -> reads/writes (MEDIUM) or a
    LOW dynamic ``queries`` edge when the verb is seen but no table is
    recoverable. No verb -> no edge (same rule as the legacy pass)."""
    text = " ".join(f for f in frags if f)
    vm = SQL_VERB_RE.search(text)
    if not vm:
        return
    verb = vm.group(1).upper()
    etype = "reads" if verb == "SELECT" else "writes"
    seen, tables = set(), []
    for t in SQL_TABLE_RE.findall(text):
        if t.lower() in SQL_SKIP_WORDS or t.lower() in seen:
            continue
        seen.add(t.lower())
        tables.append(t)
    if tables:
        for t in tables:
            dst = _table_dst(ctx, t, file_tables)
            if not dst:
                continue
            ctx.edge(src, dst, etype, line, "MEDIUM",
                     {"via": "sql-literal", "lang": LANG})
    else:
        ctx.edge(src, "unresolved:query:dynamic", "queries", line,
                 "LOW", {"via": "sql-dynamic", "lang": LANG})


class _FirstPass(ast.NodeVisitor):
    """Order-preserving pre-scan: class names, ORM tables, def name -> id.

    Method ids mirror the typescript analyzer shape:
    ``py:class:<Class>#<method>``.
    """

    def __init__(self):
        self.stack = []  # enclosing class names
        self.class_names = set()
        self.file_tables = set()
        self.name_to_id = {}

    def visit_ClassDef(self, node):
        self.class_names.add(node.name)
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Assign)
                    and any(isinstance(t, ast.Name)
                            and t.id == "__tablename__"
                            for t in sub.targets)
                    and _is_str(sub.value)):
                self.file_tables.add(sub.value.value.lower())
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _visit_def(self, node):
        if self.stack:
            nid = f"py:class:{self.stack[-1]}#{node.name}"
        else:
            nid = f"py:function:{node.name}"
        self.name_to_id.setdefault(node.name, nid)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self._visit_def(node)

    def visit_AsyncFunctionDef(self, node):
        self._visit_def(node)


def _route_decorator(dec):
    """(framework, METHOD, route, line) for FastAPI/Flask route decorators."""
    if not isinstance(dec, ast.Call):
        return None
    func = dec.func
    if not (isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)):
        return None
    recv, attr = func.value.id, func.attr
    if not (dec.args and _is_str(dec.args[0])):
        return None
    route = "/" + dec.args[0].value.strip("/")
    if recv in _FASTAPI_OBJS and attr.lower() in _FASTAPI_METHODS:
        return ("fastapi", attr.upper(), route, dec.lineno)
    if recv in _FLASK_OBJS and attr == "route":
        methods = ["GET"]
        for kw in dec.keywords:
            if (kw.arg == "methods"
                    and isinstance(kw.value, (ast.List, ast.Tuple))):
                found = [e.value.upper() for e in kw.value.elts
                         if _is_str(e) and e.value.upper() in _KNOWN_HTTP]
                if found:
                    methods = found
        return ("flask", "/".join(methods), route, dec.lineno)
    return None


def _deco_tag(dec):
    """Decorator source text (fallback: dotted name) for job/click regexes."""
    try:
        seg = ast.get_source_segment(_DECO_SRC, dec)
    except Exception:
        seg = None
    if seg:
        return "@" + seg.strip()
    dotted = _dotted(dec.func if isinstance(dec, ast.Call) else dec)
    return "@" + (dotted or "")


def _emit_import(ctx, rel, fw, node):
    if isinstance(node, ast.Import):
        targets = [a.name for a in node.names]
    elif isinstance(node, ast.ImportFrom):
        if node.module:
            # ast separates the relative level, so `from ..pkg import m`
            # records `pkg` (the legacy regex kept the dots: `..pkg`).
            targets = [node.module]
        else:
            # `from . import x` — the module is the imported name.
            targets = [a.name for a in node.names]
    else:
        return
    for target in targets:
        target = _clean_id(target)
        if not target:
            continue
        if target.split(".")[0] in IMPORT_SKIP:
            continue
        ctx.edge(f"file:{rel}", f"unresolved:module:{target}",
                 "imports", node.lineno, "MEDIUM",
                 {"lang": LANG, "framework": fw})


def _emit_call(ctx, rel, node, enclosing, class_names, file_tables,
               name_to_id):
    src = enclosing or f"file:{rel}"
    line = node.lineno
    func = node.func
    # Django path(route, handler): endpoint node (MEDIUM) + handled-by (LOW).
    is_path = (isinstance(func, ast.Name) and func.id == "path") or (
        isinstance(func, ast.Attribute) and func.attr == "path")
    if (is_path and len(node.args) >= 2 and _is_str(node.args[0])):
        handler = _dotted(node.args[1])
        if handler:
            short = handler.split(".")[-1]
            route = "/" + node.args[0].value.strip("/")
            eid = f"endpoint:GET {route}"
            ctx.node(eid, "endpoint", f"GET {route}", line, "MEDIUM",
                     {"lang": LANG, "framework": "django",
                      "handler": short})
            ctx.edge(eid, f"unresolved:handler:{short}", "handled-by",
                     line, "LOW", {"lang": LANG})
    # ORM session.query(Model): queries edge (MEDIUM).
    if (isinstance(func, ast.Attribute) and func.attr == "query"
            and node.args and isinstance(node.args[0], ast.Name)):
        model = _clean_id(node.args[0].id)
        if model:
            ctx.edge(src, _model_dst(model, class_names), "queries", line,
                     "MEDIUM", {"via": "orm-query", "lang": LANG})
    # SQL sinks over string-literal args (nested calls visited separately).
    dotted_sink = (isinstance(func, ast.Attribute)
                   and func.attr in SQL_DOTTED_SINKS)
    bare_sink = (isinstance(func, ast.Name)
                 and func.id in SQL_BARE_SINKS)
    if dotted_sink or bare_sink:
        frags = []
        for a in list(node.args) + [k.value for k in node.keywords]:
            frags.extend(_const_strs(a))
        _sql_literal_edges(ctx, src, frags, line, file_tables)
    # Same-file calls: enclosing function/method -> defined symbol (HIGH).
    if (enclosing and isinstance(func, ast.Name)
            and func.id in name_to_id):
        dst = name_to_id[func.id]
        base = (src.rsplit("#", 1)[-1] if "#" in src
                else src.split(":")[-1])
        if dst != src and func.id != base:
            ctx.edge(src, dst, "calls", line, "HIGH",
                     {"via": "same-file", "lang": LANG})
    # Constructor-call + method-call: ``Service().create(...)`` resolves to
    # the in-file class method (MEDIUM — the receiver type is name-resolved,
    # not declared). Plain ``obj.method(...)`` stays unresolved: only an
    # inline construction names the class deterministically.
    if (enclosing and isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Call)
            and isinstance(func.value.func, ast.Name)):
        cls = func.value.func.id
        if cls in class_names:
            dst = f"py:class:{cls}#{func.attr}"
            if dst != src:
                ctx.edge(src, dst, "calls", line, "MEDIUM",
                         {"via": "same-file-ctor", "lang": LANG})


def _emit_attr(ctx, rel, node, parent, enclosing, class_names):
    """Chained ``Model.query...`` / ``Model.objects...`` ORM usage.

    Only fires inside a further attribute chain (``User.query.filter``),
    mirroring the legacy ``\\.query\\.`` / ``\\.objects\\.`` patterns; a
    bare ``session.query`` that is itself the call func is handled by the
    Call visit instead.
    """
    if not isinstance(parent, ast.Attribute):
        return
    if not isinstance(node.value, ast.Name):
        return
    model = _clean_id(node.value.id)
    if not model:
        return
    src = enclosing or f"file:{rel}"
    if node.attr == "query":
        ctx.edge(src, _model_dst(model, class_names), "queries",
                 node.lineno, "MEDIUM",
                 {"via": "orm-query", "lang": LANG})
    elif node.attr == "objects":
        ctx.edge(src, _model_dst(model, class_names), "reads",
                 node.lineno, "MEDIUM",
                 {"via": "orm-manager", "lang": LANG})


def _emit_def(ctx, rel, fw, node, parent, enclosing, stack, class_names,
              file_tables, name_to_id):
    fname = node.name
    line = node.lineno
    if stack:
        sym = f"{stack[-1][1]}#{fname}"
        skind = "method"
    else:
        sym = f"py:function:{fname}"
        skind = "function"
    route, job_line = None, None
    for dec in node.decorator_list:
        r = _route_decorator(dec)
        if r:
            route = r  # last route decorator wins (legacy overwrite)
        tag = _deco_tag(dec)
        if CELERY_RE.search(tag) or SCHED_RE.search(tag):
            job_line = getattr(dec, "lineno", line)
        if CLICK_RE.search(tag):
            hid = f"handler:{rel}:{dec.lineno}"
            ctx.node(hid, "handler",
                     f"cli@{rel.split('/')[-1]}:{dec.lineno}", dec.lineno,
                     "MEDIUM", {"lang": LANG, "framework": "click"})
            ctx.edge(f"file:{rel}", hid, "defines", dec.lineno, "HIGH", {})
    if route:
        _fw, method, route_s, mline = route
        eid = f"endpoint:{method} {route_s}"
        ctx.node(sym, skind, fname, line,
                 "HIGH", {"lang": LANG, "framework": fw})
        ctx.edge(f"file:{rel}", sym, "defines", line, "HIGH", {})
        if skind == "method":
            ctx.edge(stack[-1][1], sym, "defines", line, "HIGH", {})
        ctx.node(eid, "endpoint", f"{method} {route_s}", mline,
                 "HIGH", {"lang": LANG, "framework": fw,
                          "handler": fname})
        ctx.edge(eid, sym, "handled-by", mline, "HIGH", {})
    elif job_line is not None:
        ctx.node(sym, "job", fname, line,
                 "HIGH", {"lang": LANG, "framework": fw})
        ctx.edge(f"file:{rel}", sym, "defines", line, "HIGH", {})
        if skind == "method":
            ctx.edge(stack[-1][1], sym, "defines", line, "HIGH", {})
    else:
        ctx.node(sym, skind, fname, line,
                 "HIGH", {"lang": LANG, "framework": fw})
        ctx.edge(f"file:{rel}", sym, "defines", line, "HIGH", {})
        if skind == "method":
            ctx.edge(stack[-1][1], sym, "defines", line, "HIGH", {})
    for stmt in node.body:
        _emit_node(ctx, rel, fw, stmt, node, sym, stack, class_names,
                   file_tables, name_to_id)


def _emit_class(ctx, rel, fw, node, parent, stack, class_names,
                file_tables, name_to_id):
    cname = node.name
    line = node.lineno
    cid = f"py:class:{cname}"
    bases = []
    for b in node.bases:
        d = _dotted(b)
        if d:
            bases.append(d)
    for kw in node.keywords:
        d = _dotted(kw.value)
        bases.append(f"{kw.arg}={d}" if d else (kw.arg or ""))
    nkind = "class"
    if "Test" in cname or "test" in rel:
        nkind = "test"
    ctx.node(cid, nkind, cname, line, "HIGH",
             {"lang": LANG, "framework": fw, "bases": bases})
    ctx.edge(f"file:{rel}", cid, "defines", line, "HIGH", {})
    best = None  # earliest __tablename__ assignment in the class subtree
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Assign)
                and any(isinstance(t, ast.Name)
                        and t.id == "__tablename__" for t in sub.targets)
                and _is_str(sub.value)):
            if best is None or sub.lineno < best[0]:
                best = (sub.lineno, sub.value.value.lower())
    if best:
        tn = best[1]
        tid = f"table:{tn}"
        ctx.node(tid, "table", tn, line, "HIGH",
                 {"lang": LANG, "framework": fw,
                  "via": "orm-model", "model": cname})
        ctx.edge(f"file:{rel}", tid, "defines", line, "HIGH", {})
        ctx.edge(cid, tid, "writes", line, "MEDIUM",
                 {"via": "orm", "lang": LANG})
    new_stack = stack + [(cname, cid)]
    for stmt in node.body:
        _emit_node(ctx, rel, fw, stmt, node, None, new_stack, class_names,
                   file_tables, name_to_id)


def _emit_node(ctx, rel, fw, node, parent, enclosing, stack, class_names,
               file_tables, name_to_id):
    if isinstance(node, ast.ClassDef):
        _emit_class(ctx, rel, fw, node, parent, stack, class_names,
                    file_tables, name_to_id)
        return
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        _emit_def(ctx, rel, fw, node, parent, enclosing, stack,
                  class_names, file_tables, name_to_id)
        return
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        _emit_import(ctx, rel, fw, node)
        return
    if isinstance(node, ast.Call):
        _emit_call(ctx, rel, node, enclosing, class_names, file_tables,
                   name_to_id)
    elif isinstance(node, ast.Attribute):
        _emit_attr(ctx, rel, node, parent, enclosing, class_names)
    for child in ast.iter_child_nodes(node):
        _emit_node(ctx, rel, fw, child, node, enclosing, stack,
                   class_names, file_tables, name_to_id)


def _ast_scan(ctx, rel, text, tree):
    global _DECO_SRC
    _DECO_SRC = text
    fw = _framework_of(text)
    ctx.node(f"file:{rel}", "file", rel.split("/")[-1], 1, "HIGH",
             {"lang": LANG, "framework": fw,
              "role": "test" if ("test" in rel or "conftest" in rel)
              else "source"})
    fp = _FirstPass()
    fp.visit(tree)
    for stmt in tree.body:
        _emit_node(ctx, rel, fw, stmt, tree, None, [], fp.class_names,
                   fp.file_tables, fp.name_to_id)
    # Line-local textual surface with no AST equivalent (single-line,
    # newline-safe patterns only).
    for i, line in enumerate(text.splitlines(), start=1):
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
        if REDIS_RE.search(line):
            ctx.edge(f"file:{rel}", "cache:redis", "writes", i, "LOW",
                     {"via": "redis-client", "lang": LANG})


# ---------------------------------------------------------------------------
# Legacy regex fallback (unparseable files: py2 sources, fragments)
# ---------------------------------------------------------------------------

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


def _emit_sql_edges(ctx, src, line, i, class_names, file_tables):
    """DML verbs in sink string literals + ORM model usage.

    Literal tables target bare ``table:<name>`` ids (lowercased, matching
    sql.py ``_tail_name``): the generic layer treats them as placeholders
    only while no node with that id exists, and as resolved once any
    analyzer owns the node. Dynamic SQL (verb but no literal table)
    becomes a LOW ``queries`` edge.
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
                    dst = _table_dst(ctx, t, file_tables)
                    if not dst:
                        continue
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


def _legacy_scan(ctx, path, text):
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


def scan(ctx, path, text):
    """Primary AST pass; legacy regex fallback for unparseable files."""
    try:
        tree = ast.parse(text)
    except Exception as exc:
        ctx.scan_error(exc)
        _legacy_scan(ctx, path, text)
        return
    try:
        _ast_scan(ctx, ctx.path, text, tree)
    except Exception as exc:
        # The map must survive any single file: keep AST evidence already
        # emitted and let the legacy pass add its line-local surface.
        ctx.scan_error(exc)
        _legacy_scan(ctx, path, text)
