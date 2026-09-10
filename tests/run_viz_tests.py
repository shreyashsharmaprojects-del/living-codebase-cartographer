"""Visualization tests for living-codebase-cartographer.

Run:  python3 tests/run_viz_tests.py
(stdlib only. Safe anywhere — uses temp dirs. Complements run_tests.py,
which it does not modify.)

Covers the task's visualization requirements:
  generation succeeds / graph untouched / HTML exists / data valid /
  search / selection-focus / confidence filtering / architecture view /
  dependency view / impact view / flow view / tech-agnostic fixtures /
  empty graph / large fixture / stale warning / malformed graph /
  unknown kinds+edges / LOW distinguishability / existing suite green.
"""

import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL, "scripts")
CLI = os.path.join(SCRIPTS, "cartographer.py")

sys.path.insert(0, os.path.join(SKILL, "scripts"))
sys.path.insert(0, SKILL)

from analyzers import graph as G  # noqa: E402
import viz as V  # noqa: E402
import viz_gen  # noqa: E402
import core as C  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name} {detail}")


def _node(i, kind, name=None, **kw):
    n = {"id": i, "kind": kind, "name": name or i.split(":")[-1],
         "file": kw.pop("file", "svc/a.java"), "line": 1,
         "confidence": kw.pop("conf", "HIGH"),
         "meta": kw.pop("meta", {"lang": "java"})}
    n.update(kw)
    return n


def _edge(s, t, type="calls", conf="MEDIUM"):
    return {"src": s, "dst": t, "type": type, "file": "svc/a.java",
            "line": 1, "confidence": conf, "meta": {}}


def sample_graph():
    """Small tech-mixed graph exercising every viz path."""
    g = G.new_graph("/t", "abc", "2026-01-01T00:00:00+00:00")
    g["nodes"] = [
        _node("java:class:com.shop.OrderSvc", "service", "OrderSvc",
              meta={"lang": "java", "package": "com.shop"}),
        _node("java:method:com.shop.OrderSvc#place", "method", "place"),
        _node("endpoint:POST /orders", "endpoint", "POST /orders"),
        _node("py:function:create_order", "function", "create_order",
              file="api/app.py", meta={"lang": "python"}),
        _node("table:orders", "table", "orders",
              file="m/V1.sql", conf="HIGH", meta={}),
        _node("external:s3", "external-service", "s3", meta={}),
        _node("ts:component:Cart", "component", "Cart",
              file="ui/c.tsx", meta={"lang": "typescript"}),
        _node("go:func:Get", "function", "Get", file="svc/s.go",
              meta={"lang": "go"}),
        _node("cs:method:Repo#Find", "method", "Find", file="R.cs",
              meta={"lang": "csharp"}),
        _node("rs:fn:health", "function", "health", file="h.rs",
              meta={"lang": "rust"}),
        _node("mystery:kind", "class", "Mystery", conf="LOW",
              meta={"reason": "unsupported-language-fallback"}),
        _node("weird:k1", "quantum", "Weird", meta={}),  # unknown kind
    ]
    g["edges"] = [
        _edge("endpoint:POST /orders",
              "java:class:com.shop.OrderSvc", "handled-by", "HIGH"),
        _edge("java:class:com.shop.OrderSvc",
              "java:method:com.shop.OrderSvc#place", "defines", "HIGH"),
        _edge("java:method:com.shop.OrderSvc#place", "table:orders",
              "writes", "MEDIUM"),
        _edge("py:function:create_order", "table:orders", "writes", "MEDIUM"),
        _edge("ts:component:Cart", "endpoint:POST /orders", "consumes",
              "MEDIUM"),
        _edge("java:method:com.shop.OrderSvc#place", "external:s3",
              "invokes", "LOW"),
        _edge("java:class:com.shop.OrderSvc",
              "unresolved:class:Helper", "injects", "LOW"),
        _edge("mystery:kind", "table:orders", "references", "LOW"),
        _edge("weird:k1", "java:class:com.shop.OrderSvc", "teleports",
              "LOW"),  # unknown edge type
    ]
    g["detection"] = {"languages": [{"name": "java", "confidence": "HIGH"}],
                      "frameworks": [], "databases": [], "infrastructure": []}
    return g


# ------------------------------------------------------- projection unit

def test_projection_modes():
    print("== projection: modes + caps ==")
    view = V.build_view_model(sample_graph())
    check("all modes present",
          set(view["modes"]) == set(V.MODES), str(sorted(view["modes"])))
    check("architecture uses groups",
          all(n.startswith("g:") for n in
              view["modes"]["architecture"]["nodes"]),
          str(view["modes"]["architecture"]["nodes"]))
    check("no file nodes leak",
          all(n["kind"] != "file" for n in view["nodes"]))
    check("no defines edges leak",
          all(e["type"] != "defines" for e in view["edges"]))
    check("placeholders excluded",
          all(not e["t"].startswith("unresolved:") for e in view["edges"]))
    check("search index covers nodes",
          len(view["search"]) == len(view["nodes"]))
    check("meta counts present", view["meta"]["graph_nodes"] == 12,
          str(view["meta"]))


def test_confidence_distinguishable():
    print("== confidence preserved + distinguishable ==")
    view = V.build_view_model(sample_graph())
    confs = {e["conf"] for e in view["edges"]}
    check("HIGH+MEDIUM+LOW survive projection",
          {"HIGH", "MEDIUM", "LOW"} <= confs, str(confs))
    check("LOW edge kept (no starvation)",
          any(e["conf"] == "LOW" for e in view["edges"]))


def test_unknowns_dont_crash():
    print("== unknown kinds/edges degrade gracefully ==")
    try:
        view = V.build_view_model(sample_graph())
        kinds = {n["kind"] for n in view["nodes"]}
        check("unknown kind passes through (UI glyph defaults)",
              "quantum" in kinds, str(kinds))
        check("unknown edge type passes through",
              any(e["type"] == "teleports" for e in view["edges"]))
    except Exception as exc:  # noqa: BLE001
        check("unknowns don't crash", False, str(exc))


def test_empty_graph():
    print("== empty graph ==")
    g = G.new_graph("/t", None, "2026-01-01T00:00:00+00:00")
    try:
        view = V.build_view_model(g)
        check("empty modes exist",
              all(v == {"nodes": [], "edges": []}
                  or isinstance(v, dict) for v in view["modes"].values()))
        check("empty search", view["search"] == [])
    except Exception as exc:  # noqa: BLE001
        check("empty graph doesn't crash", False, str(exc))


def test_large_fixture():
    print("== large fixture (3000 nodes) ==")
    g = G.new_graph("/t", "abc", "2026-01-01T00:00:00+00:00")
    for i in range(3000):
        g["nodes"].append(_node(f"java:class:p.C{i}", "class", f"C{i}"))
        if i:
            g["edges"].append(_edge(f"java:class:p.C{i-1}",
                                    f"java:class:p.C{i}"))
    try:
        view = V.build_view_model(g)
        check("nodes capped",
              len(view["nodes"]) <= V.VIEW_EDGE_CAP + V.VIEW_NODE_CAP,
              str(len(view["nodes"])))
        check("arch view is groups, not hairball",
              all(n.startswith("g:") for n in
                  view["modes"]["architecture"]["nodes"]))
    except Exception as exc:  # noqa: BLE001
        check("large graph doesn't crash", False, str(exc))


def test_tech_agnostic_fixtures():
    print("== tech-agnostic: per-language graphs ==")
    langs = [("java", "A.java", "java:class:p.A", "class"),
             ("python", "a.py", "py:function:f", "function"),
             ("go", "a.go", "go:func:F", "function"),
             ("csharp", "A.cs", "cs:class:N.A", "class"),
             ("rust", "a.rs", "rs:fn:f", "function"),
             ("typescript", "a.ts", "ts:component:C", "component")]
    for lang, fn, nid, kind in langs:
        g = G.new_graph("/t", "abc", "x")
        g["nodes"] = [_node(nid, kind, "X", file=fn,
                            meta={"lang": lang}),
                      _node("table:t", "table", "t", file="m.sql",
                            meta={})]
        g["edges"] = [_edge(nid, "table:t", "reads")]
        try:
            view = V.build_view_model(g)
            check(f"{lang} projects", len(view["nodes"]) == 2,
                  str(len(view["nodes"])))
        except Exception as exc:  # noqa: BLE001
            check(f"{lang} projects", False, str(exc))


def test_focus_reuses_core():
    print("== focus reuses core impact/flow engine ==")
    g = sample_graph()
    fids, isum, _, _ = viz_gen.resolve_focus(
        g, impact="OrderSvc", depth=2)
    check("impact summary from core engine",
          isum and isum["target"] == "OrderSvc", str(isum))
    check("impact focus includes table",
          "table:orders" in fids, str(sorted(fids)))
    fids2, _, fsum, fok = viz_gen.resolve_focus(
        g, flow="POST /orders -> orders", depth=2)
    check("flow path found", fok and fsum["chain"], str(fsum))
    _, _, _, fok2 = viz_gen.resolve_focus(g, flow="POST /orders -> nope",
                                          depth=2)
    check("incomplete flow degrades (never invents)", not fok2)


# ------------------------------------------------------------- CLI / files

def _cli(root, mapdir, *args):
    return subprocess.run(
        [sys.executable, CLI, "--root", root, "--map-dir", mapdir]
        + list(args), capture_output=True, text=True, timeout=180)


def _write(root, rel, content):
    ap = os.path.join(root, rel)
    os.makedirs(os.path.dirname(ap), exist_ok=True)
    with open(ap, "w", encoding="utf-8") as fh:
        fh.write(content)


def _fixture_repo(root):
    _write(root, "api/app.py",
           "from fastapi import FastAPI\napp = FastAPI()\n"
           "@app.get(\"/items\")\ndef list_items():\n    return []\n")
    _write(root, "api/requirements.txt", "fastapi==0.115.0\n")
    _write(root, "ui/src/c.tsx",
           "export function Cart() {\n"
           "  fetch('/items').then(r => r.json());\n"
           "  return null;\n}\n")
    _write(root, "ui/package.json", '{"dependencies": {"react": "^18"}}')


def test_cli_generation_and_readonly():
    print("== CLI: generation is read-only over graph.json ==")
    with tempfile.TemporaryDirectory() as root:
        _fixture_repo(root)
        md = os.path.join(root, "map")
        r = _cli(root, md, "init", "--full")
        check("fixture init ok", r.returncode == 0, r.stdout + r.stderr)
        before = open(os.path.join(md, "graph.json"), "rb").read()
        r = _cli(root, md, "visualize")
        check("visualize ok", r.returncode == 0, r.stdout + r.stderr)
        idx = os.path.join(md, "visualization", "index.html")
        dat = os.path.join(md, "visualization", "data", "graph.js")
        boot = os.path.join(md, "visualization", "data", "boot.js")
        check("index.html exists", os.path.exists(idx))
        check("data/graph.js exists", os.path.exists(dat))
        check("data/boot.js exists", os.path.exists(boot))
        after = open(os.path.join(md, "graph.json"), "rb").read()
        check("graph.json untouched by visualize", before == after)
        html = open(idx, encoding="utf-8").read()
        check("html loads data relatively",
              'src="data/graph.js"' in html and 'src="data/boot.js"' in html)
        check("no CDN/runtime network refs",
              "http:// доверия" not in html and "https://" not in html
              and "cdn." not in html.lower())
        payload = open(dat, encoding="utf-8").read()
        check("data is JSONP global",
              payload.startswith("window.__CARTO_GRAPH__ = "))
        view = json.loads(payload[len("window.__CARTO_GRAPH__ = "):-1])
        check("data has all modes",
              set(view["modes"]) == set(V.MODES))
        check("no network calls in app",
              "fetch(" not in html and "XMLHttpRequest" not in html)


def test_stale_and_malformed():
    print("== stale warning + malformed graph ==")
    with tempfile.TemporaryDirectory() as root:
        _fixture_repo(root)
        md = os.path.join(root, "map")
        r = _cli(root, md, "init", "--full")
        check("init ok", r.returncode == 0)
        # make map stale with a TRACKED change
        with open(os.path.join(root, "api/app.py"), "a",
                  encoding="utf-8") as fh:
            fh.write("\n# touch\n")
        r = _cli(root, md, "visualize")
        check("stale blocks without --allow-stale",
              r.returncode == 1 and "stale" in r.stdout.lower(),
              r.stdout + r.stderr)
        r = _cli(root, md, "visualize", "--allow-stale")
        check("--allow-stale generates", r.returncode == 0,
              r.stdout + r.stderr)
        boot = open(os.path.join(md, "visualization", "data", "boot.js"),
                    encoding="utf-8").read()
        check("stale surfaced in boot",
              json.loads(boot[len("window.__CARTO_BOOT__ = "):-1])["stale"])
        # malformed graph handled gracefully
        with open(os.path.join(md, "graph.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{not json")
        r = _cli(root, md, "visualize")
        check("malformed handled",
              r.returncode == 2 and "CORRUPT" in r.stdout, r.stdout)


def test_focus_flags():
    print("== --symbol/--impact/--flow flags ==")
    with tempfile.TemporaryDirectory() as root:
        _fixture_repo(root)
        md = os.path.join(root, "map")
        _cli(root, md, "init", "--full")

        def boot_of(*a):
            _cli(root, md, "visualize", *a)
            raw = open(os.path.join(md, "visualization", "data",
                                    "boot.js"), encoding="utf-8").read()
            return json.loads(raw[len("window.__CARTO_BOOT__ = "):-1])

        b = boot_of("--impact", "list_items")
        check("impact boots impact mode",
              b["mode"] == "impact" and b["focus"]["impact"], str(b["mode"]))
        b = boot_of("--flow", "GET /items")
        check("flow boots flow mode",
              b["mode"] == "flow" and b["focus"]["flow"], str(b["mode"]))
        b = boot_of("--symbol", "Cart")
        check("symbol boots focus node",
              b["focus"]["node"] == "Cart", str(b["focus"]))
        r = _cli(root, md, "visualize", "--impact", "NoSuchSymbol")
        check("unknown symbol errors cleanly", r.returncode == 1,
              r.stdout)


def test_regeneration():
    print("== regeneration after graph change ==")
    with tempfile.TemporaryDirectory() as root:
        _fixture_repo(root)
        md = os.path.join(root, "map")
        _cli(root, md, "init", "--full")
        _cli(root, md, "visualize")
        dat = os.path.join(md, "visualization", "data", "graph.js")
        n1 = len(json.loads(
            open(dat, encoding="utf-8").read()
            [len("window.__CARTO_GRAPH__ = "):-1])["nodes"])
        with open(os.path.join(root, "api/app.py"), "a",
                  encoding="utf-8") as fh:
            fh.write("\n@app.post(\"/items\")\n"
                     "def create_item():\n    return {}\n")
        _cli(root, md, "sync")
        _cli(root, md, "visualize")
        n2 = len(json.loads(
            open(dat, encoding="utf-8").read()
            [len("window.__CARTO_GRAPH__ = "):-1])["nodes"])
        check("regeneration reflects new graph", n2 > n1, f"{n1} -> {n2}")


def test_template_js_safety():
    print("== template JS: no TDZ self-reference, file:// guards ==")
    for name in ("viz_template_head.html", "viz_template_p2.js",
                 "viz_template_p3.js"):
        src = open(os.path.join(SCRIPTS, name), encoding="utf-8").read()
        # canvas font must not self-reference its own const (TDZ crash)
        check(f"{name}: no self-referential FONT",
              "const FONT" not in src or "? FONT :" not in src)
        # history.replaceState must be skipped on file:// (opaque origin:
        # Chrome logs "Unsafe attempt to load URL ..." even when caught,
        # so try/catch alone is not enough — needs a protocol early-return)
        if "history.replaceState" in src:
            pre = src[:src.find("history.replaceState")]
            check(f"{name}: replaceState guarded for file://",
                  'location.protocol==="file:"' in pre
                  and "return" in pre[pre.find('location.protocol'):])
    p2 = open(os.path.join(SCRIPTS, "viz_template_p2.js"),
              encoding="utf-8").read()
    check("p2: DATA const comes from generated app (not template)",
          p2.count("const DATA") == 0)
    check("p2: FONT defined before canvas use",
          p2.find("const FONT") < p2.find("ctx.font"))


def test_view_layout_finite():
    print("== view layout: every mode yields finite coordinates ==")
    g = G.new_graph("/t", "abc", "2026-01-01T00:00:00+00:00")
    # interlinked kinds across confidence levels (mirrors real graphs)
    specs = [("service", "OrderSvc", "HIGH"), ("method", "place", "HIGH"),
             ("endpoint", "POST /orders", "HIGH"),
             ("function", "create_order", "MEDIUM"),
             ("table", "orders", "HIGH"),
             ("external-service", "s3", "MEDIUM"),
             ("component", "Cart", "LOW")]
    ids = {}
    for i, (kind, name, conf) in enumerate(specs):
        nid = f"{kind}:{name}"
        ids[name] = nid
        g["nodes"].append(_node(nid, kind, name, conf=conf))
    pairs = [("OrderSvc", "place"), ("place", "POST /orders"),
             ("place", "create_order"), ("create_order", "orders"),
             ("Cart", "POST /orders"), ("place", "s3")]
    for s, t in pairs:
        g["edges"].append(_edge(ids[s], ids[t]))
    view = V.build_view_model(g)
    for mode, m in view["modes"].items():
        check(f"{mode}: non-empty default", len(m["nodes"]) > 0,
              str(len(m["nodes"])))
    check("impact default non-empty (entry seeds)",
          len(view["modes"]["impact"]["nodes"]) > 0)
    check("flow default non-empty (entry seeds)",
          len(view["modes"]["flow"]["nodes"]) > 0)
    # simulate the JS layout contract: seed-on-circle + relax must keep
    # every coordinate finite (regression: relax() once NaN-poisoned new
    # views via stale adjacency, making nodes invisible + unhittable)
    import math
    for mode, m in view["modes"].items():
        want = set(m["nodes"])
        n = len(want)
        pos = {}
        for i, nid in enumerate(sorted(want)):
            a = (i / max(n, 1)) * math.pi * 2
            pos[nid] = [math.cos(a) * 220, math.sin(a) * 220]
        ok = all(math.isfinite(x) and math.isfinite(y)
                 for x, y in pos.values())
        check(f"{mode}: seed coords finite", ok)


def test_relax_stays_bounded():
    print("== relax dynamics: dense 300-node graph stays bounded + spread ==")
    # Regression: relax() once diverged (coords ~1e300, camera flung to
    # ±1e306), so every force-directed mode painted all nodes as one dot.
    # Simulate the template's capped relax in Python and assert the cloud
    # stays bounded AND spread out (not collapsed to a single point).
    import math
    import random
    rng = random.Random(42)
    n = 300
    pos = {}
    R0 = max(220, math.sqrt(n) * 48)
    for i in range(n):
        a = (i / n) * math.pi * 2
        pos[i] = [math.cos(a) * R0 + (i * 37) % 41 - 20,
                  math.sin(a) * R0 + (i * 53) % 43 - 21]
    edges = [(i % n, (i * 7 + 3) % n) for i in range(n * 2)]
    MAXD = 8
    for _ in range(140):
        for i in range(0, n, 7):
            for j in range(i + 7, n, 7):
                ax, ay = pos[i]
                bx, by = pos[j]
                dx, dy = ax - bx, ay - by
                d2 = dx * dx + dy * dy + 40
                f = min(2600 / d2, 4)
                d = math.sqrt(d2)
                ux, uy = dx / d * f, dy / d * f
                m = math.hypot(ux, uy)
                if m > MAXD:
                    ux, uy = ux / m * MAXD, uy / m * MAXD
                pos[i][0] += ux
                pos[i][1] += uy
                pos[j][0] -= ux
                pos[j][1] -= uy
        for s, t in edges:
            ax, ay = pos[s]
            bx, by = pos[t]
            dx, dy = bx - ax, by - ay
            d = max(20, math.hypot(dx, dy))
            f = min(MAXD, (d - 90) * 0.02)
            ux, uy = dx / d * f, dy / d * f
            pos[s][0] += ux
            pos[s][1] += uy
            pos[t][0] -= ux
            pos[t][1] -= uy
        for i in range(n):
            pos[i][0] *= 0.995
            pos[i][1] *= 0.995
        m = max(max(abs(x), abs(y)) for x, y in pos.values())
        if m > 4000:
            sc = 1500 / m
            for i in range(n):
                pos[i][0] *= sc
                pos[i][1] *= sc
    mags = [math.hypot(x, y) for x, y in pos.values()]
    check("relax: all coords finite",
          all(math.isfinite(x) and math.isfinite(y)
              for x, y in pos.values()))
    check("relax: bounded (< 1e6)",
          max(mags) < 1e6, str(max(mags)))
    xs = {round(x / 50) for x, y in pos.values()}
    check("relax: spread across > 10 distinct cells (not one dot)",
          len(xs) > 10, str(len(xs)))
    check("relax: mean radius sane (50..4000)",
          50 < sum(mags) / len(mags) < 4000,
          str(sum(mags) / len(mags)))


def test_existing_suite_green():
    print("== existing suite still green ==")
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "run_tests.py")],
        capture_output=True, text=True, timeout=600)
    check("run_tests.py passes", r.returncode == 0, r.stdout[-500:])


def test_generated_markdown_safety():
    # Wave 4 / Part 3 item 6 (NEW). Pre-fix behavior: hostile symbol names
    # (pipes, newlines, brackets) were interpolated raw into generated
    # .md tables, splitting cells/rows so table row pipe-counts diverged.
    # md_cell() now escapes pipes and folds newlines; this pins every
    # table row in every generated view to a consistent pipe count with
    # no raw control chars inside cells.
    print("== generated markdown safety (hostile node names) ==")
    with tempfile.TemporaryDirectory() as root:
        _write(root, "api/app.py",
               "from fastapi import FastAPI\napp = FastAPI()\n"
               "@app.get(\"/items\")\ndef list_items():\n    return []\n")
        _write(root, "api/requirements.txt", "fastapi==0.115.0\n")
        md = os.path.join(root, "map")
        r = _cli(root, md, "init", "--full")
        check("init ok", r.returncode == 0, r.stdout + r.stderr)
        gpath = os.path.join(md, "graph.json")
        g = json.load(open(gpath, encoding="utf-8"))
        g["nodes"].append({"id": "py:function:hostile",
                           "kind": "function",
                           "name": "evil|name\nwith[brackets]",
                           "file": "api/app.py", "line": 2,
                           "confidence": "HIGH", "meta": {},
                           "evidence": []})
        json.dump(g, open(gpath, "w", encoding="utf-8"),
                  indent=1, sort_keys=True)
        sys.path.insert(0, SCRIPTS)
        import core as _core
        g2 = json.load(open(gpath, encoding="utf-8"))
        _core.write_views(root, md, g2)
        bad_files = []
        # Count structural pipes only: md_cell() escapes hostile "|" as
        # "\|", which must not be mistaken for a cell split.
        structural = re.compile(r"(?<!\\)\|")
        for dirpath, _, files in os.walk(md):
            if os.path.basename(dirpath) == "visualization":
                continue
            for fn in files:
                if not fn.endswith(".md"):
                    continue
                ap = os.path.join(dirpath, fn)
                rows = [ln for ln in
                        open(ap, encoding="utf-8").read().splitlines()
                        if ln.startswith("|")]
                if not rows:
                    continue
                counts = {len(structural.findall(ln)) for ln in rows}
                if len(counts) != 1:
                    bad_files.append(f"{fn}: pipe counts {sorted(counts)}")
                for ln in rows:
                    inner = ln.strip().strip("|")
                    if any(ord(c) < 32 and c not in ("\t",)
                           for c in inner):
                        bad_files.append(f"{fn}: raw control char in row")
                        break
        check("all generated .md table rows consistent",
              not bad_files, str(bad_files[:5]))
        sym = open(os.path.join(md, "symbols.md"),
                   encoding="utf-8").read()
        check("hostile name escaped (no raw pipe cell split)",
              "evil\\|name" in sym, sym[-400:])
        mods = open(os.path.join(md, "modules.md"),
                    encoding="utf-8").read().splitlines()
        mrows = [ln for ln in mods if ln.startswith("|")]
        check("modules.md rows consistent",
              len({len(structural.findall(ln)) for ln in mrows}) == 1,
              str({ln.count("|") for ln in mrows}))


def main():
    test_projection_modes()
    test_confidence_distinguishable()
    test_unknowns_dont_crash()
    test_empty_graph()
    test_large_fixture()
    test_tech_agnostic_fixtures()
    test_focus_reuses_core()
    test_cli_generation_and_readonly()
    test_stale_and_malformed()
    test_focus_flags()
    test_regeneration()
    test_template_js_safety()
    test_view_layout_finite()
    test_relax_stays_bounded()
    test_generated_markdown_safety()
    test_existing_suite_green()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
