"""TypeScript/JavaScript analyzer (framework identity as metadata).

Generic output: component/service/handler/guard/interceptor (frontend roles),
route, endpoint (consumed), class, interface, enum, file. Angular/React/Vue specifics
(decorators, lazy imports, hooks) are recorded in meta, never as kinds.
"""

import re

NAME = "typescript"
KIND = "language"
EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
FILENAMES = set()
PATH_HINTS = ()
PRIORITY = 10

LANG = "typescript"

# Line-local patterns use [ \t] (never bare \s): \s would swallow newlines
# and let matches bleed across lines. URL captures additionally exclude \n
# so endpoint ids stay single-line (<=512 chars, enforced at emit).
ROUTE_RE = re.compile(r"path[ \t]*:[ \t]*['\"`]([^'\"`\n]*)['\"`]")
GUARD_RE = re.compile(r"(?:canActivate|canMatch)[ \t]*:[ \t]*\[([^\]\n]{0,500})\]")
LOAD_RE = re.compile(
    r"loadComponent[ \t]*:[ \t]*\(\)[ \t]*=>[ \t]*import\(['\"`]([^'\"`\n]{0,300})['\"`]\)"
    r"\.then\(\(m\)[ \t]*=>[ \t]*m\.(\w+)\)")
HTTP_RE = re.compile(
    r"\.http[ \t]*\.[ \t]*(get|post|put|delete|patch)[ \t]*[<(](?:[^)\n]|\n){0,500}?['\"`](/(?:[^'\"`\n]*))?['\"`]")
HTTP2_RE = re.compile(
    r"this\.http[ \t]*\.[ \t]*(get|post|put|delete|patch)[ \t]*<[ \t]*[^>\n]{0,200}>[ \t\n]*\([ \t\n]*['\"`]([^'\"`\n]{0,300})['\"`]")
FETCH_RE = re.compile(
    r"""\bfetch[ \t]*\([ \t\n]*['"`]([^'"`\n]{0,300})['"`]""")
# `method: "POST"` inside a fetch options literal. The options window itself
# is sliced (not matched), so no pattern here spans lines.
FETCH_METHOD_RE = re.compile(r"""method[ \t]*:[ \t]*['"`]([A-Za-z]{1,20})['"`]""")
FETCH_METHOD_KEY_RE = re.compile(r"""method[ \t]*:""")
FETCH_METHOD_SHORT_RE = re.compile(r"[{,][ \t]*method[ \t]*[,}]")
FETCH_HOOK_HINT_RE = re.compile(r"\b(useQuery|useMutation)\b")
# axios.get/post/put/patch/delete/head/options(url, ...) and $http.get/...(url).
# The gap between `(` and the URL allows newlines (multi-line calls) but the
# URL capture itself excludes newlines, so endpoint ids stay single-line.
AXIOS_CALL_RE = re.compile(
    r"""\baxios[ \t]*\.[ \t]*(get|post|put|patch|delete|head|options)[ \t]*\([ \t\n]*['"`]([^'"`\n]{0,300})['"`]""")
HTTP_SHORT_RE = re.compile(
    r"""(?<![\w$])\$http[ \t]*\.[ \t]*(get|post|put|patch|delete|head|options)[ \t]*\([ \t\n]*['"`]([^'"`\n]{0,300})['"`]""")
# axios({method, url}) / $http({method, url}). The body capture allows one
# nesting level (e.g. headers: {...}) so method/url after a nested object
# are still seen; it is bounded by braces, never by \s, and stays inside
# the config literal. url/method sub-patterns stay single-line.
CONFIG_CALL_RE = re.compile(
    r"""(?<![\w$])(axios|\$http)[ \t]*\([ \t\n]*\{((?:[^{}]|\{[^{}]*\}){0,500})\}""")
CONFIG_METHOD_RE = re.compile(r"""method[ \t]*:[ \t]*['"`]([A-Za-z]{1,20})['"`]""")
CONFIG_METHOD_KEY_RE = re.compile(r"""method[ \t]*:""")
CONFIG_URL_RE = re.compile(r"""url[ \t]*:[ \t]*['"`]([^'"`\n]{0,300})['"`]""")
# Declarations: exported and plain. Non-exported symbols are still declared
# symbols, so they get nodes + file defines like exported ones.
EXPORT_DECL_RE = re.compile(
    r"^[ \t]*export[ \t]+(?:default[ \t]+)?(?:abstract[ \t]+)?(?:const[ \t]+)?"
    r"(class|interface|enum|type)[ \t]+(\w+)")
PLAIN_DECL_RE = re.compile(
    r"^[ \t]*(?:declare[ \t]+|abstract[ \t]+|default[ \t]+)?"
    r"(class|interface|enum)[ \t]+(\w+)")
CLASS_RE = re.compile(r"export[ \t]+(?:default[ \t]+)?(?:class|function)[ \t]+(\w+)")
FUNC_RE = re.compile(
    r"export[ \t]+(?:async[ \t]+)?function[ \t]+(\w+)|"
    r"(?:const|let|var)[ \t]+(\w+)[ \t]*=[ \t]*(?:async[ \t]+)?\(|"
    r"(?:const|let|var)[ \t]+(\w+)[ \t]*=[ \t]*(?:async[ \t]*)?\(")
INJECTABLE_RE = re.compile(r"@Injectable")
COMPONENT_RE = re.compile(r"@Component")
GUARD_IFACE = re.compile(
    r"implements[ \t]+([\w, \t]*?(?:CanActivate|CanMatch)[\w, \t]*)")
INTERCEPTOR_RE = re.compile(
    r"implements[ \t]+([\w, \t]*?HttpInterceptor[\w, \t]*)")
REACT_HOOK_RE = re.compile(
    r"\b(useState|useEffect|useReducer|useContext|useQuery|useSWR)[ \t]*\(")
REACT_COMPONENT_RE = re.compile(
    r"(?:export[ \t]+(?:default[ \t]+)?function[ \t]+([A-Z]\w*)|"
    r"(?:const|let)[ \t]+([A-Z]\w*)[ \t]*=[ \t]*(?:\([^)]*\)|[^=\n]*?)[ \t]*=>[ \t]*(?:\(|<))")
VUE_SFC_RE = re.compile(r"<(template|script|style)[^>\n]*>")
IMPORT_RE = re.compile(
    r"""import[ \t]+(?:[^'"`\n]*?[ \t]+from[ \t]+)?['"`]([^'"`\n]+)['"`]""")
ROUTE_LIB_RE = re.compile(
    r"""(?:from[ \t]+['"`](?:react-router(?:-dom)?|vue-router|@angular/router|svelte-routing)['"`])|"""
    r"""(?:createBrowserRouter|createRoutesFromElements|<Route[ \t\n]|useRoutes[ \t]*\()""")
# Same-file call detection: pass 1 collects defined function/method names.
FUNC_DEF_RE = re.compile(
    r"(?:function[ \t]+(\w+)|(?:const|let|var)[ \t]+(\w+)[ \t]*=[ \t]*(?:async[ \t]*)?[\(<]|"
    r"(?:public|private|protected|static|async|[ \t])*(\w+)[ \t]*\([^;{}\n]*\)[ \t]*\{)")
CALL_RE = re.compile(r"\b([A-Za-z_][\w]*)[ \t]*\(")
CALL_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "catch", "return", "new", "import",
    "function", "const", "let", "var", "class", "typeof", "instanceof",
    "super", "this", "await", "yield", "throw", "else", "do", "try",
})


def can_handle(path, text=None):
    return path.rsplit(".", 1)[-1].lower() in (
        "ts", "tsx", "js", "jsx", "mjs", "cjs") and not path.endswith(".d.ts")


def _emit_ts_calls(ctx, src, current_class, body, i, defined, rel):
    """Emit same-file calls edges for call sites found in `body`."""
    _src_name = (src.rsplit("#", 1)[-1] if "#" in src
                 else src.split(":")[-1])
    for _cm in CALL_RE.finditer(body):
        _t = _cm.group(1)
        if _t in CALL_KEYWORDS or _t in ("this", _src_name):
            continue
        if _t in defined:
            _dst = (f"{current_class[1]}#{_t}" if current_class
                    else f"ts:func:{_t}")
            if _dst != src:
                ctx.edge(src, _dst, "calls", i, "HIGH",
                         {"via": "same-file", "lang": LANG})


def _method_id_shape_example():
    # Reference shape kept stable for the class->method defines edge:
    #   class node  "ts:service:ClaimService"
    #   method node "ts:service:ClaimService#submitClaim"
    return "ts:service:ClaimService#submitClaim"


def _norm_url(url):
    url = (url or "").split("?")[0].split("${")[0].split("{")[0]
    if not url or not url.startswith("/"):
        return None
    if url != "/" and url.endswith("/"):
        url = url.rstrip("/")
    url = re.sub(r"/\d+$", "/{id}", url)
    if "\n" in url or len(url) > 400:
        return None
    return url or None


def _emit_consumer(ctx, rel, i, method, url, via, hook=None,
                   _known_paths=None, _method_state="known"):
    """Emit a frontend endpoint-consumption ref.

    Known method -> `endpoint:<METHOD> <path>` at LOW, as before.
    `_method_state="unknown"` (an options/config object names `method`
    but its value is not a static literal) -> resolve against the same
    file's known consumer paths when exactly one method is known for that
    path (resolver matches any method same path); otherwise
    `endpoint:* <path>` at LOW with meta method=unknown. The unknown
    branch never defaults to GET and never fabricates a method node.
    Bare calls with no options object keep the platform default (GET for
    fetch, the axios/$http default) — that default is language semantics,
    not a guess. Endpoint node ids are single-line (see _norm_url).
    """
    url = _norm_url(url)
    if url is None:
        return
    if not method and _method_state == "unknown" \
            and _known_paths is not None:
        known = _known_paths.get(url, set())
        known = {m for m in known if m != "*"}
        if len(known) == 1:
            method = next(iter(known))
            _method_state = "known"
    if method:
        method = method.upper()
        eid = f"endpoint:{method} {url}"[:512].split("\n")[0]
        meta = {"lang": LANG, "observed": "frontend-consumer", "via": via}
    else:
        eid = f"endpoint:* {url}"[:512].split("\n")[0]
        meta = {"lang": LANG, "observed": "frontend-consumer", "via": via,
                "method": "unknown"}
    if hook:
        meta["hook"] = hook
    ctx.node(eid, "endpoint", eid[len("endpoint:"):], i, "LOW", meta)
    edge_meta = {"http": method or "*", "via": via, "lang": LANG}
    if hook:
        edge_meta["hook"] = hook
    if method is None:
        edge_meta["method"] = "unknown"
    ctx.edge(f"file:{rel}", eid, "consumes", i, "LOW", edge_meta)


def _options_method(text, fetch_start):
    """Classify the options of a fetch(...) call.

    Returns (state, method) where state is "bare" (no options object —
    the platform default GET applies), "known" (static literal found), or
    "unknown" (an options object names `method` but its value is not
    statically discoverable, or the tail cannot be sliced).

    Slices (never regex-scans) the balanced tail after the first comma of
    the fetch call; bare `method` shorthand and non-literal values stay
    unknown. The slice is bounded.
    """
    open_paren = text.find("(", fetch_start)
    if open_paren < 0:
        return ("unknown", None)
    depth = 1
    in_str = None
    comma = None
    j = open_paren + 1
    n = len(text)
    while j < n:
        c = text[j]
        if in_str:
            if c == "\\":
                j += 2
                continue
            if c == in_str:
                in_str = None
        elif c in ("'", '"'):
            in_str = c
        elif c in ("([{"):
            depth += 1
        elif c in (")]}"):
            depth -= 1
            if depth == 0:
                break
        elif c == "," and depth == 1 and comma is None:
            comma = j
            break
        j += 1
    if comma is None:
        return ("bare", None)
    k = comma + 1
    depth = 0
    in_str = None
    end = None
    while k < n:
        c = text[k]
        if in_str:
            if c == "\\":
                k += 2
                continue
            if c == in_str:
                in_str = None
        elif c in ("'", '"'):
            in_str = c
        elif c in ("([{"):
            depth += 1
        elif c in (")]}"):
            if depth == 0:
                end = k
                break
            depth -= 1
        k += 1
    window = text[comma + 1:end if end is not None else min(n, comma + 2001)]
    window = window[:2000]
    m = FETCH_METHOD_RE.search(window)
    if m:
        meth = m.group(1).upper()
        if meth in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD",
                    "OPTIONS"):
            return ("known", meth)
        return ("unknown", None)
    # A bare `method` shorthand ({method}) passes a variable through and
    # is unknown. A `method:` key with no string literal (an options
    # object that names method dynamically, e.g. method: m) is unknown.
    # Otherwise the tail holds no method key at all (headers/body-only
    # options) and the platform default GET applies.
    if FETCH_METHOD_SHORT_RE.search(" " + window):
        return ("unknown", None)
    if FETCH_METHOD_KEY_RE.search(window):
        return ("unknown", None)
    return ("bare", None)


def _framework_of(text, rel):
    if "@angular/" in text or "@Component" in text or "loadComponent" in text:
        return "angular"
    if "from 'react'" in text or 'from "react"' in text or "react-router" in text:
        return "react"
    if "from 'vue'" in text or 'from "vue"' in text or "vue-router" in text \
            or ("<template>" in text and "<script" in text):
        return "vue"
    if ".svelte" in rel or "svelte" in text[:2000]:
        return "svelte"
    return None


def scan(ctx, path, text):
    rel = ctx.path
    is_spec = rel.endswith((".spec.ts", ".spec.tsx", ".spec.ts",
                             ".test.ts", ".test.tsx", ".test.js"))
    fw = _framework_of(text, rel)
    ctx.node(f"file:{rel}", "file", rel.split("/")[-1], 1, "HIGH",
             {"lang": LANG, "framework": fw,
              "role": "test" if is_spec else "source"})
    lines = text.splitlines()
    # pass 1: collect defined function/method names (for same-file calls)
    defined = set()
    for _dm in FUNC_DEF_RE.finditer(text):
        for _g in _dm.groups():
            if _g:
                defined.add(_g)
    # pass 2: line scan; track enclosing class + function for call sources
    current_class = None   # (name, node-id)
    current_func = None    # node-id of enclosing function/method
    brace_depth = 0        # reset class scope when its block closes
    class_depth = None
    declared = {}          # node-id -> (line, conf) for file defines

    def _declare(nid, kind, name, i, conf, meta):
        ctx.node(nid, kind, name, i, conf, meta)
        declared.setdefault(nid, (i, conf))

    # module imports -> generic imports edges (cross-file structure)
    for m in IMPORT_RE.finditer(text):
        target = m.group(1)
        line = text[:m.start()].count("\n") + 1
        if target.startswith("."):
            ctx.edge(f"file:{rel}", f"unresolved:module:{target}",
                     "imports", line, "MEDIUM",
                     {"lang": LANG, "framework": fw})
    # Whole-text HTTP consumer pass (multi-line calls included). Per-line
    # matching below would miss `fetch(\n  url,\n  {method...})`.
    # Pre-pass: known (method, path) pairs, so unknown-method refs can
    # match any same-path method known in this file (never default GET).
    _known_paths = {}
    for _hm in list(HTTP_RE.finditer(text)):
        _u = _norm_url(_hm.group(2))
        if _u:
            _known_paths.setdefault(_u, set()).add(_hm.group(1).upper())
    for _hm in list(HTTP2_RE.finditer(text)):
        _u = _norm_url(_hm.group(2))
        if _u:
            _known_paths.setdefault(_u, set()).add(_hm.group(1).upper())
    for _am in list(AXIOS_CALL_RE.finditer(text)) + \
            list(HTTP_SHORT_RE.finditer(text)):
        _u = _norm_url(_am.group(2))
        if _u:
            _known_paths.setdefault(_u, set()).add(_am.group(1).upper())
    for _cm in CONFIG_CALL_RE.finditer(text):
        _mm = CONFIG_METHOD_RE.search(_cm.group(2))
        _um = CONFIG_URL_RE.search(_cm.group(2))
        if _um is not None and _mm is not None:
            _u = _norm_url(_um.group(1))
            if _u:
                _known_paths.setdefault(_u, set()).add(
                    _mm.group(1).upper())
    _http_spans = []  # consumed char spans, so the line pass can skip them
    for hm in list(HTTP_RE.finditer(text)):
        _http_spans.append((hm.start(), hm.end()))
        _emit_consumer(ctx, rel, text[:hm.start()].count("\n") + 1,
                       hm.group(1), hm.group(2), "http")
    for hm in list(HTTP2_RE.finditer(text)):
        _http_spans.append((hm.start(), hm.end()))
        _emit_consumer(ctx, rel, text[:hm.start()].count("\n") + 1,
                       hm.group(1), hm.group(2), "http")
    for am in list(AXIOS_CALL_RE.finditer(text)) + \
            list(HTTP_SHORT_RE.finditer(text)):
        _http_spans.append((am.start(), am.end()))
        _emit_consumer(ctx, rel, text[:am.start()].count("\n") + 1,
                       am.group(1), am.group(2),
                       "axios" if am.group(0).lstrip()[:5] == "axios"
                       else "$http")
    for cm in CONFIG_CALL_RE.finditer(text):
        _http_spans.append((cm.start(), cm.end()))
        _body = cm.group(2)
        _mm = CONFIG_METHOD_RE.search(_body)
        _um = CONFIG_URL_RE.search(_body)
        if _um is None:
            continue
        if _mm is not None:
            _state, _meth = "known", _mm.group(1)
        elif CONFIG_METHOD_KEY_RE.search(_body):
            # method: <non-literal expression> -> unknown, never GET
            _state, _meth = "unknown", None
        else:
            # no method key: axios/$http object-form default is GET
            _state, _meth = "bare", "GET"
        _emit_consumer(ctx, rel, text[:cm.start()].count("\n") + 1,
                       _meth, _um.group(1),
                       "axios" if cm.group(1) == "axios" else "$http",
                       _known_paths=_known_paths, _method_state=_state)
    for fm in FETCH_RE.finditer(text):
        fstart = fm.start()
        if any(s <= fstart < e for s, e in _http_spans):
            continue
        _http_spans.append((fm.start(), fm.end()))
        _line = text[:fstart].count("\n") + 1
        _state, _meth = _options_method(text, fstart)
        if _state == "bare":
            # no options object: the platform default GET applies
            _meth = "GET"
        elif _state == "unknown":
            _meth = None
        _hook = None
        if FETCH_HOOK_HINT_RE.search(text[max(0, fstart - 500):fstart]):
            _tail = text[max(0, fstart - 500):fstart]
            _hm = None
            for _cand in FETCH_HOOK_HINT_RE.finditer(_tail):
                _hm = _cand.group(1)
            _hook = _hm
        _emit_consumer(ctx, rel, _line, _meth, fm.group(1), "fetch",
                       hook=_hook, _known_paths=_known_paths,
                       _method_state=_state)
    decorators = []
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("@"):
            decorators.append(stripped)
            continue
        # Non-exported declarations are still declared symbols.
        pm = PLAIN_DECL_RE.match(line)
        if pm and not CLASS_RE.search(line):
            dkind, dname = pm.group(1), pm.group(2)
            nkind = {"class": "class", "interface": "interface",
                     "enum": "enum"}[dkind]
            nid = f"ts:{nkind}:{dname}"
            _declare(nid, nkind, dname, i, "HIGH",
                     {"lang": LANG, "framework": fw, "exported": False})
            if current_class is None:
                current_class = (dname, nid) if dkind == "class" else None
                if dkind == "class":
                    class_depth = brace_depth
            decorators = []
        cm = CLASS_RE.search(line)
        if cm:
            cname = cm.group(1)
            dec = " ".join(decorators)
            if re.search(r"\bfunction[ \t]+" + re.escape(cname), line):
                # `export function foo` — a plain function, not a class.
                # Track as enclosing scope so its call sites link.
                current_class = None
                current_func = f"ts:func:{cname}"
                _declare(current_func, "function", cname, i, "HIGH",
                         {"lang": LANG, "framework": fw})
                decorators = []
                # calls on the definition line itself (one-liner bodies)
                if "{" in line:
                    _emit_ts_calls(ctx, current_func, None,
                                   line.split("{", 1)[1], i, defined, rel)
                continue
            if COMPONENT_RE.search(dec):
                nkind, nid = "component", f"ts:component:{cname}"
            elif INJECTABLE_RE.search(dec):
                nkind, nid = "service", f"ts:service:{cname}"
            elif "Guard" in cname or GUARD_IFACE.search(line):
                nkind, nid = "guard", f"ts:guard:{cname}"
            elif INTERCEPTOR_RE.search(line):
                nkind, nid = "interceptor", f"ts:interceptor:{cname}"
            elif fw == "react" and cname[:1].isupper():
                nkind, nid = "component", f"ts:component:{cname}"
            elif "Handler" in cname or "Resolver" in cname:
                nkind, nid = "handler", f"ts:handler:{cname}"
            else:
                nkind, nid = "class", f"ts:class:{cname}"
            _declare(nid, nkind, cname, i, "HIGH",
                     {"lang": LANG, "framework": fw,
                      "decorators": dec[:200]})
            current_class = (cname, nid)
            current_func = None
            class_depth = brace_depth
            decorators = []
            continue
        if not cm:
            em = EXPORT_DECL_RE.match(line)
            if em:
                ekind, ename = em.group(1), em.group(2)
                if ekind == "type":
                    _declare(f"ts:type:{ename}", "class", ename, i,
                             "MEDIUM",
                             {"lang": LANG, "framework": fw,
                              "via": "type-alias"})
                else:
                    nkind = {"class": "class", "interface": "interface",
                             "enum": "enum"}[ekind]
                    nid = f"ts:{nkind}:{ename}"
                    _declare(nid, nkind, ename, i, "HIGH",
                             {"lang": LANG, "framework": fw,
                              "exported": True})
                    if ekind == "class":
                        current_class = (ename, nid)
                        current_func = None
                        class_depth = brace_depth
                decorators = []
            rm = REACT_COMPONENT_RE.search(line)
            if rm and fw in ("react", None):
                cname = rm.group(1) or rm.group(2)
                nid = f"ts:component:{cname}"
                _declare(nid, "component", cname, i, "MEDIUM",
                         {"lang": LANG, "framework": fw or "react",
                          "via": "function-component"})
        if stripped and not stripped.startswith("@"):
            decorators = []
        # routes (any router flavour, not just Angular)
        if ("routes" in rel or "router" in rel or ROUTE_LIB_RE.search(text)
                or "app.routes.ts" in rel):
            rm = ROUTE_RE.search(line)
            if rm and "path" in line:
                route = "/" + rm.group(1).strip("/")
                rid = f"route:{route}"
                ctx.node(rid, "route", route, i, "HIGH",
                         {"lang": LANG, "framework": fw})
                gm = GUARD_RE.search(line + "".join(lines[i:i + 2]))
                if gm:
                    for g in re.findall(r"(\w+)", gm.group(1)):
                        if g in ("true", "false"):
                            continue
                        ctx.edge(rid, f"unresolved:guard:{g}",
                                 "guarded-by", i, "MEDIUM",
                                 {"lang": LANG})
                lm = LOAD_RE.search("\n".join(lines[max(0, i - 1):i + 2]))
                if lm:
                    ctx.edge(rid, f"unresolved:component:{lm.group(2)}",
                             "navigates", i, "HIGH",
                             {"lazy": lm.group(1), "lang": LANG})
        # HTTP client calls: the whole-text pass above already emitted
        # fetch/axios/$http/HttpClient refs (multi-line safe). This line
        # pass only emits HttpClient matches not covered by a span, so
        # single-line `.http.post(...)` forms keep working without dupes.
        _line_start = sum(len(x) + 1 for x in lines[:i - 1])
        for hm in list(HTTP_RE.finditer(line)):
            _abs = _line_start + hm.start()
            if any(s <= _abs < e for s, e in _http_spans):
                continue
            _http_spans.append((_abs, _abs + hm.end() - hm.start()))
            _emit_consumer(ctx, rel, i, hm.group(1), hm.group(2), "http")
        for hm in list(HTTP2_RE.finditer(line)):
            _abs = _line_start + hm.start()
            if any(s <= _abs < e for s, e in _http_spans):
                continue
            _http_spans.append((_abs, _abs + hm.end() - hm.start()))
            _emit_consumer(ctx, rel, i, hm.group(1), hm.group(2), "http")
        # same-file calls: enclosing scope -> defined function (HIGH).
        # add_node/add_edge dedupe by id, so re-emitting is safe.
        _fd = FUNC_DEF_RE.search(line)
        if _fd:
            _fname = _fd.group(1) or _fd.group(2) or _fd.group(3)
            if _fname and _fname in defined \
                    and _fname not in CALL_KEYWORDS:
                if current_class:
                    current_func = f"{current_class[1]}#{_fname}"
                    ctx.node(current_func, "method", _fname, i,
                             "MEDIUM",
                             {"lang": LANG, "via": "member-function"})
                    ctx.edge(current_class[1], current_func,
                             "defines", i, "MEDIUM", {"lang": LANG})
                    declared.setdefault(current_func, (i, "MEDIUM"))
                elif not current_func or not current_func.endswith(
                        f":{_fname}"):
                    current_func = f"ts:func:{_fname}"
                    _declare(current_func, "function", _fname, i,
                             "MEDIUM", {"lang": LANG})
                # calls on the definition line itself (one-liner bodies)
                if "{" in line:
                    _emit_ts_calls(ctx, current_func, current_class,
                                   line.split("{", 1)[1], i, defined, rel)
                brace_depth += line.count("{") - line.count("}")
                continue
        if current_func and "(" in line \
                and not stripped.startswith(("import ", "export ")):
            _emit_ts_calls(ctx, current_func, current_class, line,
                           i, defined, rel)
        brace_depth += line.count("{") - line.count("}")
        if current_class and class_depth is not None \
                and brace_depth <= class_depth and "}" in line:
            current_class = None
            current_func = None
            class_depth = None
    # 2.4: every declared symbol gets a file->symbol defines edge,
    # unconditionally (deduped by add_edge), at its own declaration
    # confidence. Covers plain declarations, function components, type
    # aliases, and class members.
    for _nid, (_ln, _conf) in declared.items():
        if _nid.startswith("file:"):
            continue
        ctx.edge(f"file:{rel}", _nid, "defines", _ln, _conf, {})
