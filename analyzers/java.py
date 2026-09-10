"""Java analyzer (incl. Spring/JPA/JDBC conventions as metadata).

Emits only generic graph kinds: class/interface/enum (for records too),
method, endpoint, controller/service/configuration, table/sequence,
configuration keys, file. Framework identity (spring/jpa/...) lives in
meta, never in kinds or edge types.
"""

import re

NAME = "java"
KIND = "language"
EXTENSIONS = {".java"}
FILENAMES = set()
PATH_HINTS = ()
PRIORITY = 10

LANG = "java"

HTTP = {
    "GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
    "DeleteMapping": "DELETE", "PatchMapping": "PATCH", "RequestMapping": "GET",
}
CLASS_RE = re.compile(
    r"^\s*(?:public|protected|private|abstract|final|sealed)?\s*"
    r"(?:class|interface|enum|record)\s+(\w+)"
)
METHOD_RE = re.compile(
    r"^\s*(?:public|protected|private|@\w+[ \t]+)?"
    r"(?:(?:static|final|synchronized|abstract|default)[ \t]+)*"
    r"(?:[\w<>\[\]?., \t]+[ \t]+)?(\w+)[ \t]*\([^;{}]*\)[ \t]*(?:throws[ \t]+[\w, \t]+)?[ \t]*[{;]?[ \t]*$"
)
CALL_RE = re.compile(
    r"\b([A-Za-z_][\w]*)\s*\.\s*([a-z][\w]*)\s*\("
    r"|\b([a-z][\w]*)\s*\("
    r"|\bthis\s*\.\s*([a-z][\w]*)\s*\(")
CALL_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "catch", "return", "new", "throw",
    "assert", "super", "else", "do", "try", "synchronized", "import",
    "package", "record", "class", "interface", "enum",
})
FIELD_DECL_RE = re.compile(
    r"^\s*private\s+(?:final\s+)?([\w<>., \t]+?)\s+(\w+)\s*[;=]")
NEW_RE = re.compile(r"\bnew\s+([A-Z][\w]*)\s*\(")
FIELD_INJECT_RE = re.compile(
    r"^\s*private\s+(?:final\s+)?([A-Z][\w<>]*)\s+\w+\s*[;=]")
TABLE_RE = re.compile(r'@Table\s*\(\s*name\s*=\s*"([^"]+)"')
REPO_RE = re.compile(r"interface\s+(\w+)\s+extends\s+JpaRepository\s*<\s*(\w+)")
SQL_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+([a-z][a-z0-9_]*)\b", re.IGNORECASE)
SEQ_RE = re.compile(r"nextval\s*\(\s*'([^']+)'")
MAPPING_RE = re.compile(
    r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)"
    r"(?:\s*\(\s*(?:value\s*=\s*)?\"([^\"]*)\")?"
    r"(?:\s*\(\s*(?:params\s*=\s*)?\"([^\"]*)\")?"
    r"[^)]*\)?")
MAPPING_PATH_RE = re.compile(r"(?:value|path)\s*=\s*\"([^\"]*)\"")
PATH_ARRAY_RE = re.compile(
    r"(?:value|path)\s*=\s*\{\s*\"([^\"]*)\"")
MAPPING_PLAIN_RE = re.compile(
    r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)"
    r"\s*\(\s*\"([^\"]*)\"")
VALUE_RE = re.compile(r"@Value\s*\(\s*\"([^\"]+)\"")
SCHED_RE = re.compile(r"@Scheduled\s*(\([^)]*\))?")
TEST_RE = re.compile(
    r"(?:@\w+[ \t]+)*(?:public[ \t]+)?void[ \t]+(test\w*|\w+Test\w*)[ \t]*\(")


def can_handle(path, text=None):
    return path.endswith(".java")


def _mapping_path(annotation):
    pm = MAPPING_PLAIN_RE.search(annotation)
    if pm:
        return pm.group(2)
    arr = PATH_ARRAY_RE.search(annotation)
    if arr:
        return arr.group(1)
    kv = MAPPING_PATH_RE.search(annotation)
    if kv:
        return kv.group(1)
    return ""


def _test_cases(ctx, text):
    for m in TEST_RE.finditer(text):
        line = text[:m.start()].count("\n") + 1
        tid = f"test:{ctx.path}#{m.group(1)}"
        ctx.node(tid, "test-case", m.group(1), line, "MEDIUM", {})
        ctx.edge(f"file:{ctx.path}", tid, "defines", line, "MEDIUM", {})


def scan(ctx, path, text):
    rel = ctx.path
    ctx.node(f"file:{rel}", "file", path.split("/")[-1], 1, "HIGH",
             {"lang": LANG,
              "role": "test" if "/test/" in rel else "source"})
    if "/test/" in rel:
        _test_cases(ctx, text)
    lines = text.splitlines()
    pkg = ""
    m = re.search(r"^package[ \t]+([\w.]+);", text, re.M)
    if m:
        pkg = m.group(1)
    cls = None
    kind = "class"
    stereotypes = set()
    base_path = ""
    pre_annotations = []
    current_method = None
    pending_mapping = None  # (http, path, line)

    field_types = {}
    for fline in lines:
        fm0 = FIELD_DECL_RE.match(fline)
        if fm0:
            ftype = re.sub(r"<.*", "", fm0.group(1)).split(".")[-1].strip()
            if ftype and ftype[0].isupper():
                field_types[fm0.group(2)] = ftype

    def cls_id(name):
        return f"java:class:{pkg}.{name}" if pkg else f"java:class:{name}"

    def method_id(cname, mname):
        base = f"{pkg}.{cname}" if pkg else cname
        return f"java:method:{base}#{mname}"

    defined_methods = set()
    for dline in lines:
        dm = re.match(
            r"^\s*(?:public|protected|private)\s+"
            r"(?:(?:static|final|synchronized|abstract|default)\s+)*"
            r"(?:[\w<>\[\]?., \t]+\s+)?(\w+)\s*\(", dline)
        if dm and "(" in dline:
            defined_methods.add(dm.group(1))

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("*") or stripped.startswith("/*") \
                or stripped.startswith("*/") or stripped.startswith("//"):
            continue
        if stripped.startswith("@"):
            pre_annotations.append((stripped, i))
            am = MAPPING_RE.search(stripped)
            if am and cls is None:
                base_path = _mapping_path(stripped) or am.group(2) or ""
            if am and cls is not None:
                pending_mapping = (
                    HTTP[am.group(1)],
                    _mapping_path(stripped) or (am.group(2) or ""), i)
            for ster in ("RestController", "Controller", "Service",
                         "Repository", "Component", "Configuration",
                         "Entity", "ControllerAdvice", "Embeddable",
                         "MappedSuperclass"):
                if re.match(r"@" + ster + r"\b", stripped):
                    stereotypes.add(ster)
            continue
        cm = CLASS_RE.match(line)
        if cm and cls is None:
            cls = cm.group(1)
            if "interface " in line:
                decl = "interface"
            elif "enum " in line:
                decl = "enum"
            elif "record " in line:
                # Generic graph has no record kind: closest generic is
                # class; the record nuance is preserved in meta.java_kind.
                decl = "record"
            else:
                decl = "class"
            # Generic graph folds JPA entity/repository into class +
            # meta; controllers/services keep their generic kinds.
            if decl == "record":
                kind = "class"
            else:
                kind = decl
            meta = {"lang": LANG, "package": pkg,
                    "stereotypes": sorted(stereotypes),
                    "framework": "spring" if stereotypes & {
                        "RestController", "Controller", "Service",
                        "Repository", "Component", "Configuration"} else None}
            meta = {k: v for k, v in meta.items() if v is not None}
            t = re.search(r"extends\s+(\w+)", line)
            if t:
                meta["extends"] = t.group(1)
            t = re.search(r"implements\s+([\w,\s]+?)(?:\s*\{|$)", line)
            if t:
                meta["implements"] = [x.strip() for x in t.group(1).split(",")]
            table = TABLE_RE.search("\n".join(a for a, _ in pre_annotations))
            table_name = table.group(1).lower() if table else None
            if table_name:
                meta["table"] = table.group(1)
            node_kind = ("controller" if "RestController" in stereotypes or
                         "Controller" in stereotypes
                         else "service" if "Service" in stereotypes
                         else "configuration" if "Configuration" in stereotypes
                         else kind)
            if node_kind in ("entity", "repository"):
                # Generic graph folds JPA entity/repository into class +
                # meta; controllers/services keep their generic kinds.
                meta["stereotype"] = node_kind
                node_kind = "class"
            meta["java_kind"] = decl
            ctx.node(cls_id(cls), node_kind, cls, i, "HIGH", meta)
            ctx.edge(f"file:{rel}", cls_id(cls), "defines", i, "HIGH", {})
            if "Entity" in stereotypes and table_name:
                # JPA entity <-> table: the class maps its rows, so it both
                # reads (loads) and writes (persists) that table.
                tid = f"table:{table_name}"
                ctx.node(tid, "table", table_name, i, "HIGH",
                         {"lang": LANG, "via": "jpa-entity",
                          "model": cls})
                ctx.edge(f"file:{rel}", tid, "defines", i, "HIGH", {})
                ctx.edge(cls_id(cls), tid, "reads", i, "MEDIUM",
                         {"via": "jpa-entity", "lang": LANG})
                ctx.edge(cls_id(cls), tid, "writes", i, "MEDIUM",
                         {"via": "jpa-entity", "lang": LANG})
                ctx.edge(cls_id(cls), tid, "queries", i, "LOW",
                         {"via": "jpa-entity", "lang": LANG})
            for ster in stereotypes:
                if ster in ("Service", "Component", "Repository",
                            "Configuration", "Controller",
                            "RestController"):
                    ctx.node(f"stereotype:spring:{ster}", "stereotype",
                             ster, i, "HIGH",
                             {"lang": LANG, "framework": "spring"})
                    ctx.edge(cls_id(cls), f"stereotype:spring:{ster}",
                             "references", i, "HIGH",
                             {"via": "annotation", "lang": LANG})
            repo_m = REPO_RE.search(line + "\n" + (
                lines[i] if i < len(lines) else ""))
            if repo_m:
                ent = repo_m.group(2)
                ctx.edge(cls_id(cls), f"entity:{ent}", "reads", i,
                         "HIGH", {"via": "JpaRepository", "lang": LANG})
                ctx.edge(cls_id(cls), f"entity:{ent}", "writes", i,
                         "MEDIUM", {"via": "JpaRepository", "lang": LANG})
            pre_annotations = []
            continue
        if cls is None:
            pre_annotations = []
            continue
        mm = METHOD_RE.match(line)
        if mm is None and "(" in line and re.search(
                r"\b(public|protected|private)\b", line):
            hm = re.match(
                r"^\s*(?:public|protected|private)\s+"
                r"(?:(?:static|final|synchronized|abstract|default)\s+)*"
                r"(?:[\w<>\[\]?., \t]+\s+)?(\w+)\s*\(", line)
            if hm:
                mm = hm
        is_decl = (
            mm and "(" in line
            and re.match(
                r"^\s*(?:public|protected|private|(?:static|final|synchronized|abstract|default)\s+)+",
                line)
            and not line.strip().startswith(("return ", "throw ", "new "))
            and "->" not in line and "=" not in line.split("(")[0]
            and ("{" in line or line.rstrip().endswith(")")
                 or line.count("(") == line.count(")"))
        )
        if not is_decl and mm and "(" in line and line.count("(") > line.count(")") \
                and re.match(r"^\s*(?:public|protected|private)\b", line) \
                and "=" not in line.split("(")[0] and "->" not in line \
                and not line.strip().startswith(("return ", "throw ", "new ")):
            is_decl = True
        if is_decl:
            mname = mm.group(1)
            if mname in ("if", "for", "while", "switch", "catch", "return",
                         "new", "class"):
                is_decl = False
        if is_decl:
            mname = mm.group(1)
            if mname == cls and re.match(
                    rf"^\s*(?:public|protected|private)\s+{re.escape(cls)}\s*\(",
                    line):
                for param in line[line.find("(") + 1:line.rfind(")")].split(","):
                    parts = param.strip().split()
                    if len(parts) >= 2:
                        dep = re.sub(r"<.*", "", parts[-2].split(".")[-1])
                        if dep and dep[0].isupper() and dep not in (
                                "String", "Long", "Integer", "Boolean",
                                "BigDecimal", "List", "Optional", "Clock",
                                "Locale", "ObjectMapper"):
                            ctx.edge(cls_id(cls),
                                     f"unresolved:class:{dep}", "injects",
                                     i, "HIGH",
                                     {"via": "constructor", "lang": LANG})
                            field_types.setdefault(parts[-1], dep)
                current_method = None
                pre_annotations = []
                continue
            current_method = mname
            mid = method_id(cls, mname)
            ctx.node(mid, "method", f"{cls}.{mname}", i, "HIGH",
                     {"lang": LANG, "class": cls_id(cls)})
            ctx.edge(f"file:{rel}", mid, "defines", i, "HIGH", {})
            ctx.edge(cls_id(cls), mid, "defines", i, "HIGH", {})
            if pending_mapping:
                http, sub, mline = pending_mapping
                full = (base_path + sub) or "/"
                full = "/" + full.strip("/")
                eid = f"endpoint:{http} {full}"
                ctx.node(eid, "endpoint", f"{http} {full}", mline,
                         "HIGH", {"lang": LANG, "framework": "spring",
                                  "handler": mid, "controller": cls})
                ctx.edge(eid, mid, "handled-by", mline, "HIGH", {})
                pending_mapping = None
            sched = SCHED_RE.search(stripped)
            if sched:
                ctx.node("schedule:cron", "schedule", "cron", i,
                         "MEDIUM", {"lang": LANG, "framework": "spring"})
                ctx.edge(mid, "schedule:cron", "triggered-by", i, "HIGH",
                         {"schedule": sched.group(1) or "", "lang": LANG})
            pre_annotations = []
            continue
        fm = FIELD_INJECT_RE.match(line)
        if fm and current_method is None:
            dep = re.sub(r"<.*", "", fm.group(1))
            if dep not in ("String", "Long", "Integer", "Boolean",
                           "BigDecimal", "List", "Optional", "ObjectMapper",
                           "Clock", "Locale"):
                ctx.edge(cls_id(cls), f"unresolved:class:{dep}", "injects",
                         i, "MEDIUM", {"lang": LANG})
        for cmatch in CALL_RE.finditer(line):
            receiver, target_m = None, None
            if cmatch.group(1) is not None:
                receiver, target_m = cmatch.group(1), cmatch.group(2)
            elif cmatch.group(3) is not None:
                target_m = cmatch.group(3)
            else:
                target_m = cmatch.group(4)
            if target_m in CALL_KEYWORDS:
                continue
            if receiver in ("this", cls):
                receiver = None
            elif receiver == cls:
                continue
            edge_src = method_id(cls, current_method) if current_method else cls_id(cls)
            if receiver is None:
                if target_m in defined_methods:
                    ctx.edge(edge_src, method_id(cls, target_m), "calls",
                             i, "HIGH", {"via": "same-class", "lang": LANG})
                continue
            target_cls = (field_types.get(receiver)
                          if receiver[0].islower() else receiver)
            if target_cls is None:
                continue
            else:
                ctx.edge(edge_src,
                         f"unresolved:method:{target_cls}#{target_m}",
                         "calls", i,
                         "MEDIUM" if receiver[0].islower() else "LOW",
                         ({"receiver": receiver, "lang": LANG}
                          if receiver[0].islower() else {"lang": LANG}))
        for nmatch in NEW_RE.finditer(line):
            if nmatch.group(1) != cls:
                edge_src = method_id(cls, current_method) if current_method else cls_id(cls)
                ctx.edge(edge_src, f"unresolved:class:{nmatch.group(1)}",
                         "references", i, "LOW",
                         {"via": "instantiation", "lang": LANG})
        if ('"' in line or "'" in line) and re.search(
                r"(?i)\b(select|insert|update|delete|from|join|nextval)\b", line):
            edge_src = method_id(cls, current_method) if current_method else cls_id(cls)
            for tm in SQL_TABLE_RE.finditer(line):
                tbl = tm.group(1).lower()
                if tbl in ("select", "where", "set", "values", "order", "group"):
                    continue
                ctx.edge(edge_src, f"table:{tbl}", "reads", i, "MEDIUM",
                         {"via": "jdbc", "lang": LANG})
            for sm in SEQ_RE.finditer(line):
                ctx.edge(edge_src, f"sequence:{sm.group(1)}", "invokes",
                         i, "MEDIUM", {"via": "jdbc", "lang": LANG})
        vm = VALUE_RE.search(line)
        if vm:
            ctx.edge(
                method_id(cls, current_method) if current_method else cls_id(cls),
                f"config-key:{vm.group(1)}", "configures", i, "HIGH",
                {"lang": LANG})
