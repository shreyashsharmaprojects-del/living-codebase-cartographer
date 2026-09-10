"""Generate the visualization artifact (VIEW builder, never analysis).

Reads graph.json read-only, projects it via viz.py, and writes:
    <map-dir>/visualization/index.html   (self-contained app shell)
    <map-dir>/visualization/data/graph.js (JSONP data: window.__CARTO_GRAPH__)
    <map-dir>/visualization/data/boot.js  (window.__CARTO_BOOT__: mode/focus)

index.html loads data/ relatively, so the whole directory is portable and
works over file:// (no fetch/CORS) and offline. No network, no telemetry.

graph.json is NEVER modified here: the only writes are under visualization/.
"""

import json
import os
import shutil
import webbrowser

from analyzers import graph as G

try:
    from . import viz as V
    from . import core as C
except ImportError:  # script executed as top-level module
    import viz as V
    import core as C

TEMPLATE_DIR = os.path.dirname(os.path.abspath(__file__))


def _read(name):
    with open(os.path.join(TEMPLATE_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def resolve_focus(graph, symbol=None, impact=None, flow=None, depth=2):
    """Focus node ids + summaries, reusing core's impact/flow engine.

    Returns (focus_ids, impact_summary, flow_summary, flow_complete).
    Unknown symbols raise LookupError; incomplete flows degrade to the
    evidenced segments with flow_complete=False (never invented links).
    """
    target = impact or symbol
    focus_ids, impact_summary = set(), None
    if target:
        results = C.impact_analysis(graph, target)
        # Prefer the richest match (file nodes carry no edges); the CLI
        # prints all hits, the visualization focuses the most connected.
        by_id = {n["id"]: n for n in graph["nodes"]}
        _, _inc, _out = C.graph_indexes(graph)
        r = max(results, key=lambda x: len(_inc.get(x["target"]["id"], []))
                + len(_out.get(x["target"]["id"], [])))
        by_id = {n["id"]: n for n in graph["nodes"]}
        _, incoming, outgoing = C.graph_indexes(graph)
        seen = {r["target"]["id"]}
        frontier = {r["target"]["id"]}
        for _ in range(max(depth, 1)):
            nxt = set()
            for fid in frontier:
                for e in incoming.get(fid, [])[:25] + \
                        outgoing.get(fid, [])[:25]:
                    for nid in (e["src"], e["dst"]):
                        if nid not in seen and nid in by_id:
                            seen.add(nid)
                            nxt.add(nid)
            frontier = nxt
            if not frontier or len(seen) >= V.NEIGHBOR_CAP:
                break
        focus_ids |= seen
        trans = max(len(seen) - 1 - len(r["callers"]) - len(r["callees"]), 0)
        impact_summary = {
            "target": r["target"]["name"],
            "target_id": r["target"]["id"],
            "callers": len(r["callers"]),
            "callees": len(r["callees"]),
            "transitive": trans,
            "entrypoints": r["entry_points"][:10],
            "node": r["target"]["name"],
        }
    flow_summary, flow_complete = None, True
    if flow:
        parts = [p.strip() for p in flow.split("->")]
        to_text = parts[1] if len(parts) > 1 else None
        from_text = parts[0]
        chain_ids, complete = [], True
        try:
            if to_text:
                results = C.flow_path(graph, from_text, to_text)
                r = next((x for x in results if x["found"]), None)
                if r is None:
                    complete = False
                else:
                    chain_ids = [n["id"] for n in r["path"]]
                    focus_ids |= set(chain_ids)
            else:
                # single seed: endpoint/route name -> handled-by neighborhood
                seeds = C.find_nodes(graph, from_text)
                entry = next((n for n in seeds
                              if n["kind"] in C.ENTRY_KINDS), None)
                seed = entry or (seeds[0] if seeds else None)
                if seed is None:
                    raise LookupError(from_text)
                _, incoming, outgoing = C.graph_indexes(graph)
                chain_ids = [seed["id"]]
                for e in outgoing.get(seed["id"], []):
                    if e["type"] in ("handled-by", "exposes", "consumes"):
                        chain_ids.append(e["dst"])
                focus_ids |= V.expand_neighborhood(
                    set(chain_ids), outgoing, incoming, depth,
                    V.NEIGHBOR_CAP)
        except LookupError:
            complete = False
            chain_ids = []
        flow_summary = {"from": from_text, "to": to_text,
                        "chain": chain_ids}
        flow_complete = complete and bool(chain_ids)
    return focus_ids, impact_summary, flow_summary, flow_complete


def generate(root, map_dir=C.DEFAULT_MAP_DIR, mode="architecture",
             symbol=None, impact=None, flow=None, depth=2,
             allow_stale=False, open_browser=False, serve=False,
             serve_port=8734):
    root = os.path.abspath(root)
    paths = C.map_paths(root, map_dir)
    if not os.path.exists(paths["graph"]):
        print("STATUS: NO_MAP — run `init` first.")
        return 2
    try:
        graph = C.load_graph(paths)
    except (OSError, ValueError) as exc:
        print(f"STATUS: CORRUPT — graph.json unreadable ({exc}); "
              "run `init --full` to rebuild.")
        return 2
    if graph.get("version") != C.VERSION:
        print(f"STATUS: STALE_SCHEMA — graph.json version "
              f"{graph.get('version')} != tool version {C.VERSION}; "
              "run `init --full` to rebuild.")
        return 2
    fresh = C.map_freshness(root, map_dir)
    if fresh["stale"] and not allow_stale:
        print(f"Codebase map is stale ({len(fresh['changed'])} changed "
              f"files). Run `sync` before visualizing, or pass "
              f"`--allow-stale`.")
        for c in fresh["changed"][:10]:
            print(f"  M `{c}`")
        return 1
    if symbol and not C.find_nodes(graph, symbol):
        print(f"No nodes match '{symbol}'.")
        return 1

    try:
        focus_ids, impact_sum, flow_sum, flow_ok = resolve_focus(
            graph, symbol=symbol, impact=impact, flow=flow, depth=depth)
    except LookupError as exc:
        print(exc)
        return 1

    # Freshness/change context for the overview + issues tables: the last
    # few architectural change records (agent-curated, newest first).
    changes = []
    try:
        cdir = os.path.join(paths["dir"], "changes")
        names = sorted(os.listdir(cdir), reverse=True)[:8]
        for nm in names:
            if not nm.endswith(".md"):
                continue
            ap = os.path.join(cdir, nm)
            with open(ap, encoding="utf-8") as fh:
                lines = [ln.strip() for ln in fh.readlines()[:8]]
            title = next((ln.lstrip("# ").strip() for ln in lines
                          if ln.startswith("#")), nm)
            changes.append({"file": nm, "title": title[:120]})
    except OSError:
        changes = []
    # Curated business-flows/ (read-only VIEW input, never a second source
    # of truth): <name>.md files with `node: <id>` evidence links become
    # curated flows shown first in the Flows view. _candidates.md skipped.
    curated_flows = []
    try:
        bfdir = os.path.join(paths["dir"], "business-flows")
        for nm in sorted(os.listdir(bfdir)):
            if not nm.endswith(".md") or nm == "_candidates.md":
                continue
            ap = os.path.join(bfdir, nm)
            with open(ap, encoding="utf-8") as fh:
                text = fh.read()
            chain = []
            for ln in text.splitlines():
                ln = ln.strip()
                if ln.lower().startswith("node:"):
                    cid = ln.split(":", 1)[1].strip().strip("`\"' ")
                    if cid:
                        chain.append(cid)
            if chain:
                curated_flows.append({"seed": nm[:-3], "kind": "curated",
                                      "source": "business-flows/" + nm,
                                      "chain": chain})
    except OSError:
        curated_flows = []
    extra = {"stale": fresh["changed"][:15] if fresh.get("stale") else [],
             "changes": changes, "curated_flows": curated_flows}
    view = V.build_view_model(
        graph,
        focus=(sorted(focus_ids) if focus_ids else None),
        impact=impact_sum, flow=flow_sum, flow_ids=None,
        flow_complete=flow_ok, depth=depth, extra=extra)
    if impact_sum or flow_sum:
        # focus modes render the focus set directly (already capped)
        view["modes"]["impact" if impact_sum else "flow"] = {
            "nodes": sorted(focus_ids),
            "edges": [[e["s"], e["t"]] for e in view["edges"]
                      if e["s"] in focus_ids and e["t"] in focus_ids],
        }

    outdir = os.path.join(paths["dir"], "visualization")
    datadir = os.path.join(outdir, "data")
    stale_paths = [os.path.join(outdir, "assets"),
                   os.path.join(outdir, "graph.json")]
    for p in stale_paths:
        if os.path.exists(p):
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
    os.makedirs(datadir, exist_ok=True)

    with open(os.path.join(datadir, "graph.js"), "w",
              encoding="utf-8") as fh:
        fh.write("window.__CARTO_GRAPH__ = ")
        json.dump(view, fh, separators=(",", ":"), sort_keys=True)
        fh.write(";")
    boot = {
        "mode": ("impact" if impact_sum else
                 "flow" if flow_sum else
                 (mode if mode in V.MODES else "architecture")),
        "view": None,  # resolved client-side from ?view= (default overview)
        "viz_version": V.VIZ_VERSION,
        "depth": depth,
        # absolute repo root so editor links (vscode://file/...) open the
        # real file; graph file paths are repo-relative and would resolve
        # to filesystem root ("path is wrong").
        "root": root,
        "title": "Cartographer — " + (
            impact_sum["target"] if impact_sum else
            (flow_sum["from"] if flow_sum else "architecture")),
        # stale=[] really means fresh: only list files when actually stale
        # (an empty banner saying "stale (0 changed files)" destroys trust).
        "stale": fresh["changed"][:15] if fresh.get("stale") else [],
        "focus": {"impact": impact_sum,
                  "impactId": impact,
                  "node": symbol,
                  "flow": flow_sum,
                  "flow_complete": flow_ok},
    }
    with open(os.path.join(datadir, "boot.js"), "w",
              encoding="utf-8") as fh:
        fh.write("window.__CARTO_BOOT__ = ")
        json.dump(boot, fh, separators=(",", ":"), sort_keys=True)
        fh.write(";")

    shell = _read("viz_template_head.html")
    p2 = _read("viz_template_p2.js")
    p3 = _read("viz_template_p3.js")
    shell = shell.replace("__TITLE__", "Cartographer — " + (
        impact_sum["target"] if impact_sum else
        (flow_sum["from"] if flow_sum else "architecture")))
    head_tail = shell[shell.find(
        "<script>\n\"use strict\";\n/* ==================="):]
    assert "const DATA = window.__CARTO_GRAPH__" in head_tail, \
        "head const block changed"
    head_tail = head_tail[:head_tail.rfind("</script>")]
    opener = "<script>\n\"use strict\";\n"
    assert head_tail.startswith(opener), "head script opener changed"
    head_tail = head_tail[len(opener):]
    app = ("<script src=\"data/graph.js\"></script>\n"
           "<script src=\"data/boot.js\"></script>\n"
           "<script>\n" + head_tail + "\n"
           + p2 + "\n" + p3 + "\n</script>\n</body>\n</html>")
    # Seam: the stub placeholder block. Cut from the stub's opening
    # <script> through </html> and emit loaders + app (p2/p3 declare
    # DATA/BOOT consts themselves — no duplication).
    marker = "/* __GRAPH_DATA__ */"
    assert marker in shell, "template stub changed"
    shell = shell[:shell.find("<script>\n\"use strict\";\n" + marker)] \
        + app
    with open(os.path.join(outdir, "index.html"), "w",
              encoding="utf-8") as fh:
        fh.write(shell)

    index = os.path.join(outdir, "index.html")
    print("Visualization generated:")
    print(index)
    print(f"  view: {view['meta']['view_nodes']} nodes / "
          f"{view['meta']['view_edges']} edges "
          f"(graph: {view['meta']['graph_nodes']} / "
          f"{view['meta']['graph_edges']}); "
          f"modes: {', '.join(V.MODES)}")
    if fresh["stale"]:
        print(f"  note: map stale ({len(fresh['changed'])} files) — "
              f"generated with --allow-stale.")
    if serve:
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
        import functools

        class _H(SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

        handler = functools.partial(_H, directory=outdir)
        srv = ThreadingHTTPServer(("127.0.0.1", serve_port), handler)
        url = f"http://127.0.0.1:{serve_port}/index.html"
        print(f"Serving visualization at {url} (Ctrl-C to stop)")
        if open_browser:
            webbrowser.open(url)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    if open_browser:
        webbrowser.open("file://" + index)
    return 0
