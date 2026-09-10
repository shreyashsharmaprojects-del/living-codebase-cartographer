"""Rust analyzer: modules, structs/enums/traits, fns, web-framework routes.

Generic kinds only; actix/axum/rocket/warp specifics live in meta.
"""

import re

NAME = "rust"
KIND = "language"
EXTENSIONS = {".rs"}
FILENAMES = set()
PATH_HINTS = ()
PRIORITY = 10

LANG = "rust"

MOD_RE = re.compile(r"^\s*(?:pub\s+)?mod\s+(\w+)", re.M)
STRUCT_RE = re.compile(
    r"^\s*(?:pub\s+)?(?:struct|enum|trait|type)\s+(\w+)", re.M)
FN_RE = re.compile(
    r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*(?:<[^>]*>)?\s*\(", re.M)
IMPL_RE = re.compile(r"^\s*impl\s+(?:<[^>]*>\s+)?(\w+)", re.M)
ROUTE_ATTR_RE = re.compile(
    r"#\[(get|post|put|delete|patch)\s*\(\s*[\"']([^\"']+)[\"']")
ROUTER_RE = re.compile(
    r"""\.(?:route|service|nest)\s*\(\s*["']([^"']+)["']""")
HANDLER_RE = re.compile(
    r"""\.(?:get|post|put|delete|patch)\s*\(\s*([\w:]+)""")
# Single-line use lists only: newlines are excluded so one use can never
# swallow following statements into an "unresolved:module:..." id (mirrors
# python IMPORT_RE single-line fix; Wave 4 Part 3 item 4 class).
USE_RE = re.compile(r"^\s*use\s+([\w:{},* \t]+);", re.M)
# Same-file call detection (mirrors java's same-class HIGH pattern).
CALL_RE = re.compile(r"\b([A-Za-z_][\w]*)(?:::\s*([A-Za-z_][\w]*))?\s*\("
                     r"|\b([A-Za-z_][\w]*)\s*\.\s*([A-Za-z_][\w]*)\s*\(")
CALL_KEYWORDS = frozenset({
    "if", "for", "while", "match", "return", "use", "mod", "fn",
    "struct", "enum", "impl", "let", "mut", "ref", "move", "in",
    "else", "loop", "vec", "Some", "None", "Ok", "Err", "panic",
})
SQL_VERB_RE = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
SQL_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+([a-z][a-z0-9_]*)\b", re.IGNORECASE)
SCHED_RE = re.compile(r"tokio::time::interval|cron::|job_scheduler")


def can_handle(path, text=None):
    return path.endswith(".rs")


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


def _fn_id(fname, impl_type):
    if impl_type:
        return f"rs:method:{impl_type}#{fname}"
    return f"rs:fn:{fname}"


def _impl_for_lines(text):
    """Impl-type active on each line (brace-scoped).

    `impl T { ... }` attaches only to lines inside its braces; after the
    closing brace the active type resets to None. Brace counting ignores
    string/char literals and line comments; other skew (e.g. braces in
    block comments) is accepted as heuristic noise.
    """
    out = []
    cur = None
    depth = 0
    impl_depth = 0
    for line in text.splitlines():
        im = IMPL_RE.match(line)
        if im:
            cur = im.group(1)
            impl_depth = depth
        out.append(cur)
        code = re.sub(r'"(?:\\.|[^"\\])*"', '""', line)
        code = re.sub(r"'(?:\\.|[^'\\])*'", "''", code)
        code = code.split("//", 1)[0]
        depth += code.count("{") - code.count("}")
        if cur is not None and depth <= impl_depth:
            cur = None
    return out


def _fn_ids(text, impl_for):
    """Pre-pass: bare fn name -> node id, mirroring the main loop's impl
    tracking so call-site lookups resolve to the same ids the loop emits."""
    mapping = {}
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        fm = FN_RE.match(line)
        if fm:
            impl_type = (impl_for[idx]
                         if impl_for and idx < len(impl_for) else None)
            mapping.setdefault(fm.group(1), _fn_id(fm.group(1), impl_type))
    return mapping


def scan(ctx, path, text):
    rel = ctx.path
    ctx.node(f"file:{rel}", "file", rel.split("/")[-1], 1, "HIGH",
             {"lang": LANG,
              "role": "test" if ("test" in rel or "#[test]" in text[:500]) else "source"})
    for m in USE_RE.finditer(text):
        target = m.group(1).split("{")[0].strip().rstrip(":")
        if target and target not in ("std",):
            ctx.edge(f"file:{rel}", f"unresolved:module:{target}",
                     "imports", _line_of(text, m), "MEDIUM",
                     {"lang": LANG})
    pending_route = None
    impl_for = _impl_for_lines(text)
    defined = {m.group(1) for m in FN_RE.finditer(text)}  # fns in file
    fn_ids = _fn_ids(text, impl_for)  # bare name -> node id (impl-aware)
    current_fn = None  # node-id of enclosing fn, if any
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        # brace-scoped impl owner for this line (None outside an impl block)
        impl_type = (impl_for[i - 1]
                     if i - 1 < len(impl_for) else None)
        am = ROUTE_ATTR_RE.search(stripped)
        if am:
            pending_route = (am.group(1).upper(), am.group(2), i)
            continue
        for rm in ROUTER_RE.finditer(line):
            route = rm.group(1)
            eid = f"endpoint:* {route}"
            ctx.node(eid, "endpoint", f"* {route}", i, "MEDIUM",
                     {"lang": LANG, "via": "router"})
            ctx.edge(f"file:{rel}", eid, "exposes", i, "MEDIUM",
                     {"lang": LANG})
            hm = HANDLER_RE.search(line)
            if hm:
                ctx.edge(eid, f"unresolved:handler:{hm.group(1)}",
                         "handled-by", i, "LOW", {"lang": LANG})
        im = IMPL_RE.match(line)
        if im:
            continue
        sm = STRUCT_RE.match(line)
        if sm:
            sname = sm.group(1)
            nkind = ("interface" if "trait" in line
                     else "enum" if "enum" in line else "class")
            ctx.node(f"rs:type:{sname}", nkind, sname, i, "HIGH",
                     {"lang": LANG})
            ctx.edge(f"file:{rel}", f"rs:type:{sname}", "defines",
                     i, "HIGH", {})
            continue
        fm = FN_RE.match(line)
        if fm:
            fname = fm.group(1)
            nid = _fn_id(fname, impl_type)
            nkind = "method" if impl_type else "function"
            ctx.node(nid, nkind, fname, i, "HIGH",
                     {"lang": LANG,
                      "impl": impl_type} if impl_type else {"lang": LANG})
            ctx.edge(f"file:{rel}", nid, "defines", i, "HIGH", {})
            if impl_type:
                ctx.edge(f"rs:type:{impl_type}", nid, "defines",
                         i, "HIGH", {})
            current_fn = nid
            if pending_route:
                http, route, mline = pending_route
                eid = f"endpoint:{http} {route}"
                ctx.node(eid, "endpoint", f"{http} {route}", mline,
                         "HIGH", {"lang": LANG, "handler": fname})
                ctx.edge(eid, nid, "handled-by", mline, "HIGH", {})
                pending_route = None
            if SCHED_RE.search(line):
                ctx.edge(nid, "schedule:cron", "triggered-by", i,
                         "LOW", {"lang": LANG})
            # calls on the definition line itself (one-liner bodies)
            if "{" in line:
                _self = fname
                for _cm in CALL_RE.finditer(line.split("{", 1)[1]):
                    _t = _cm.group(2) or _cm.group(1) or _cm.group(4)
                    if not _t or _t in CALL_KEYWORDS or _t == _self:
                        continue
                    if _t in defined:
                        _dst = fn_ids.get(_t, _fn_id(_t, None))
                        if _dst != current_fn:
                            ctx.edge(current_fn, _dst, "calls", i, "HIGH",
                                     {"via": "same-file", "lang": LANG})
            continue
        # same-file calls: enclosing fn -> defined fn (HIGH).
        # NOTE: the self check compares the bare name because a method's
        # node id is rs:method:T#m while _t is the bare call name.
        if current_fn and "(" in line and not FN_RE.match(line):
            _self = current_fn.rsplit("#", 1)[-1].rsplit(":", 1)[-1]
            for _cm in CALL_RE.finditer(line):
                _t = _cm.group(2) or _cm.group(1) or _cm.group(4)
                if not _t or _t in CALL_KEYWORDS or _t == _self:
                    continue
                if _t in defined:
                    _dst = fn_ids.get(_t, _fn_id(_t, None))
                    if _dst != current_fn:
                        ctx.edge(current_fn, _dst, "calls", i, "HIGH",
                                 {"via": "same-file", "lang": LANG})
    for tm in SQL_TABLE_RE.finditer(text):
        tbl = tm.group(1).lower()
        if tbl in ("select", "where", "set", "values", "order", "group"):
            continue
        line = text[:tm.start()].count("\n") + 1
        # Verb classification (mirrors python/java/go): the DML verb
        # decides reads vs writes. The statement's verb is the LAST verb
        # on the match's line (searching from the file start would pin the
        # first verb of an unrelated earlier statement).
        line_start = text.rfind("\n", 0, tm.start()) + 1
        vm = None
        for cand in SQL_VERB_RE.finditer(text, line_start, tm.start()):
            vm = cand
        etype = "reads" if (vm is None
                            or vm.group(1).upper() == "SELECT") \
            else "writes"
        ctx.edge(f"file:{rel}", f"table:{tbl}", etype, line, "LOW",
                 {"via": "sql-string", "lang": LANG})
