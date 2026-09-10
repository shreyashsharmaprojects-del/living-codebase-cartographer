"""Visualization projection layer (read-only VIEW over graph.json).

Consumes the generic graph interface only:
    graph = {"version", "nodes"[{id,kind,name,file,line,confidence,
                                 meta{lang,framework,package,...},...}],
             "edges"[{src,dst,type,confidence,file,line,meta,...}],
             "detection"{languages,frameworks,databases,infrastructure},
             "flow_candidates"[{seed,kind,endpoint,chain}],
             "unresolved"[ids], ...}

Never imports analyzer internals. Never mutates the graph. Technology
identity comes only from node `meta` and the `detection` record.

Produces a compact view model embedded in the generated HTML:
    {"nodes":[...], "edges":[...], "groups":[...], "modes":{...},
     "search":[...], "focus":{...}, "meta":{...}}. See build_view_model().

View-model size caps keep large graphs usable: the default mode shows
architecture-level GROUP nodes (never 1371 raw nodes), and focused modes
neighborhood-expand around seeds with priority-ordered caps.
"""

import os

from analyzers import graph as G

VIZ_VERSION = 1
VIEW_NODE_CAP = 400     # max raw nodes embedded in any one mode
VIEW_EDGE_CAP = 1200    # max edges embedded in any one mode
ARCH_GROUP_CAP = 60     # max groups in the default architecture view
ARCH_GROUP_MIN = 4      # groups below this fold into kind buckets
NEIGHBOR_CAP = 120      # max nodes in a focus/impact/flow view
DEPTH_DEFAULT = 2

CONFIDENCES = ("HIGH", "MEDIUM", "LOW", "UNKNOWN")

# Modes shipped in the UI. Each maps to a builder below.
MODES = ("architecture", "dependency", "call-graph", "data-flow", "api",
         "database", "external", "impact", "flow")

# First-class structured views (primary nav). "graph" hosts the canvas +
# MODES above; the rest are sortable/filterable tables over the same model.
# "capabilities" is the Wave-4a intent-layer peer (capability -> requirements
# -> realizing code -> test coverage); empty when no intent was imported.
VIEWS = ("overview", "endpoints", "data", "dependencies", "symbols",
         "flows", "capabilities", "graph", "issues")

# Layered (Sugiyama-style) graph roles, left-to-right. Technology identity
# never leaks into layers: mapping is by generic kind only.
LAYERS = ("clients", "endpoints", "handlers", "services", "data-access",
          "stores")
LAYER_OF = {
    "component": "clients",
    "endpoint": "endpoints", "route": "endpoints", "queue": "endpoints",
    "topic": "endpoints", "event": "endpoints", "job": "endpoints",
    "schedule": "endpoints",
    "controller": "handlers", "handler": "handlers", "guard": "handlers",
    "interceptor": "handlers",
    "service": "services", "function": "services", "method": "services",
    "class": "services", "interface": "services", "enum": "services",
    "query": "data-access", "procedure": "data-access",
    "table": "stores", "view": "stores", "collection": "stores",
    "entity": "stores", "cache": "stores", "cache-key": "stores",
    "sequence": "stores", "migration": "stores", "database": "stores",
    "external-service": "stores", "deployment-unit": "stores",
}
GRAPH_NODE_CAP = 300    # max nodes laid out on canvas (banner states N of M)
SYMBOL_ROW_CAP = 3000   # max rows embedded in the symbols table

# Kind sets are generic (from the closed schema), never stack-specific.
STRUCTURAL_KINDS = ("repository", "application", "module", "package",
                    "directory", "deployment-unit", "database")
ENTRY_KINDS = ("endpoint", "route", "queue", "topic", "event", "job",
               "schedule")
LOGIC_KINDS = ("service", "controller", "handler", "component", "function",
               "method", "class", "interface", "guard", "interceptor")
DATA_KINDS = ("table", "view", "collection", "query", "procedure",
              "sequence", "cache", "cache-key", "entity", "migration")
DATA_EDGES = ("reads", "writes", "queries", "transforms", "publishes",
              "consumes", "creates", "modifies", "seeds", "invokes")
CALL_EDGES = ("calls", "handled-by", "injects", "invokes", "implements",
              "extends", "defines")
DEP_EDGES = ("depends-on", "imports", "injects", "calls", "references",
             "exposes", "consumes", "configures", "deploys-to", "tests",
             "extends", "implements")
SKIP_NODE_KINDS = ("file",)          # file nodes are noise in graph views
SKIP_EDGE_TYPES = ("defines",)       # containment impl detail, not insight

CONF_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNKNOWN": 3}
KIND_RANK = {  # lower = more architecturally significant
    "application": 0, "deployment-unit": 1, "database": 2,
    "external-service": 3, "endpoint": 4, "route": 5, "controller": 6,
    "service": 7, "handler": 8, "component": 9, "queue": 10, "topic": 10,
    "event": 10, "job": 11, "table": 12, "collection": 12, "view": 13,
    "class": 14, "interface": 15, "function": 16, "method": 17,
}


def _kind_rank(kind):
    return KIND_RANK.get(kind, 50)


def layer_for_kind(kind, fallback_layer=None):
    """Architectural-role layer; unmappable kinds take the deterministic
    longest-path fallback (or middle 'services') — never random."""
    if kind in LAYER_OF:
        return LAYER_OF[kind]
    if fallback_layer is not None:
        return LAYERS[min(len(LAYERS) - 1, max(0, fallback_layer))]
    return "services"


def project_node(n, degree, fallback_layers=None):
    meta = n.get("meta") or {}
    fb = (fallback_layers or {}).get(n["id"])
    return {
        "id": n["id"],
        "label": n.get("name") or n["id"],
        "kind": n.get("kind"),
        "lang": meta.get("lang"),
        "fw": meta.get("framework"),
        "group": group_of(n),
        "layer": layer_for_kind(n.get("kind"), fb),
        "file": n.get("file"),
        "line": n.get("line"),
        "conf": n.get("confidence", "UNKNOWN"),
        "deg": degree,
    }


def project_edge(e):
    """Edge projection keeps type/confidence/evidence/file:line for the
    clickable edge inspector (spec 4.5)."""
    return {"s": e["src"], "t": e["dst"],
            "type": e.get("type"),
            "conf": e.get("confidence", "UNKNOWN"),
            "file": e.get("file"), "line": e.get("line"),
            "ev": e.get("evidence")}


def _area_from_dotted(val):
    """com.claims.<area>[...] -> claims.<area>; language-neutral rule:
    drop one leading well-known root segment, keep at most two."""
    if not val:
        return None
    segs = str(val).replace("/", ".").split(".")
    if segs and segs[0].lower() in ("com", "org", "io", "net"):
        segs = segs[1:]
    segs = [s for s in segs if s]
    if not segs:
        return None
    return ".".join(segs[:2]) if len(segs) > 1 else segs[0]


def _area_from_id(nid):
    """java:method:com.claims.claim.Foo#bar -> claims.claim (same rule)."""
    if not nid or ":" not in nid:
        return None
    tail = nid.split(":")[-1]
    head = tail.split("#")[0]
    if "." not in head:
        return None
    return _area_from_dotted(head.rsplit(".", 1)[0])


def group_of(n):
    """Architecture grouping from existing metadata (generic, no stack ids).

    Priority: explicit structural meta (package/namespace/module) -> top
    source directory -> kind bucket (endpoints, data, external...).
    """
    meta = n.get("meta") or {}
    area = _area_from_id(n.get("id", "")) or _area_from_dotted(
        meta.get("package")) or _area_from_dotted(
        meta.get("namespace")) or _area_from_dotted(meta.get("module"))
    if area:
        return area
    f = n.get("file") or ""
    parts = [p for p in f.split("/") if p not in (".", "")]
    # Strip generic scaffolding to reach the meaningful area level:
    # backend/src/main/java/com/claims/<area>/... -> <area>.
    while len(parts) >= 2 and parts[0] in (
            "backend", "frontend", "services", "apps", "packages", "src",
            "api", "ui", "server", "client", "db", "migrations",
            "test", "tests", "e2e"):
        parts = parts[1:]
    parts = [p for p in parts
             if p not in ("src", "main", "java", "resources", "app", "lib",
                          "com", "org", "io", "net", "ts", "js")]
    if len(parts) >= 2:
        return parts[-2] if "." not in parts[-1] else parts[0]
    if parts:
        return parts[0].split(".")[0]
    kind = n.get("kind")
    if kind in ENTRY_KINDS:
        return "endpoints"
    if kind in DATA_KINDS:
        return "data"
    if kind in ("external-service",):
        return "external"
    return "misc"


def _ranked(nodes_by_id, outgoing, cap):
    scored = sorted(
        nodes_by_id.values(),
        key=lambda n: (CONF_RANK.get(n.get("confidence", "UNKNOWN"), 3),
                       _kind_rank(n.get("kind")),
                       -len(outgoing.get(n["id"], [])),
                       n.get("name", "")))
    return [n["id"] for n in scored[:cap]]


def build_view_model(graph, focus=None, impact=None, flow=None,
                     flow_ids=None, flow_complete=True, depth=DEPTH_DEFAULT,
                     extra=None):
    """Build the embeddable view model. Pure function of the graph dict."""
    by_id = {n["id"]: n for n in graph.get("nodes", [])}
    outgoing, incoming = {}, {}
    for e in graph.get("edges", []):
        if G.is_placeholder(e.get("dst", ""), by_id):
            continue  # placeholders stay textual; never graph nodes
        outgoing.setdefault(e["src"], []).append(e)
        incoming.setdefault(e["dst"], []).append(e)

    keep = {nid: n for nid, n in by_id.items()
            if n.get("kind") not in SKIP_NODE_KINDS}
    degree = {nid: len(outgoing.get(nid, [])) + len(incoming.get(nid, []))
              for nid in keep}

    vedges = []
    for e in graph.get("edges", []):
        if (e.get("type") in SKIP_EDGE_TYPES
                or e.get("src") not in keep
                or e.get("dst") not in keep):
            continue
        vedges.append(project_edge(e))
    # deterministic + capped: significant kinds, higher confidence first.
    # Reserve ~15% of the edge budget for LOW/UNKNOWN so confidence
    # filtering always has something to reveal (no silent starvation).
    vedges.sort(key=lambda e: (
        CONF_RANK.get(e["conf"], 3),
        _kind_rank(by_id.get(e["s"], {}).get("kind")),
        e["s"], e["t"]))
    main_cap = int(VIEW_EDGE_CAP * 0.85)
    lows = [e for e in vedges if e["conf"] in ("LOW", "UNKNOWN")]
    highs = [e for e in vedges if e["conf"] not in ("LOW", "UNKNOWN")]
    vedges = highs[:main_cap] + lows[:VIEW_EDGE_CAP - main_cap]
    edge_ids = {(e["s"], e["t"]) for e in vedges}
    touched = set()
    for e in vedges:
        touched.add(e["s"])
        touched.add(e["t"])

    # architecture groups: derived from group_of() over touched nodes.
    # Tiny groups fold into kind buckets so the default view stays an
    # architecture abstraction, not config-key noise.
    _FOLD_BY_KIND = {"configuration-key": "config", "configuration": "config",
                     "environment": "config", "external-service": "external",
                     "endpoint": "endpoints", "ci-job": "ci"}
    groups, gcount = {}, {}
    for nid in touched:
        g = group_of(by_id[nid])
        gcount[g] = gcount.get(g, 0) + 1
    folded = {}
    for nid in touched:
        g = group_of(by_id[nid])
        if gcount[g] < ARCH_GROUP_MIN or not g or g.isdigit():
            g = _FOLD_BY_KIND.get(by_id[nid]["kind"], "misc")
        folded[nid] = g
    fcount = {}
    for nid in touched:
        fcount[folded[nid]] = fcount.get(folded[nid], 0) + 1
    top_groups = sorted(fcount, key=lambda g: (-fcount[g], g))[:ARCH_GROUP_CAP]
    for g in top_groups:
        members = [nid for nid in touched if folded[nid] == g]
        confs = {}
        for nid in members:
            c = by_id[nid].get("confidence", "UNKNOWN")
            confs[c] = confs.get(c, 0) + 1
        groups[g] = {"id": g, "label": g, "count": len(members),
                     "confs": confs, "members": sorted(members)[:NEIGHBOR_CAP]}
    gnodes = [{"id": "g:" + g, "label": g, "kind": "__group__",
               "count": groups[g]["count"], "confs": groups[g]["confs"],
               "members": groups[g]["members"]}
              for g in top_groups]
    gedges = []
    seen = set()
    for e in vedges:
        gs, gt = folded[e["s"]], folded[e["t"]]
        if gs == gt or gs not in groups or gt not in groups:
            continue
        key = (gs, gt, e["type"])
        if key in seen:
            continue
        seen.add(key)
        gedges.append({"s": "g:" + gs, "t": "g:" + gt, "type": e["type"],
                       "conf": e["conf"]})

    modes = {
        "architecture": {"nodes": ["g:" + g for g in top_groups],
                         "edges": [[e["s"], e["t"]] for e in gedges]},
        "dependency": _mode(keep, outgoing, incoming, by_id, DEP_EDGES,
                            LOGIC_KINDS + ENTRY_KINDS + ("external-service",
                                                         "configuration",
                                                         "deployment-unit")),
        "call-graph": _mode(keep, outgoing, incoming, by_id, CALL_EDGES,
                            LOGIC_KINDS + ENTRY_KINDS),
        "data-flow": _mode(keep, outgoing, incoming, by_id, DATA_EDGES,
                           LOGIC_KINDS + DATA_KINDS + ENTRY_KINDS
                           + ("external-service", "database")),
        "api": _mode(keep, outgoing, incoming, by_id, None,
                     ENTRY_KINDS + LOGIC_KINDS, seed_kinds=ENTRY_KINDS),
        "database": _mode(keep, outgoing, incoming, by_id, None,
                          DATA_KINDS + LOGIC_KINDS + ("database",
                                                      "external-service"),
                          seed_kinds=DATA_KINDS + ("database",)),
        "external": _mode(keep, outgoing, incoming, by_id, None,
                          ("external-service",) + LOGIC_KINDS + ENTRY_KINDS,
                          seed_kinds=("external-service",)),
    }

    focus_set = set()
    if focus:
        focus_set = expand_neighborhood(set(focus), outgoing, incoming,
                                        depth, NEIGHBOR_CAP)
    if impact:
        focus_set |= set(impact.get("nodes", []))
    if flow_ids:
        focus_set |= set(flow_ids)
    # Default impact/flow need a home, not a blank page: the top entry
    # points seed the neighborhood expansion (depth-capped, same budget).
    if not focus_set:
        seeds = [nid for nid, n in keep.items()
                 if n.get("kind") in ENTRY_KINDS]
        seeds = _ranked({nid: keep[nid] for nid in seeds}, outgoing,
                        NEIGHBOR_CAP // 4) or _ranked(
            keep, outgoing, NEIGHBOR_CAP // 4)
        focus_set = expand_neighborhood(set(seeds), outgoing, incoming,
                                        DEPTH_DEFAULT, NEIGHBOR_CAP)
    modes["impact"] = ({"nodes": sorted(focus_set), "edges": [
        [e["s"], e["t"]] for e in vedges
        if e["s"] in focus_set and e["t"] in focus_set]}
        if focus_set else {"nodes": [], "edges": []})
    modes["flow"] = modes["impact"]

    # longest-path fallback layers for kinds with no role mapping
    # (deterministic: sorted-order BFS from entry seeds, quantized 0..5).
    _fb = {}
    _seeds = sorted(nid for nid, n in keep.items()
                    if n.get("kind") in ENTRY_KINDS)
    _dist = {nid: 0 for nid in _seeds}
    _front = sorted(_seeds)
    while _front:
        _nxt = []
        for _fid in _front:
            for _e in outgoing.get(_fid, []):
                _d = _e.get("dst")
                if _d in keep and _d not in _dist:
                    _dist[_d] = _dist[_fid] + 1
                    _nxt.append(_d)
        _front = sorted(set(_nxt))
    for nid, n in keep.items():
        if n.get("kind") not in LAYER_OF:
            _fb[nid] = min(5, _dist.get(nid, 6) // 2 if nid in _dist else 3)
    vnodes = [project_node(by_id[nid], degree.get(nid, 0),
                           fallback_layers=_fb) for nid in sorted(touched)]
    search = [{"id": n["id"], "label": n["label"], "kind": n["kind"],
               "lang": n["lang"], "file": n["file"]}
              for n in sorted(vnodes, key=lambda n: n["label"].lower())]
    det = graph.get("detection", {}) or {}
    stack = {sec: [{"name": d.get("name"), "conf": d.get("confidence")}
                   if isinstance(d, dict) else {"name": d, "conf": "UNKNOWN"}
                   for d in det.get(sec, [])]
             for sec in ("languages", "frameworks", "databases",
                         "infrastructure")}
    counts = {}
    for n in graph.get("nodes", []):
        counts[n.get("kind")] = counts.get(n.get("kind"), 0) + 1

    tables = build_tables(graph, by_id, outgoing, incoming, degree,
                          extra=extra)

    return {
        "nodes": vnodes,
        "edges": vedges,
        "groups": gnodes,
        "modes": modes,
        "tables": tables,
        "views": list(VIEWS),
        "layers": list(LAYERS),
        "caps": {"graph_nodes": GRAPH_NODE_CAP,
                 "symbols": SYMBOL_ROW_CAP},
        "search": search,
        "focus": {"impact": impact, "flow": flow,
                  "flow_complete": flow_complete, "depth": depth},
        "meta": {
            "viz_version": VIZ_VERSION,
            "graph_nodes": len(graph.get("nodes", [])),
            "graph_edges": len(graph.get("edges", [])),
            "view_nodes": len(vnodes),
            "view_edges": len(vedges),
            "kinds": sorted(counts.items()),
            "stack": stack,
            "flows_available": len(graph.get("flow_candidates", [])),
            "last_sync": graph.get("last_sync_time"),
            "last_commit": (graph.get("last_sync_commit") or "")[:12],
        },
    }


def _method_path(name):
    """'GET /api/health' -> ('GET', '/api/health'); no verb -> ('', name)."""
    parts = str(name or "").split(None, 1)
    if len(parts) == 2 and parts[0].isupper() and len(parts[0]) <= 7:
        return parts
    return "", str(name or "")


def build_tables(graph, by_id, outgoing, incoming, degree, extra=None):
    """Structured first-class views (spec 4.1). Pure function of the graph
    (+ optional `extra` freshness dict from viz_gen: stale/change files).
    All rows carry id + file/line so the UI can link inspector + editor."""
    extra = extra or {}
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    def row(n):
        meta = n.get("meta") or {}
        return {"id": n["id"], "label": n.get("name") or n["id"],
                "kind": n.get("kind"), "lang": meta.get("lang"),
                "file": n.get("file"), "line": n.get("line"),
                "conf": n.get("confidence", "UNKNOWN"),
                "deg": degree.get(n["id"], 0)}

    # ---- endpoints: method/path/handler/file:line/confidence/consumers
    # + downstream reach (bounded BFS over outgoing edges)
    endpoints = []
    for n in nodes:
        if n.get("kind") not in ENTRY_KINDS:
            continue
        meta = n.get("meta") or {}
        method, path = _method_path(n.get("name"))
        handler = meta.get("handler")
        for e in outgoing.get(n["id"], []):
            if e.get("type") in ("handled-by", "exposes") \
                    and e.get("dst") in by_id:
                handler = handler or e["dst"]
                break
        consumers = sorted({e["src"] for e in incoming.get(n["id"], [])
                            if e.get("type") == "consumes"})
        endpoints.append({"id": n["id"], "method": method, "path": path,
                          "handler": handler,
                          "file": n.get("file"), "line": n.get("line"),
                          "conf": n.get("confidence", "UNKNOWN"),
                          "consumers": consumers[:10],
                          "n_consumers": len(consumers),
                          "reach": _downstream_reach(
                              n["id"], outgoing, by_id, limit=400)})

    # ---- data: tables + readers/writers + migrations
    # Columns: the graph carries no column/field nodes, so every store row
    # states that explicitly rather than showing a misleading empty column.
    data_kinds = ("table", "view", "collection", "entity", "database")
    tables, access = [], []
    for n in nodes:
        if n.get("kind") in data_kinds:
            r = row(n)
            r["columns"] = sorted(
                (n.get("meta") or {}).get("columns") or [])
            tables.append(r)
    migrations = []
    for n in nodes:
        if n.get("kind") == "migration":
            for e in outgoing.get(n["id"], []):
                if e.get("dst") in by_id and by_id[e["dst"]].get("kind") \
                        in data_kinds:
                    migrations.append(
                        {"migration": n.get("name") or n["id"],
                         "migration_id": n["id"],
                         "table": e["dst"],
                         "file": n.get("file"), "line": n.get("line"),
                         "conf": n.get("confidence", "UNKNOWN")})
    migrations = sorted(migrations,
                        key=lambda m: (m["table"], m["migration"]))
    for e in edges:
        if e.get("type") in ("reads", "writes", "queries", "creates",
                              "modifies", "seeds", "transforms"):
            other = e["src"] if e["dst"] in {t["id"] for t in tables} \
                else None
            access.append({"table": e["dst"], "op": e["type"],
                           "actor": e["src"], "file": e.get("file"),
                           "line": e.get("line"),
                           "conf": e.get("confidence", "UNKNOWN")})
    # keep access rows whose target is a known store (deterministic order)
    store_ids = {t["id"] for t in tables}
    access = sorted([a for a in access if a["table"] in store_ids],
                    key=lambda a: (a["table"], a["op"], a["actor"]))

    # ---- dependencies: external grouped by manifest (edge/config file).
    # Version comes only from node meta (never parsed here); importing
    # files come from the depends-on/imports edges into each service.
    dependencies = []
    for n in nodes:
        if n.get("kind") != "external-service":
            continue
        meta = n.get("meta") or {}
        dep_edges = [e for e in incoming.get(n["id"], [])
                     if e.get("type") in ("depends-on", "imports")]
        manifests = sorted({e.get("file") or "" for e in dep_edges})
        importing = sorted({e["src"] for e in dep_edges})
        dependencies.append({"id": n["id"], "label": n.get("name"),
                             "manifest": manifests[0] if manifests else
                             (n.get("file") or ""),
                             "manifests": manifests,
                             "version": meta.get("version"),
                             "importing": importing[:15],
                             "n_importing": len(importing),
                             "file": n.get("file"), "line": n.get("line"),
                             "conf": n.get("confidence", "UNKNOWN")})
    dependencies.sort(key=lambda d: (d["manifest"] or "", d["label"] or ""))

    # ---- symbols: every node, capped (banner states N of M)
    sym_rows = sorted((row(n) for n in nodes),
                      key=lambda r: (r["label"] or "").lower())
    symbols = {"total": len(sym_rows),
               "rows": sym_rows[:SYMBOL_ROW_CAP]}

    # ---- flows: curated business-flows/ first, then tool candidates.
    # Curated files are read-only VIEW input via extra["curated_flows"]
    # (viz_gen reads business-flows/*.md); without them the section is
    # candidates only and says so (never presented as curated).
    flows = []
    curated = extra.get("curated_flows", []) or []
    for f in curated:
        steps = []
        for sid in f.get("chain", []) or []:
            n = by_id.get(sid)
            steps.append({"id": sid, "label": n.get("name") if n else sid,
                          "kind": n.get("kind") if n else None,
                          "file": n.get("file") if n else None,
                          "line": n.get("line") if n else None,
                          "missing": n is None})
        flows.append({"seed": f.get("seed"), "kind": f.get("kind"),
                      "curated": True, "source": f.get("source"),
                      "steps": steps, "complete": all(not s["missing"]
                                                     for s in steps)})
    for f in graph.get("flow_candidates", []) or []:
        steps = []
        for sid in f.get("chain", []) or []:
            n = by_id.get(sid)
            steps.append({"id": sid, "label": n.get("name") if n else sid,
                          "kind": n.get("kind") if n else None,
                          "file": n.get("file") if n else None,
                          "line": n.get("line") if n else None,
                          "missing": n is None})
        flows.append({"seed": f.get("seed"), "kind": f.get("kind"),
                      "curated": False,
                      "steps": steps, "complete": all(not s["missing"]
                                                     for s in steps)})

    # ---- issues: validate findings + LOW items + unresolved + stale
    issues = []
    ids = {n["id"] for n in nodes}
    seen, dupes = set(), set()
    for n in nodes:
        if n["id"] in seen:
            dupes.add(n["id"])
        seen.add(n["id"])
    for d in sorted(dupes):
        issues.append({"severity": "HIGH", "category": "validation",
                       "message": "duplicate node id", "id": d})
    for e in edges:
        if e["src"] not in ids:
            issues.append({"severity": "HIGH", "category": "validation",
                           "message": "edge with unknown src "
                           + str(e.get("type")), "id": e["src"],
                           "file": e.get("file"), "line": e.get("line")})
        elif not G.is_placeholder(e.get("dst", ""), ids):
            pass
        if e["dst"] not in ids and not G.is_placeholder(
                e.get("dst", ""), ids):
            issues.append({"severity": "HIGH", "category": "validation",
                           "message": "edge with unknown dst "
                           + str(e.get("type")), "id": e["dst"],
                           "file": e.get("file"), "line": e.get("line")})
    for n in nodes:
        if not n.get("file") or n.get("confidence") == "UNKNOWN":
            issues.append({"severity": "MEDIUM", "category": "validation",
                           "message": "node without evidence", "id": n["id"],
                           "file": n.get("file"), "line": n.get("line")})
    for nid in sorted(graph.get("unresolved", []) or []):
        issues.append({"severity": "LOW", "category": "unresolved",
                       "message": "unresolved reference", "id": nid})
    for n in nodes:
        if n.get("confidence") == "LOW":
            issues.append({"severity": "LOW", "category": "low-confidence",
                           "message": "LOW-confidence node (lead, "
                           "not confirmed)", "id": n["id"],
                           "file": n.get("file"), "line": n.get("line")})
    for e in edges:
        if e.get("confidence") == "LOW":
            issues.append({"severity": "LOW", "category": "low-confidence",
                           "message": "LOW-confidence edge "
                           + str(e.get("type")), "id": e["src"],
                           "file": e.get("file"), "line": e.get("line")})
    for f in extra.get("stale", []):
        issues.append({"severity": "MEDIUM", "category": "stale",
                       "message": "file changed since last sync",
                       "file": f})
    sev_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    issues.sort(key=lambda i: (sev_rank.get(i["severity"], 3),
                               i["category"], i.get("id") or "",
                               i.get("file") or ""))

    # ---- overview: stack, counts, freshness, validation, entry points
    counts = {}
    for n in nodes:
        counts[n.get("kind")] = counts.get(n.get("kind"), 0) + 1
    entry_points = [{"id": n["id"], "label": n.get("name"),
                     "kind": n.get("kind"), "file": n.get("file"),
                     "line": n.get("line")}
                    for n in nodes if n.get("kind") in ENTRY_KINDS][:40]
    low_items = sum(1 for n in nodes if n.get("confidence") == "LOW") \
        + sum(1 for e in edges if e.get("confidence") == "LOW")
    overview = {
        "counts": sorted(counts.items()),
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "freshness": {"last_sync": graph.get("last_sync_time"),
                      "last_commit": (graph.get("last_sync_commit") or "")[:12],
                      "stale": extra.get("stale", [])[:15]},
        "validation": {
            "unresolved": len(graph.get("unresolved", []) or []),
            "low_items": low_items,
            "dupes": len(dupes),
            "no_evidence": sum(1 for n in nodes
                               if not n.get("file")
                               or n.get("confidence") == "UNKNOWN"),
        },
        "entry_points": entry_points,
        "recent": extra.get("changes", [])[:15],
    }
    return {"overview": overview, "endpoints": endpoints, "data": {
        "tables": tables, "access": access, "migrations": migrations},
        "dependencies": dependencies, "symbols": symbols, "flows": flows,
        "capabilities": _capabilities_table(nodes, edges, by_id),
        "issues": issues}


def _capabilities_table(nodes, edges, by_id):
    """Intent-layer tree: capability -> requirements -> realizing code.

    Pure projection over ASSERTED intent nodes/edges (Wave 4a contract):
    ids `intent:<kind>:<slug>`, provenance "asserted", confidence null.
    Per-requirement status: realized (has realizing code) / unbound /
    stale (a binding edge carries review=needs-review). Empty list when
    no intent was imported — the UI renders the honest empty state.
    Every row carries file/line for inspector + editor links."""
    intent_kinds = {"capability", "requirement", "concept", "slice",
                    "decision", "non-goal"}
    inodes = {n["id"]: n for n in nodes
              if n.get("kind") in intent_kinds
              or str(n.get("id", "")).startswith("intent:")}
    if not inodes:
        return []
    realized_by = {}   # req/cap id -> [code node ids]
    code_why = {}      # code id -> [(intent id, why text)]
    stale_ids = set()
    req_of_cap = {}    # cap id -> [req ids]
    for e in edges:
        t = e.get("type")
        if t == "realizes" and e.get("dst") in inodes:
            realized_by.setdefault(e["dst"], []).append(e["src"])
            meta = e.get("meta") or {}
            code_why.setdefault(e["src"], []).append(
                (e["dst"], meta.get("why", "")))
            if meta.get("review") == "needs-review":
                stale_ids.add(e["dst"])
                stale_ids.add(e["src"])
        elif t == "part-of" and e.get("src") in inodes \
                and e.get("dst") in inodes:
            src_k = inodes[e["src"]].get("kind")
            if src_k == "requirement":
                req_of_cap.setdefault(e["dst"], []).append(e["src"])
    caps = []
    for nid, n in inodes.items():
        if n.get("kind") != "capability":
            continue
        reqs = []
        for rid in sorted(req_of_cap.get(nid, [])):
            r = inodes.get(rid, {})
            rmeta = r.get("meta") or {}
            code = sorted(set(realized_by.get(rid, [])))
            stale = rid in stale_ids
            status = "stale" if stale else (
                "realized" if code else "unbound")
            code_rows = []
            for c in code:
                bn = by_id.get(c) or {}
                why = ""
                for iid, w in code_why.get(c, []):
                    if iid == rid:
                        why = w
                        break
                code_rows.append({"id": c, "label": bn.get("name", c),
                                  "kind": bn.get("kind"),
                                  "file": bn.get("file"),
                                  "line": bn.get("line"), "why": why})
            reqs.append({
                "id": rid, "title": r.get("name") or rid,
                "status": status,
                "asserted_by": rmeta.get("author"),
                "asserted_at": rmeta.get("asserted_at"),
                "source": rmeta.get("source"),
                "code": code_rows,
            })
        caps.append({
            "id": nid, "title": n.get("name") or nid,
            "status": (n.get("meta") or {}).get("status", "active"),
            "requirements": reqs,
        })
    caps.sort(key=lambda c: c["id"])
    return caps


def _downstream_reach(nid, outgoing, by_id, limit=400):
    """Count of nodes reachable downstream (bounded BFS, deterministic)."""
    seen = {nid}
    frontier = [nid]
    while frontier and len(seen) < limit:
        nxt = []
        for fid in sorted(frontier):
            for e in outgoing.get(fid, []):
                dst = e.get("dst")
                if dst in by_id and dst not in seen:
                    seen.add(dst)
                    nxt.append(dst)
                    if len(seen) >= limit:
                        break
            if len(seen) >= limit:
                break
        frontier = nxt
    return len(seen) - 1


def longest_path_layer(nid, outgoing, layer_of_fn, memo, visiting=None):
    """Fallback layer for kinds with no architectural mapping: longest-path
    depth from entry-point seeds, quantized onto the LAYERS axis. Pure +
    deterministic (iteration in sorted id order; cycles clamp at depth 6).
    ( viz_gen / tests can call this for unmappable kinds. )"""
    if nid in memo:
        return memo[nid]
    visiting = visiting or set()
    if nid in visiting:
        return 3  # cycle: middle layer, deterministic
    visiting.add(nid)
    best = 0
    for e in sorted(outgoing.get(nid, []), key=lambda e: e.get("dst", "")):
        dst = e.get("dst")
        if dst is None:
            continue
        best = max(best, 1 + longest_path_layer(
            dst, outgoing, layer_of_fn, memo, visiting))
    visiting.discard(nid)
    layer = min(5, max(0, best // 2)) if best else 3
    memo[nid] = layer
    return layer


def _mode(keep, outgoing, incoming, by_id, edge_types, kinds,
          seed_kinds=None):
    seeds = [nid for nid, n in keep.items()
             if n.get("kind") in (seed_kinds or kinds)]
    seeds = _ranked({nid: keep[nid] for nid in seeds}, outgoing,
                    VIEW_NODE_CAP // 2)
    if not seeds:
        seeds = _ranked(keep, outgoing, VIEW_NODE_CAP // 2)
    want = set(seeds)
    for nid in seeds:
        for e in outgoing.get(nid, [])[:8] + incoming.get(nid, [])[:8]:
            if edge_types and e.get("type") not in edge_types:
                continue
            if e["src"] in keep and e["dst"] in keep:
                want.add(e["src"])
                want.add(e["dst"])
            if len(want) >= VIEW_NODE_CAP:
                break
    want = set(_ranked({nid: keep[nid] for nid in want}, outgoing,
                       VIEW_NODE_CAP))
    edges = []
    for nid in want:
        for e in outgoing.get(nid, []):
            if e["dst"] in want and (not edge_types
                                     or e.get("type") in edge_types):
                edges.append([e["src"], e["dst"]])
            if len(edges) >= VIEW_EDGE_CAP:
                break
    return {"nodes": sorted(want), "edges": edges}


def expand_neighborhood(seeds, outgoing, incoming, depth, cap):
    seen, frontier = set(seeds), set(seeds)
    for _ in range(max(depth, 1)):
        nxt = set()
        for fid in frontier:
            for e in outgoing.get(fid, [])[:25] + incoming.get(fid, [])[:25]:
                for nid in (e["src"], e["dst"]):
                    if nid not in seen:
                        seen.add(nid)
                        nxt.add(nid)
        if not nxt or len(seen) >= cap:
            break
        frontier = nxt
    return set(sorted(seen)[:cap])


def viz_dir(root, map_dir):
    return os.path.join(os.path.abspath(root), map_dir, "visualization")
