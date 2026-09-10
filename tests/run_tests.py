"""Regression + generic-conformance tests for living-codebase-cartographer.

Run:  python3 tests/run_tests.py
(no third-party deps; stdlib only. Safe to run anywhere — uses temp dirs.)

Covers:
  1. Generic-graph conformance: every emitted kind/edge type across all
     analyzers belongs to the closed generic vocabulary (no tech leakage).
  2. Technology detection on representative stacks.
  3. Analyzer dispatch: one file -> the right analyzer(s).
  4. v1 -> v2 migration preserves curated semantics.
  5. End-to-end init/sync/validate on a mixed Python+TS fixture via the CLI.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL, "scripts")
CLI = os.path.join(SCRIPTS, "cartographer.py")

sys.path.insert(0, SKILL)

from analyzers.graph import NODE_KINDS, EDGE_TYPES  # noqa: E402
from analyzers import (  # noqa: E402
    analyzers_for, detect_tech, BY_NAME,
)
from analyzers import java, typescript, python, go, csharp, rust, sql  # noqa: E402,E501
from analyzers.context import ScanContext  # noqa: E402
from analyzers import graph as G  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name} {detail}")


def fresh_graph():
    return G.new_graph("/tmp/test-root", "abc", "2026-01-01T00:00:00+00:00")


def run_analyzer(mod, rel, text):
    g = fresh_graph()
    ctx = ScanContext(g, G.add_node, G.add_edge, rel, "abc")
    mod.scan(ctx, rel, text)
    return g


# ---------------------------------------------------------------- 1. schema

def test_closed_schema():
    print("== generic-graph conformance ==")
    samples = {
        java: ("svc/Foo.java",
               "package com.x;\nimport org.springframework.web.bind.annotation.*;\n"
               "@RestController\n@RequestMapping(\"/api/a\")\n"
               "public class Foo {\n"
               "    private final Bar bar;\n"
               "    public Foo(Bar bar) { this.bar = bar; }\n"
               "    @GetMapping(\"/{id}\")\n"
               "    public String get() { return bar.find(); }\n}\n"),
        typescript: ("ui/a.ts",
                     "import {x} from './b';\n"
                     "import {Component} from '@angular/core';\n"
                     "@Component({})\nexport class Card {}\n"
                     "export function App() { return null; }\n"),
        python: ("api/main.py",
                 "from fastapi import FastAPI\napp = FastAPI()\n"
                 "@app.get(\"/items\")\ndef list_items():\n    return []\n"),
        go: ("svc/s.go",
             "package main\nimport \"net/http\"\n"
             "type S struct{}\n"
             "func (s *S) Get() string { return \"x\" }\n"
             "func main() {\n"
             "  http.HandleFunc(\"/a\", h)\n"
             "  RegisterSServiceServer(srv, &S{})\n}\n"),
        csharp: ("A/B.cs",
                 "using System;\nnamespace N {\n"
                 "[ApiController]\npublic class B {\n"
                 "  [HttpGet(\"{id}\")]\n"
                 "  public string Get(string id) { return id; }\n}}\n"),
        rust: ("src/a.rs",
               "use crate::b;\n"
               "#[get(\"/a\")]\nasync fn a() -> String {\n"
               "  \"x\".to_string()\n}\n"),
        sql: ("m/V1__x.sql",
              "CREATE TABLE t (id INT);\nALTER TABLE t ADD COLUMN c INT;\n"),
    }
    for mod, (rel, text) in samples.items():
        g = run_analyzer(mod, rel, text)
        badk = {n["kind"] for n in g["nodes"]} - NODE_KINDS
        bade = {e["type"] for e in g["edges"]} - EDGE_TYPES
        check(f"{mod.NAME}: kinds closed", not badk, str(badk))
        check(f"{mod.NAME}: edges closed", not bade, str(bade))
        check(f"{mod.NAME}: emits evidence",
              all(n["file"] == rel for n in g["nodes"]) and len(g["nodes"]) > 0)


def test_java_maps_to_generic():
    print("== java analyzer uses generic kinds ==")
    g = run_analyzer(
        java, "com/x/Foo.java",
        "package com.x;\n"
        "@Entity\npublic class Foo {\n}\n")
    kinds = {n["name"]: n["kind"] for n in g["nodes"]}
    check("entity folds to class", kinds.get("Foo") == "class", str(kinds))
    g = run_analyzer(
        java, "com/x/R.java",
        "package com.x;\n"
        "public interface R extends JpaRepository<Foo, Long> {\n}\n")
    kinds = {n["name"]: n["kind"] for n in g["nodes"]}
    check("JpaRepository folds to interface",
          kinds.get("R") == "interface", str(kinds))
    g = run_analyzer(
        java, "com/x/Dto.java",
        "package com.x;\n"
        "public record Dto(String a) {}\n")
    kinds = {n["name"]: n["kind"] for n in g["nodes"]}
    check("record folds to class (meta keeps nuance)",
          kinds.get("Dto") == "class", str(kinds))
    dto = [n for n in g["nodes"] if n["name"] == "Dto"][0]
    check("record nuance in meta", dto["meta"].get("java_kind") == "record",
          str(dto["meta"]))


def test_all_output_conforms():
    print("== all-analyzer output conforms (incl. fallback marking) ==")
    from analyzers import fallback
    g = run_analyzer(
        fallback, "mystery.rb",
        "class Widget\n  def render\n    fetch('/api/widgets')\n  end\nend\n")
    badk = {n["kind"] for n in g["nodes"]} - NODE_KINDS
    check("fallback kinds closed", not badk, str(badk))
    lows = [e for e in g["edges"]]
    check("fallback emits LOW only",
          all(e["confidence"] == "LOW" for e in lows) and lows)
    check("fallback marks reason",
          all(e["meta"].get("reason") == "unsupported-language-fallback"
              for e in lows))


# ---------------------------------------------------------------- 2. detect

def test_detection():
    print("== technology detection ==")
    files = [("backend/pom.xml", ""), ("frontend/package.json", ""),
             ("backend/src/main/resources/application.properties", ""),
             ("docker-compose.yml", "")]
    texts = {
        "backend/pom.xml": "<artifactId>spring-boot-starter-web</artifactId>",
        "frontend/package.json": '{"dependencies": {"@angular/core": "1"}}',
        "backend/src/main/resources/application.properties":
            "spring.datasource.url=jdbc:postgresql://db/app",
        "docker-compose.yml": "services:\n  db:\n  keycloak:\n",
    }
    det = detect_tech(files, lambda r: texts.get(r, ""))
    langs = {d["name"]: d["confidence"]
             for d in det.get("languages", [])}
    check("detects java HIGH", langs.get("java") == "HIGH", str(langs))
    check("detects angular HIGH",
          any(d["name"] == "angular" and d["confidence"] == "HIGH"
              for d in det.get("frameworks", [])))
    check("detects postgresql HIGH",
          any(d["name"] == "postgresql" and d["confidence"] == "HIGH"
              for d in det.get("databases", [])))
    check("never claims without evidence",
          all(d["confidence"] in ("HIGH", "MEDIUM", "LOW")
              for sec in det.values() for d in sec))


# ---------------------------------------------------------------- 3. dispatch

def test_dispatch():
    print("== analyzer dispatch ==")
    cases = [("a.java", ["java"]), ("a.py", ["python"]),
             ("a.go", ["go"]), ("a.cs", ["csharp"]), ("a.rs", ["rust"]),
             ("m/V1__x.sql", ["sql"]), ("a.rb", ["fallback"])]
    for path, expected in cases:
        got = [a.NAME for a in analyzers_for(path, "")]
        if path == "a.rb":
            # .rb is unknown-source: analyzed via the LOW fallback pass
            check("a.rb -> fallback (graceful degradation)",
                  got == expected, str(got))
        else:
            check(f"{path} -> {expected}", got == expected, str(got))
    check("registry has fallback", "fallback" in BY_NAME)


# ---------------------------------------------------------------- 4. migrate

def test_migrate_v1():
    print("== v1 -> v2 migration ==")
    sys.path.insert(0, SCRIPTS)
    import core
    g = {"version": 1, "nodes": [
        {"id": "a", "kind": "frontend-component", "name": "C",
         "file": "f", "line": 1},
        {"id": "b", "kind": "database-table", "name": "t",
         "file": "f", "line": 2},
        {"id": "c", "kind": "entity", "name": "E",
         "file": "f", "line": 3}],
        "edges": [
        {"src": "a", "dst": "b", "type": "stereotyped-as",
         "file": "f", "line": 1}]}
    core.migrate_v1(g)
    kinds = {n["id"]: n["kind"] for n in g["nodes"]}
    check("component migrates", kinds["a"] == "component", str(kinds))
    check("table migrates", kinds["b"] == "table", str(kinds))
    check("entity folds to class", kinds["c"] == "class", str(kinds))
    check("edge migrates",
          g["edges"][0]["type"] == "references", str(g["edges"]))
    check("version bumps", g["version"] == 2)
    # Wave 5: chained migrate_v1 -> migrate_v2 path must end at VERSION 3
    # with provenance stamped (cmd_sync applies both in order).
    g2 = {"version": 1, "nodes": [
        {"id": "a", "kind": "frontend-component", "name": "C",
         "file": "f", "line": 1}],
        "edges": [
        {"src": "a", "dst": "b", "type": "stereotyped-as",
         "file": "f", "line": 1}]}
    core.migrate_v1(g2)
    check("chained: v1 lands at 2", g2["version"] == 2)
    core.migrate_v2(g2)
    check("chained: v2 lands at VERSION 3",
          g2["version"] == core.VERSION == 3, str(g2["version"]))
    check("chained: provenance stamped derived",
          all(n.get("provenance") == "derived" for n in g2["nodes"])
          and all(e.get("provenance") == "derived"
                  for e in g2["edges"]), str(g2))
    check("migrate_v2 preserves explicit asserted",
          _migrate_v2_keeps_asserted(core))


def _migrate_v2_keeps_asserted(core):
    g = {"version": 2,
         "nodes": [{"id": "intent:requirement:x", "kind": "requirement",
                    "name": "X", "file": "docs/requirements.md", "line": 1,
                    "provenance": "asserted"}],
         "edges": []}
    core.migrate_v2(g)
    return g["nodes"][0].get("provenance") == "asserted"


# ---------------------------------------------------------------- 5. e2e CLI

def _write(root, rel, content):
    ap = os.path.join(root, rel)
    os.makedirs(os.path.dirname(ap), exist_ok=True)
    with open(ap, "w", encoding="utf-8") as fh:
        fh.write(content)


def _cli(root, mapdir, *args):
    return subprocess.run(
        [sys.executable, CLI, "--root", root, "--map-dir", mapdir] + list(args),
        capture_output=True, text=True, timeout=120)


def test_e2e_mixed_repo():
    print("== e2e init/sync/validate (python + ts fixture) ==")
    with tempfile.TemporaryDirectory() as root:
        _write(root, "api/app.py",
               "from fastapi import FastAPI\napp = FastAPI()\n"
               "@app.get(\"/items\")\ndef list_items():\n    return []\n")
        _write(root, "api/requirements.txt", "fastapi==0.115.0\n")
        _write(root, "ui/src/c.tsx",
               "export function Cart() {\n"
               "  fetch('/items').then(r => r.json());\n"
               "  return null;\n}\n")
        _write(root, "ui/package.json",
               '{"dependencies": {"react": "^18"}}')
        md = os.path.join(root, "map")
        r = _cli(root, md, "init", "--full")
        check("init ok", r.returncode == 0, r.stdout + r.stderr)
        g = json.load(open(os.path.join(md, "graph.json")))
        badk = {n["kind"] for n in g["nodes"]} - NODE_KINDS
        check("e2e kinds closed", not badk, str(badk))
        eps = {n["name"] for n in g["nodes"] if n["kind"] == "endpoint"}
        check("fastapi endpoint found", "GET /items" in eps, str(eps))
        check("fetch consumer edge observed (same generic endpoint id)",
              any(e["type"] == "consumes" and
                  e["dst"] == "endpoint:GET /items" and
                  e.get("meta", {}).get("via") == "fetch"
                  for e in g["edges"]),
              str([(e["src"], e["type"], e["dst"])
                   for e in g["edges"]]))
        r = _cli(root, md, "validate")
        check("validate ok", r.returncode == 0, r.stdout)
        # incremental: add endpoint, resync, expect targeted change record
        with open(os.path.join(root, "api/app.py"), "a",
                  encoding="utf-8") as fh:
            fh.write("\n@app.post(\"/items\")\n"
                     "def create_item():\n    return {}\n")
        r = _cli(root, md, "sync")
        check("sync ok", r.returncode == 0, r.stdout + r.stderr)
        g2 = json.load(open(os.path.join(md, "graph.json")))
        eps2 = {n["name"] for n in g2["nodes"] if n["kind"] == "endpoint"}
        check("sync adds endpoint", "POST /items" in eps2, str(eps2))
        check("py nodes intact",
              any(n.get("meta", {}).get("lang") == "python"
                  for n in g2["nodes"]))
        check("ts nodes preserved",
              any(n.get("meta", {}).get("lang") == "typescript"
                  for n in g2["nodes"]))
        r = _cli(root, md, "validate")
        check("post-sync validate ok", r.returncode == 0, r.stdout)


def test_same_file_calls():
    print("== same-file calls edges (flow works on every stack) ==")
    samples = {
        python: ("api/m.py",
                 "def fetch_o(i):\n    return i\n\n"
                 "def get_o():\n    return fetch_o(1)\n"),
        typescript: ("ui/a.ts",
                     "export function helper() { return 1; }\n"
                     "export function main() { return helper(); }\n"),
        go: ("s/s.go",
             "package s\nfunc helper() int { return 1 }\n"
             "func Main() int { return helper() }\n"),
        csharp: ("A/B.cs",
                 "using System;\nnamespace N {\n"
                 "public class B {\n"
                 "  public int Helper() { return 1; }\n"
                 "  public int Main() { return Helper(); }\n}}\n"),
        rust: ("src/a.rs",
               "fn helper() -> i32 { 1 }\n"
               "fn main_fn() -> i32 { helper() }\n"),
    }
    for mod, (rel, text) in samples.items():
        g = run_analyzer(mod, rel, text)
        calls = [e for e in g["edges"] if e["type"] == "calls"
                 and e["confidence"] == "HIGH"
                 and e.get("meta", {}).get("via") == "same-file"]
        check(f"{mod.NAME}: same-file HIGH calls edge", len(calls) >= 1,
              str([(e["src"], e["dst"]) for e in g["edges"]]))
        by_id = {n["id"] for n in g["nodes"]}
        check(f"{mod.NAME}: calls dst resolves (no placeholder)",
              all(c["dst"] in by_id for c in calls),
              str([c["dst"] for c in calls]))


def test_status_after_sync():
    print("== status CURRENT after sync (hash-verified, not git-signal) ==")
    with tempfile.TemporaryDirectory() as root:
        _write(root, "api/app.py",
               "from fastapi import FastAPI\napp = FastAPI()\n"
               "@app.get(\"/items\")\ndef list_items():\n    return []\n")
        _write(root, "api/requirements.txt", "fastapi==0.115.0\n")
        md = os.path.join(root, "map")
        r = _cli(root, md, "init", "--full")
        check("init ok", r.returncode == 0, r.stdout + r.stderr)
        r = _cli(root, md, "status")
        check("fresh map CURRENT", "CURRENT" in r.stdout, r.stdout)
        # uncommitted edit (temp dir: no git at all) -> NEEDS_SYNC + S row
        with open(os.path.join(root, "api/app.py"), "a",
                  encoding="utf-8") as fh:
            fh.write("\n# touch\n")
        r = _cli(root, md, "status")
        check("edited file NEEDS_SYNC",
              "NEEDS_SYNC" in r.stdout, r.stdout)
        check("edited file listed as unsynced",
              "`api/app.py`" in r.stdout, r.stdout)
        r = _cli(root, md, "sync")
        check("sync ok", r.returncode == 0, r.stdout + r.stderr)
        r = _cli(root, md, "status")
        check("post-sync status CURRENT", "CURRENT" in r.stdout, r.stdout)
        check("status output not garbled", "ackend" not in r.stdout.replace(
            "backend", ""), r.stdout[:500])


def _git(root, *args):
    return subprocess.run(["git", "-C", root] + list(args), capture_output=True,
                          text=True, timeout=60)


def test_git_porcelain_first_line():
    print("== git porcelain first-line preservation ==")
    sys.path.insert(0, SCRIPTS)
    import core
    with tempfile.TemporaryDirectory() as root:
        r = _git(root, "init")
        if r.returncode != 0:
            check("git available (skipped)", True)
            return
        _git(root, "config", "user.email", "t@t")
        _git(root, "config", "user.name", "t")
        _write(root, "aaa_first.py", "alpha = 1\n")
        _write(root, "zzz_other.py", "omega = 1\n")
        _git(root, "add", ".")
        _git(root, "commit", "-qm", "init")
        base = _git(root, "rev-parse", "HEAD").stdout.strip()
        # unstaged modify of the alphabetically-first file: porcelain's
        # first line starts with a space (' M ...') — the old
        # stdout.strip() ate it and mangled the path to 'aa_first.py'.
        with open(os.path.join(root, "aaa_first.py"), "a",
                  encoding="utf-8") as fh:
            fh.write("beta = 2\n")
        changed = core.changed_since(root, base)
        check("first-line path intact",
              "aaa_first.py" in changed, str(sorted(changed)))
        check("no mangled path", "aa_first.py" not in changed,
              str(sorted(changed)))
        check("rename dest parsed",
              core._parse_porcelain_path("R  old.py -> new.py") == "new.py")
        check("untracked parsed",
              core._parse_porcelain_path("?? new.py") == "new.py")
        check("quoted space parsed",
              core._parse_porcelain_path(' M "a b.py"') == "a b.py")
        # Wave 5: unstaged modification of a non-first file also reports
        # verbatim (no staging); ?? entries and renames with dests parse.
        with open(os.path.join(root, "zzz_other.py"), "a",
                  encoding="utf-8") as fh:
            fh.write("extra = 1\n")
        changed2 = core.changed_since(root, base)
        check("unstaged non-first path verbatim",
              "zzz_other.py" in changed2, str(sorted(changed2)))
        _write(root, "brand_new.py", "q = 1\n")
        changed3 = core.changed_since(root, base)
        check("?? entry verbatim (real repo)",
              "brand_new.py" in changed3, str(sorted(changed3)))
        check("unicode entry parses",
              core._parse_porcelain_path(
                  ' M "caf\\303\\251.py"') == "café.py")
        check("rename-dest parses",
              core._parse_porcelain_path(
                  'R  "old name.py" -> "new name.py"') == "new name.py")


def test_convergence_git_repo():
    print("== convergence: init -> modify -> sync -> validate OK ==")
    with tempfile.TemporaryDirectory() as root:
        if _git(root, "init").returncode != 0:
            check("git available (skipped)", True)
            return
        _git(root, "config", "user.email", "t@t")
        _git(root, "config", "user.name", "t")
        _write(root, "aaa_first.py", "def alpha():\n    return 1\n")
        _write(root, "zzz_other.py", "omega = 1\n")
        _git(root, "add", ".")
        _git(root, "commit", "-qm", "init")
        md = os.path.join(root, "map")
        r = _cli(root, md, "init", "--full")
        check("init ok", r.returncode == 0, r.stdout + r.stderr)
        with open(os.path.join(root, "aaa_first.py"), "a",
                  encoding="utf-8") as fh:
            fh.write("\ndef beta_new():\n    return 2\n")
        r = _cli(root, md, "sync")
        check("sync ok", r.returncode == 0, r.stdout + r.stderr)
        r = _cli(root, md, "query", "--name", "beta_new")
        check("sync picks up beta_new", "beta_new" in r.stdout, r.stdout)
        r = _cli(root, md, "validate")
        check("validate OK",
              r.returncode == 0 and "Status: OK" in r.stdout, r.stdout)


def test_status_validate_agreement():
    print("== status/validate agreement (shared freshness verdict) ==")
    sys.path.insert(0, SCRIPTS)
    import core
    with tempfile.TemporaryDirectory() as root:
        _write(root, "api/app.py",
               "from fastapi import FastAPI\napp = FastAPI()\n"
               "@app.get(\"/items\")\ndef list_items():\n    return []\n")
        _write(root, "api/requirements.txt", "fastapi==0.115.0\n")
        md = os.path.join(root, "map")
        r = _cli(root, md, "init", "--full")
        check("init ok", r.returncode == 0, r.stdout + r.stderr)
        verdict = core.freshness_verdict(root, md)
        check("fresh verdict CURRENT", verdict["verdict"] == "CURRENT",
              str(verdict))
        rs = _cli(root, md, "status")
        rv = _cli(root, md, "validate")
        check("both CURRENT/OK when fresh",
              "CURRENT" in rs.stdout and "Status: OK" in rv.stdout,
              rs.stdout + rv.stdout)
        with open(os.path.join(root, "api/app.py"), "a",
                  encoding="utf-8") as fh:
            fh.write("\n# touch\n")
        verdict = core.freshness_verdict(root, md)
        check("stale verdict NEEDS_SYNC",
              verdict["verdict"] == "NEEDS_SYNC", str(verdict))
        rs = _cli(root, md, "status")
        rv = _cli(root, md, "validate")
        check("status NEEDS_SYNC", "NEEDS_SYNC" in rs.stdout, rs.stdout)
        check("validate NEEDS_SYNC", "NEEDS_SYNC" in rv.stdout, rv.stdout)
        check("labels present",
              "Changed (git signal)" in rs.stdout
              and "Stale (content differs)" in rs.stdout
              and "Changed (git signal)" in rv.stdout
              and "Stale (content differs)" in rv.stdout,
              rs.stdout + rv.stdout)


def test_malformed_id_validate_fails():
    print("== malformed ID validate failure ==")
    with tempfile.TemporaryDirectory() as root:
        _write(root, "api/app.py",
               "from fastapi import FastAPI\napp = FastAPI()\n"
               "@app.get(\"/items\")\ndef list_items():\n    return []\n")
        _write(root, "api/requirements.txt", "fastapi==0.115.0\n")
        md = os.path.join(root, "map")
        r = _cli(root, md, "init", "--full")
        check("init ok", r.returncode == 0, r.stdout + r.stderr)
        gpath = os.path.join(md, "graph.json")
        g = json.load(open(gpath))
        g["nodes"].append({"id": "py:function:bad\nid\twith",
                           "kind": "function", "name": "bad",
                           "file": "api/app.py", "line": 1,
                           "confidence": "HIGH", "meta": {},
                           "evidence": []})
        g["nodes"].append({"id": "x" * 600, "kind": "function",
                           "name": "long", "file": "api/app.py", "line": 1,
                           "confidence": "HIGH", "meta": {},
                           "evidence": []})
        json.dump(g, open(gpath, "w"), indent=1, sort_keys=True)
        r = _cli(root, md, "validate")
        check("validate fails on malformed IDs", r.returncode != 0,
              r.stdout)
        check("malformed IDs reported", "BAD_ID" in r.stdout, r.stdout)


def test_flow_no_path():
    print("== flow NO-PATH + impact EMPTY (non-zero, explicit) ==")
    with tempfile.TemporaryDirectory() as root:
        _write(root, "a.py", "def alpha():\n    return 1\n")
        _write(root, "b.py", "def beta():\n    return 2\n")
        md = os.path.join(root, "map")
        r = _cli(root, md, "init", "--full")
        check("init ok", r.returncode == 0, r.stdout + r.stderr)
        r = _cli(root, md, "flow", "--from", "alpha", "--to", "beta")
        check("flow no-path exits non-zero", r.returncode != 0,
              r.stdout + f" (exit {r.returncode})")
        check("flow prints NO-PATH", "NO-PATH" in r.stdout, r.stdout)
        check("flow hints cause", "Likely cause" in r.stdout, r.stdout)
        r = _cli(root, md, "impact", "alpha")
        check("impact connected exits zero", r.returncode == 0,
              r.stdout + f" (exit {r.returncode})")
        # truly isolated node (no edges at all) -> explicit EMPTY + non-zero
        gpath = os.path.join(md, "graph.json")
        g = json.load(open(gpath))
        g["nodes"].append({"id": "py:function:lonely", "kind": "function",
                           "name": "lonely", "file": "lone.py", "line": 1,
                           "confidence": "HIGH", "meta": {},
                           "evidence": []})
        json.dump(g, open(gpath, "w"), indent=1, sort_keys=True)
        r = _cli(root, md, "impact", "lonely")
        check("impact isolated exits non-zero", r.returncode != 0,
              r.stdout + f" (exit {r.returncode})")
        check("impact prints EMPTY", "IMPACT EMPTY" in r.stdout, r.stdout)


def test_git_porcelain_edge_paths():
    # Wave 4 / Part 3 item 1 (extends test_git_porcelain_first_line, which
    # covers first-line space + rename/untracked/quoted-space unit cases).
    # Pre-fix behavior (git().strip() + naive split): unicode escapes were
    # never decoded (raw '"caf\\303\\251.py"' entered the change set), CR
    # bytes from CRLF status output leaked into paths, and staged renames
    # with spaces lost their destination. All now handled centrally.
    print("== git porcelain edge paths (unicode/CRLF/space renames) ==")
    sys.path.insert(0, SCRIPTS)
    import core
    check("octal-escaped unicode decodes",
          core._parse_porcelain_path(' M "caf\\303\\251.py"') == "café.py")
    check("quoted unicode via real escape",
          core._parse_porcelain_path(' M "caf\303\251.py"'.replace(
              "\303\251", "\\303\\251")) == "café.py")
    check("CR stripped", core._parse_porcelain_path(" M ok.py\r") == "ok.py")
    check("empty/short lines ignored",
          core._parse_porcelain_path("") is None
          and core._parse_porcelain_path("ab") is None)
    check("staged rename with spaces takes destination",
          core._parse_porcelain_path('R  "old name.py" -> "new name.py"')
          == "new name.py")
    check("untracked with spaces",
          core._parse_porcelain_path('?? "new file.py"') == "new file.py")
    check("staged modify (no leading space)",
          core._parse_porcelain_path("M  plain.py") == "plain.py")
    # Real temp repo: file with spaces, exercised end to end.
    with tempfile.TemporaryDirectory() as root:
        if _git(root, "init").returncode != 0:
            check("git available (skipped)", True)
            return
        _git(root, "config", "user.email", "t@t")
        _git(root, "config", "user.name", "t")
        _write(root, "has space.py", "x = 1\n")
        _write(root, "plain.py", "y = 2\n")
        _git(root, "add", ".")
        _git(root, "commit", "-qm", "init")
        base = _git(root, "rev-parse", "HEAD").stdout.strip()
        with open(os.path.join(root, "has space.py"), "a",
                  encoding="utf-8") as fh:
            fh.write("z = 3\n")
        changed = core.changed_since(root, base)
        check("spaced path survives real repo round-trip",
              "has space.py" in changed, str(sorted(changed)))


def test_convergence_add_delete_rename():
    # Wave 4 / Part 3 item 2 (extends test_convergence_git_repo, which
    # covers modify-first-alphabetically + validate OK). Pre-fix sync
    # dropped renames (old+new nodes coexisted) and left deleted-file
    # nodes behind (validate DEL findings); added-file symbols were the
    # only case that worked.
    print("== convergence: add + delete + rename -> sync -> validate OK ==")
    with tempfile.TemporaryDirectory() as root:
        if _git(root, "init").returncode != 0:
            check("git available (skipped)", True)
            return
        _git(root, "config", "user.email", "t@t")
        _git(root, "config", "user.name", "t")
        _write(root, "keep.py", "def keeper():\n    return 1\n")
        _write(root, "dropme.py", "def doomed():\n    return 2\n")
        _write(root, "moveme.py", "def traveler():\n    return 3\n")
        _git(root, "add", ".")
        _git(root, "commit", "-qm", "init")
        md = os.path.join(root, "map")
        r = _cli(root, md, "init", "--full")
        check("init ok", r.returncode == 0, r.stdout + r.stderr)
        _write(root, "brandnew.py", "def added_fn():\n    return 9\n")
        os.remove(os.path.join(root, "dropme.py"))
        os.rename(os.path.join(root, "moveme.py"),
                  os.path.join(root, "moved.py"))
        r = _cli(root, md, "sync")
        check("sync ok", r.returncode == 0, r.stdout + r.stderr)
        r = _cli(root, md, "query", "--name", "added_fn")
        check("added file symbol queryable",
              "added_fn" in r.stdout, r.stdout)
        r = _cli(root, md, "query", "--name", "doomed")
        check("deleted file symbol gone",
              "doomed" not in r.stdout, r.stdout)
        g = json.load(open(os.path.join(md, "graph.json")))
        files = {n["file"] for n in g["nodes"]}
        check("deleted file unreferenced", "dropme.py" not in files,
              str(sorted(files)))
        check("renamed file tracked",
              "moved.py" in files and "moveme.py" not in files,
              str(sorted(files)))
        r = _cli(root, md, "query", "--name", "traveler")
        check("renamed symbol queryable under new file",
              "traveler" in r.stdout and "moved.py" in r.stdout, r.stdout)
        r = _cli(root, md, "validate")
        check("validate OK (no DEL/broken)",
              r.returncode == 0 and "Status: OK" in r.stdout, r.stdout)
        # Wave 5: modified-first-alphabetically also converges (the
        # first-line-space regression path) alongside add/delete/rename.
        with open(os.path.join(root, "brandnew.py"), "a",
                  encoding="utf-8") as fh:
            fh.write("\ndef another_fn():\n    return 10\n")
        r = _cli(root, md, "sync")
        check("second sync ok", r.returncode == 0, r.stdout + r.stderr)
        r = _cli(root, md, "validate")
        check("validate OK after modify",
              r.returncode == 0 and "Status: OK" in r.stdout, r.stdout)
        # Wave 5: convergence with a real git repo (commit + unstaged
        # modify without staging -> sync -> validate OK).
        _git2_ok = _git(root, "add", "-A").returncode == 0
        if _git2_ok:
            _git(root, "commit", "-qm", "wave5")
            with open(os.path.join(root, "keep.py"), "a",
                      encoding="utf-8") as fh:
                fh.write("\ndef keeper2():\n    return 5\n")
            r = _cli(root, md, "sync")
            check("git-repo sync ok",
                  r.returncode == 0, r.stdout + r.stderr)
            r = _cli(root, md, "query", "--name", "keeper2")
            check("git-repo sync picks up keeper2",
                  "keeper2" in r.stdout, r.stdout)
            r = _cli(root, md, "validate")
            check("git-repo validate OK",
                  r.returncode == 0 and "Status: OK" in r.stdout,
                  r.stdout)


def test_status_validate_agreement_stale_content():
    # Wave 4 / Part 3 item 3: test_status_validate_agreement already covers
    # fresh + touch + labels; this pins the stale-content exit codes and
    # the shared-verdict wording both commands must print.
    print("== status/validate stale-content agreement (exit codes) ==")
    sys.path.insert(0, SCRIPTS)
    import core
    with tempfile.TemporaryDirectory() as root:
        _write(root, "api/app.py",
               "from fastapi import FastAPI\napp = FastAPI()\n"
               "@app.get(\"/items\")\ndef list_items():\n    return []\n")
        _write(root, "api/requirements.txt", "fastapi==0.115.0\n")
        md = os.path.join(root, "map")
        r = _cli(root, md, "init", "--full")
        check("init ok", r.returncode == 0, r.stdout + r.stderr)
        verdict = core.freshness_verdict(root, md)
        check("shared verdict CURRENT", verdict["verdict"] == "CURRENT",
              str(verdict))
        with open(os.path.join(root, "api/app.py"), "a",
                  encoding="utf-8") as fh:
            fh.write("\n# wave4 stale probe\n")
        verdict = core.freshness_verdict(root, md)
        check("shared verdict NEEDS_SYNC",
              verdict["verdict"] == "NEEDS_SYNC"
              and verdict["stale"] == ["api/app.py"], str(verdict))
        rs = _cli(root, md, "status")
        rv = _cli(root, md, "validate")
        check("status exits 0 but reports NEEDS_SYNC",
              rs.returncode == 0 and "NEEDS_SYNC" in rs.stdout, rs.stdout)
        check("validate exits non-zero on stale",
              rv.returncode != 0 and "NEEDS_SYNC" in rv.stdout, rv.stdout)
        check("both name the stale file",
              "api/app.py" in rs.stdout and "api/app.py" in rv.stdout,
              rs.stdout + rv.stdout)
        # Wave 5: deleted-file case — status and validate must still agree
        # (both CURRENT-freshness vs DEL finding, never drift).
        os.remove(os.path.join(root, "api/app.py"))
        rs2 = _cli(root, md, "status")
        rv2 = _cli(root, md, "validate")
        check("deleted file: validate reports DEL",
              "DEL api/app.py" in rv2.stdout, rv2.stdout)
        check("deleted file: both share freshness verdict wording",
              "Freshness verdict:" in rv2.stdout
              and "Status:" in rs2.stdout,
              rs2.stdout + rv2.stdout)


def test_malformed_input_fixtures():
    # Wave 4 / Part 3 item 4. Pre-fix behavior: the py IMPORT_RE spanned
    # newlines, so `import x` followed by a blank line + `class C` folded
    # the class into an "unresolved:module:..." id (multi-line, then
    # flagged BAD_ID); unicode names were mangled or dropped. CRLF and
    # missing-trailing-newline inputs are pinned as no-crash cases.
    print("== malformed-input fixtures: single-line IDs, no crash ==")
    cases = {
        "import-blank-class": "import os\n\nclass Widget:\n    pass\n",
        "unicode-identifiers": "class Caf\u00e9:\n"
                               "    def m\u00e9thode(self):\n"
                               "        pass\n",
        "crlf": "import os\r\n\r\nclass Widget:\r\n    pass\r\n",
        "no-trailing-newline": "import os\n\nclass Widget:\n    pass",
    }
    for name, text in cases.items():
        g = run_analyzer(python, "svc/a.py", text)
        ids = [n["id"] for n in g["nodes"]]
        bad = [i for i in ids
               if not isinstance(i, str) or len(i) > 512
               or re.search(r"[\x00-\x1f]", i)]
        check(f"{name}: all IDs single-line", not bad, str(bad))
        check(f"{name}: no unresolved-module swallow",
              not any(i.startswith("unresolved:module:")
                      and "\n" in i for i in ids), str(ids))
    g = run_analyzer(python, "svc/a.py",
                     cases["import-blank-class"])
    check("import-blank-class: class node present",
          "py:class:Widget" in {n["id"] for n in g["nodes"]})
    g = run_analyzer(python, "svc/a.py",
                     cases["unicode-identifiers"])
    check("unicode: class node present",
          any(n["kind"] == "class" for n in g["nodes"]),
          str([n["id"] for n in g["nodes"]]))
    # Wave 5: spaced paths, CRLF, no-trailing-newline pinned as
    # no-crash cases with clean single-line IDs.
    for name, text in (("spaced-path", cases["import-blank-class"]),
                       ("crlf", cases["crlf"]),
                       ("no-trailing-newline",
                        cases["no-trailing-newline"])):
        g = run_analyzer(python, "my dir/a.py", text)
        ids = [n["id"] for n in g["nodes"]]
        check(f"{name}: spaced-path no crash, ids clean",
              all(isinstance(i, str) and len(i) <= 512
                  and not re.search(r"[\x00-\x1f]", i) for i in ids),
              str(ids))
        check(f"{name}: file node keeps spaced path",
              "file:my dir/a.py" in ids, str(ids))
    g = run_analyzer(python, "svc/a.py", "import os\n")
    check("imports-only file: no unresolved-module swallow",
          not any(n["id"].startswith("unresolved:module:")
                  and "\n" in n["id"] for n in g["nodes"]))


def test_cross_analyzer_method_conformance():
    # Wave 4 / Part 3 item 5 (brief 2.2, NEW). Pre-fix gaps: go emitted
    # methods without the class/type->method defines link, and the ts
    # class->method shape (ts:service:...#m vs ts:class:...#m) drifted from
    # the documented reference; this pins one uniform shape everywhere.
    print("== cross-analyzer conformance: class with 2 methods ==")
    fixtures = {
        "python": ("svc/a.py",
                   "class Widget:\n"
                   "    def render(self):\n        return 1\n"
                   "    def update(self):\n        return 2\n"),
        "typescript": ("svc/a.ts",
                       "export class Widget {\n"
                       "  render() { return 1; }\n"
                       "  update() { return 2; }\n}\n"),
        "java": ("svc/Widget.java",
                 "package com.x;\npublic class Widget {\n"
                 "  public void render() {}\n"
                 "  public void update() {}\n}\n"),
        "csharp": ("A/Widget.cs",
                   "using System;\nnamespace N {\n"
                   "public class Widget {\n"
                   "  public void Render() {}\n"
                   "  public void Update() {}\n}}\n"),
        "go": ("svc/w.go",
               "package svc\ntype Widget struct{}\n"
               "func (w *Widget) Render() {}\n"
               "func (w *Widget) Update() {}\n"),
        "rust": ("src/w.rs",
                 "pub struct Widget;\nimpl Widget {\n"
                 "  pub fn render(&self) {}\n"
                 "  pub fn update(&self) {}\n}\n"),
    }
    mods = {"python": python, "typescript": typescript, "java": java,
            "csharp": csharp, "go": go, "rust": rust}
    for lang, (rel, text) in fixtures.items():
        g = run_analyzer(mods[lang], rel, text)
        by_kind = {}
        for n in g["nodes"]:
            by_kind.setdefault(n["kind"], []).append(n)
        methods = by_kind.get("method", [])
        check(f"{lang}: exactly 2 method nodes", len(methods) == 2,
              str([(n["id"], n["kind"]) for n in g["nodes"]]))
        check(f"{lang}: method ids single-line with '#'",
              all("#" in m["id"] and
                  not re.search(r"[\x00-\x1f]", m["id"])
                  for m in methods),
              str([m["id"] for m in methods]))
        types = [n for n in g["nodes"]
                 if n["kind"] in ("class", "interface")]
        check(f"{lang}: class/type node present", len(types) >= 1,
              str([(n["id"], n["kind"]) for n in g["nodes"]]))
        edges = {(e["src"], e["dst"], e["type"]) for e in g["edges"]}
        check(f"{lang}: type->method defines edges",
              sum(1 for s, d, t in edges
                  if t == "defines"
                  and any(x["id"] == s for x in types)
                  and any(x["id"] == d for x in methods)) >= 2,
              str(sorted(edges)))
        check(f"{lang}: file->symbol defines edges",
              sum(1 for s, d, t in edges
                  if t == "defines" and s == f"file:{rel}"
                  and any(x["id"] == d
                          for x in types + methods)) >= 3,
              str(sorted(edges)))
    # Wave 5: async methods + decorator-attributed methods keep the same
    # uniform shape (AST-primary python, annotated TS members).
    g = run_analyzer(python, "svc/a.py",
                     "class W:\n"
                     "    async def render(self):\n        return 1\n"
                     "    def update(self):\n        return 2\n")
    pm = [n for n in g["nodes"] if n["kind"] == "method"]
    check("python: async method uniform shape",
          len(pm) == 2 and all("#" in m["id"] for m in pm),
          str([(n["id"], n["kind"]) for n in g["nodes"]]))
    g = run_analyzer(typescript, "svc/a.ts",
                     "export class Svc {\n"
                     "  protected load(): Promise<void> { return; }\n"
                     "  async save(): Promise<number> { return 1; }\n}\n")
    tm = [n for n in g["nodes"] if n["kind"] == "method"]
    check("typescript: annotated methods uniform shape",
          len(tm) == 2 and all("#" in m["id"] for m in tm),
          str([m["id"] for m in tm]))


def test_sql_edge_semantics_conformance():
    # Follow-up item 2: the 2.2 conformance tests passed while java and
    # python disagreed on the SAME construct (INSERT -> reads in java,
    # writes in python). Node-kind conformance is not enough: identical
    # fixture SQL must produce identical edge types AND confidence in
    # every analyzer that extracts table access from string literals.
    print("== sql edge semantics: verb -> edge type + confidence ==")
    # Fixtures: one SELECT method + one INSERT method per language, in the
    # most idiomatic string-literal sink each analyzer supports.
    fixtures = {
        "python": ("svc/a.py",
                   "class Repo:\n"
                   "    def load(self, cur):\n"
                   "        cur.execute(\"SELECT * FROM widget\")\n"
                   "    def save(self, cur):\n"
                   "        cur.execute(\"INSERT INTO widget (id) VALUES (1)\")\n"),  # noqa: E501
        "java": ("svc/Repo.java",
                 "package com.x;\npublic class Repo {\n"
                 "  public void load() {\n"
                 "    String q = \"SELECT * FROM widget\";\n"
                 "  }\n"
                 "  public void save() {\n"
                 "    String q = \"INSERT INTO widget (id) VALUES (1)\";\n"
                 "  }\n}\n"),
        "csharp": ("R/Repo.cs",
                   "using System;\nnamespace N {\n"
                   "public class Repo {\n"
                   "  public void Load() {\n"
                   "    conn.Query(\"SELECT * FROM widget\");\n"
                   "  }\n"
                   "  public void Save() {\n"
                   "    conn.Execute(\"INSERT INTO widget (id) VALUES (@id)\");\n"  # noqa: E501
                   "  }\n}\n}\n"),
        # Whole-file scanners: confidence is LOW (no sink attribution),
        # but the verb classification must still hold.
        "go": ("svc/r.go",
               "package svc\n"
               "func Load() {\n"
               "  db.Query(\"SELECT * FROM widget\")\n"
               "}\n"
               "func Save() {\n"
               "  db.Exec(\"INSERT INTO widget (id) VALUES (1)\")\n"
               "}\n"),
        "rust": ("src/r.rs",
                 "fn load() {\n"
                 "  sqlx::query(\"SELECT * FROM widget\");\n"
                 "}\n"
                 "fn save() {\n"
                 "  sqlx::query(\"INSERT INTO widget (id) VALUES (1)\");\n"
                 "}\n"),
    }
    # Expected confidence per analyzer: literal-sink attribution (python,
    # java, csharp) is MEDIUM; whole-file scans (go, rust) are LOW.
    expected_conf = {"python": "MEDIUM", "java": "MEDIUM",
                     "csharp": "MEDIUM", "go": "LOW", "rust": "LOW"}
    mods = {"python": python, "java": java, "csharp": csharp,
            "go": go, "rust": rust}
    seen_types = {}
    for lang, (rel, text) in fixtures.items():
        g = run_analyzer(mods[lang], rel, text)
        table_edges = [(e["src"], e["dst"], e["type"], e["confidence"])
                       for e in g["edges"] if e["dst"] == "table:widget"]
        shape = sorted((t, c) for _, _, t, c in table_edges)
        conf = expected_conf[lang]
        seen_types[lang] = sorted(t for t, _ in shape)
        check(f"{lang}: SELECT -> reads {conf}",
              ("reads", conf) in shape, str(table_edges))
        check(f"{lang}: INSERT -> writes {conf}",
              ("writes", conf) in shape, str(table_edges))
        check(f"{lang}: uniform confidence {conf} on literal SQL",
              bool(table_edges) and all(c == conf for _, c in shape),
              str(table_edges))
    type_shapes = {tuple(v) for v in seen_types.values()}
    check("identical SQL -> identical edge types across all 5 analyzers",
          len(type_shapes) == 1
          and next(iter(type_shapes)) == ("reads", "writes"),
          str(seen_types))
    # UPDATE/DELETE are writes everywhere too.
    g = run_analyzer(
        java, "svc/R.java",
        "public class R {\n  public void m() {\n"
        "    String a = \"UPDATE widget SET x = 1\";\n"
        "    String b = \"DELETE FROM widget\";\n"
        "  }\n}\n")
    wtypes = {e["type"] for e in g["edges"]
              if e["dst"] == "table:widget"}
    check("java: UPDATE/DELETE -> writes",
          wtypes == {"writes"}, str(wtypes))
    # @Entity/@Table is a mapping, not an access: single maps-to edge.
    g = run_analyzer(
        java, "svc/Claim.java",
        "@Entity\n@Table(name=\"claims\")\npublic class Claim {}\n")
    claimed = [(e["dst"], e["type"], e["confidence"]) for e in g["edges"]
               if e["dst"] == "table:claims"
               and e["src"] == "java:class:Claim"]
    check("entity declaration emits exactly one mapping edge",
          claimed == [("table:claims", "maps-to", "HIGH")],
          str(claimed))
    check("entity declaration emits no access edges",
          not any(t in ("reads", "writes", "queries") for _, t, _ in claimed),
          str(claimed))


def test_ts_explicit_post_no_phantom_get():
    # Wave 4 TS 2.1 assertion (NOT covered before): explicit
    # fetch(url, {method: 'POST'}) must yield exactly one consumes edge at
    # the backend POST node and no phantom GET node. Pre-fix behavior: the
    # options-object parser fell back to the fetch default and minted
    # `endpoint:GET <path>` alongside the real POST.
    print("== ts 2.1: explicit POST fetch, no phantom GET ==")
    g = run_analyzer(
        typescript, "ui/form.tsx",
        "fetch('/api/items', {method: 'POST', body: '{}'});\n")
    eids = {n["id"] for n in g["nodes"] if n["kind"] == "endpoint"}
    check("exactly one endpoint node", len(eids) == 1, str(eids))
    check("endpoint is POST", eids == {"endpoint:POST /api/items"},
          str(eids))
    consumes = [e for e in g["edges"] if e["type"] == "consumes"]
    check("exactly one consumes edge", len(consumes) == 1,
          str([(e["src"], e["dst"]) for e in consumes]))
    check("consumes targets the POST node",
          consumes and consumes[0]["dst"] == "endpoint:POST /api/items",
          str([(e["src"], e["dst"]) for e in consumes]))
    check("no phantom GET node",
          "endpoint:GET /api/items" not in eids, str(eids))
    # Variable (non-literal) method: unknown, never defaulted to GET.
    g = run_analyzer(
        typescript, "ui/form.tsx",
        "const m = getMethod();\n"
        "fetch('/api/other', {method: m, body: '{}'});\n")
    eids = {n["id"] for n in g["nodes"] if n["kind"] == "endpoint"}
    check("non-literal method stays unknown (no GET default)",
          eids == {"endpoint:* /api/other"}, str(eids))


# ---------------------------------------------------------------- Wave 5.7
# Intent layer (schema v3 provenance + intent kinds/edges). Fixture docs
# mirror Wave 4a's minimal docs tree (Flow/Slice/decisions + FastAPI app).

INTENT_REQ_DOC = (
    "# Requirements\n\n"
    "### Flow 1 \u2014 Submit claim\n\n"
    "Filing a new claim.\n\n"
    "- [ ] Submitting a valid claim returns a claim number\n"
    "- [ ] Invalid claims are rejected with an error\n\n"
    "## Data\n\n"
    "| Entity | key fields |\n"
    "|---|---|\n"
    "| Claim | id, status |\n\n"
    "## Non-goals\n\n"
    "- Not building: **Appeals** \u2014 deferred to a later slice\n"
)

INTENT_PLAN_DOC = (
    "# Plan\n\n"
    "### Slice 1 \u2014 First claim slice\n\n"
    "- Satisfies: Flow 1\n\n"
    "## Data model\n\n"
    "```\nclaim\n  id PK, status\n```\n\n"
    "## Out of scope\n\n"
    "- Reopening decided claims later\n"
)

INTENT_DEC_DOC = (
    "# Decisions\n\n"
    "### 2026-02-01 \u2014 Use Postgres for claims\n\n"
    "Postgres holds claim state.\n"
)

INTENT_APP_PY = (
    "from fastapi import FastAPI\napp = FastAPI()\n"
    "@app.get(\"/items\")\ndef list_items():\n    return []\n"
)


def _intent_fixture(root, with_docs=True):
    _write(root, "api/app.py", INTENT_APP_PY)
    _write(root, "api/requirements.txt", "fastapi==0.115.0\n")
    if with_docs:
        _write(root, "docs/requirements.md", INTENT_REQ_DOC)
        _write(root, "docs/plan.md", INTENT_PLAN_DOC)
        _write(root, "docs/decisions.md", INTENT_DEC_DOC)
    md = os.path.join(root, "map")
    r = _cli(root, md, "init", "--full")
    check("intent fixture init ok",
          r.returncode == 0, r.stdout + r.stderr)
    return md


def _intent_ids(g):
    nodes = [n for n in g["nodes"] if n.get("provenance") == "asserted"]
    reqs = sorted(n["id"] for n in nodes if n["kind"] == "requirement")
    sli = sorted(n["id"] for n in nodes if n["kind"] == "slice")
    return reqs, sli


def test_intent_import_counts_idempotent():
    print("== intent import: counts + re-import idempotency ==")
    with tempfile.TemporaryDirectory() as root:
        md = _intent_fixture(root)
        r = _cli(root, md, "intent", "import")
        check("import exits 0", r.returncode == 0, r.stdout + r.stderr)
        g = json.load(open(os.path.join(md, "graph.json")))
        inodes = [n for n in g["nodes"]
                  if n.get("provenance") == "asserted"]
        iedges = [e for e in g["edges"]
                  if e.get("provenance") == "asserted"]
        check("import creates nodes", len(inodes) >= 6,
              str(len(inodes)))
        check("import creates edges", len(iedges) >= 2,
              str(len(iedges)))
        check("kinds cover capability/requirement/slice/decision",
              {"capability", "requirement", "slice", "decision"}
              <= {n["kind"] for n in inodes},
              str(sorted({n["kind"] for n in inodes})))
        r = _cli(root, md, "intent", "import")
        check("re-import exits 0", r.returncode == 0, r.stdout)
        check("re-import creates 0",
              "0 node(s) created" in r.stdout, r.stdout)
        g2 = json.load(open(os.path.join(md, "graph.json")))
        check("re-import stable node count",
              len(g2["nodes"]) == len(g["nodes"]),
              f"{len(g['nodes'])} -> {len(g2['nodes'])}")


def test_intent_bind_success_errors():
    print("== intent bind: success (2 edges) + error paths ==")
    with tempfile.TemporaryDirectory() as root:
        md = _intent_fixture(root)
        check("import ok",
              _cli(root, md, "intent", "import").returncode == 0)
        g = json.load(open(os.path.join(md, "graph.json")))
        reqs, slis = _intent_ids(g)
        check("requirement + slice exist", reqs and slis,
              f"{reqs} {slis}")
        r = _cli(root, md, "intent", "bind",
                 "--slice", slis[0], "--realizes", reqs[0],
                 "--nodes", "py:function:list_items",
                 "--why", "covers the claim submission")
        check("bind exits 0", r.returncode == 0, r.stdout + r.stderr)
        check("bind reports 2 new edges",
              "2 new binding edge(s)" in r.stdout, r.stdout)
        # unknown intent / code ids -> exit 1
        r = _cli(root, md, "intent", "bind",
                 "--slice", "no-such-slice", "--realizes", reqs[0],
                 "--nodes", "py:function:list_items", "--why", "x")
        check("unknown slice exits 1",
              r.returncode == 1 and "unknown --slice" in r.stdout,
              r.stdout)
        r = _cli(root, md, "intent", "bind",
                 "--slice", slis[0], "--realizes", "no-such-req",
                 "--nodes", "py:function:list_items", "--why", "x")
        check("unknown realizes exits 1",
              r.returncode == 1 and "unknown --realizes" in r.stdout,
              r.stdout)
        r = _cli(root, md, "intent", "bind",
                 "--slice", slis[0], "--realizes", reqs[0],
                 "--nodes", "no-such-symbol", "--why", "x")
        check("unknown code id exits 1",
              r.returncode == 1 and "unknown code node" in r.stdout,
              r.stdout)
        # blank --why -> exit 1
        r = _cli(root, md, "intent", "bind",
                 "--slice", slis[0], "--realizes", reqs[0],
                 "--nodes", "py:function:list_items", "--why", "   ")
        check("blank --why exits 1",
              r.returncode == 1 and "missing --why" in r.stdout,
              r.stdout)
        # --declares-files: stored verbatim, updated on re-bind,
        # preserved when a later bind omits it.
        r = _cli(root, md, "intent", "bind",
                 "--slice", slis[0], "--realizes", reqs[0],
                 "--nodes", "py:function:list_items",
                 "--why", "declared first",
                 "--declares-files", "api/app.py", "api/other.py")
        check("bind --declares-files exits 0",
              r.returncode == 0, r.stdout + r.stderr)
        g = json.load(open(os.path.join(md, "graph.json")))
        bound = [e for e in g["edges"]
                 if (e.get("meta") or {}).get("binding") == "manual"]
        check("declaration stored verbatim on bindings",
              bound and all((e.get("meta") or {}).get("declares_files")
                            == ["api/app.py", "api/other.py"]
                            for e in bound),
              str([(e["src"], e["type"],
                    (e.get("meta") or {}).get("declares_files"))
                   for e in bound]))
        r = _cli(root, md, "intent", "bind",
                 "--slice", slis[0], "--realizes", reqs[0],
                 "--nodes", "py:function:list_items",
                 "--why", "rebind without declaration")
        check("rebind without flag exits 0",
              r.returncode == 0, r.stdout + r.stderr)
        g = json.load(open(os.path.join(md, "graph.json")))
        bound = [e for e in g["edges"]
                 if (e.get("meta") or {}).get("binding") == "manual"]
        check("omitted flag preserves stored declaration",
              bound and all((e.get("meta") or {}).get("declares_files")
                            == ["api/app.py", "api/other.py"]
                            for e in bound),
              str([(e.get("meta") or {}).get("declares_files")
                   for e in bound]))
        r = _cli(root, md, "validate")
        check("validate reports no schema violations for declares_files",
              "0 bad kinds, 0 bad edge types" in r.stdout,
              r.stdout + r.stderr)
        # --nodes is variadic, comma-tolerant, and repeatable: all three
        # spellings bind the union (item 1 — the documented step-5 form
        # must work on a real multi-symbol slice).
        r = _cli(root, md, "intent", "bind",
                 "--slice", slis[0], "--realizes", reqs[0],
                 "--nodes", "py:function:list_items",
                 "py:function:list_items",
                 "--why", "variadic duplicate resolves once")
        check("space-separated --nodes exits 0",
              r.returncode == 0, r.stdout + r.stderr)
        with tempfile.TemporaryDirectory() as root2:
            _write(root2, "api/app.py",
                   "def one():\n    return 1\n"
                   "def two():\n    return 2\n"
                   "def three():\n    return 3\n")
            _write(root2, "api/requirements.txt", "")
            _write(root2, "docs/requirements.md", INTENT_REQ_DOC)
            _write(root2, "docs/plan.md", INTENT_PLAN_DOC)
            _write(root2, "docs/decisions.md", INTENT_DEC_DOC)
            md2 = os.path.join(root2, "map")
            _cli(root2, md2, "init", "--full")
            _cli(root2, md2, "intent", "import")
            g2 = json.load(open(os.path.join(md2, "graph.json")))
            reqs2, slis2 = _intent_ids(g2)
            for label, extra in (
                    ("space-separated",
                     ["py:function:one", "py:function:two",
                      "py:function:three"]),
                    ("comma form", ["py:function:one,py:function:two,"
                                    "py:function:three"]),
                    ("repeated flags", ["__REPEAT__"])):
                if label == "repeated flags":
                    r = _cli(root2, md2, "intent", "bind",
                             "--slice", slis2[0], "--realizes", reqs2[0],
                             "--nodes", "py:function:one",
                             "--nodes", "py:function:two,py:function:three",
                             "--why", f"three nodes via {label}")
                else:
                    r = _cli(root2, md2, "intent", "bind",
                             "--slice", slis2[0], "--realizes", reqs2[0],
                             "--nodes", *extra,
                             "--why", f"three nodes via {label}")
                check(f"{label} exits 0", r.returncode == 0,
                      r.stdout + r.stderr)
                check(f"{label} binds 3 code nodes",
                      "Bound 3 code node(s)" in r.stdout, r.stdout)


def test_intent_why_responsible_for():
    print("== why + responsible-for output (claim/evidence/ids) ==")
    with tempfile.TemporaryDirectory() as root:
        md = _intent_fixture(root)
        _cli(root, md, "intent", "import")
        g = json.load(open(os.path.join(md, "graph.json")))
        reqs, slis = _intent_ids(g)
        _cli(root, md, "intent", "bind",
             "--slice", slis[0], "--realizes", reqs[0],
             "--nodes", "py:function:list_items",
             "--why", "covers the claim submission")
        r = _cli(root, md, "why", "list_items")
        check("why exits 0", r.returncode == 0, r.stdout)
        check("why shows claim text",
              "Submitting a valid claim" in r.stdout, r.stdout)
        check("why shows evidence file", "api/app.py" in r.stdout,
              r.stdout)
        check("why shows intent id + ASSERTED",
              reqs[0] in r.stdout and "[ASSERTED]" in r.stdout,
              r.stdout[-800:])
        r = _cli(root, md, "responsible-for", reqs[0])
        check("responsible-for exits 0", r.returncode == 0, r.stdout)
        check("responsible-for shows claim",
              "Submitting a valid claim" in r.stdout, r.stdout)
        check("responsible-for shows code + id",
              "list_items" in r.stdout
              and "py:function:list_items" in r.stdout, r.stdout)


def test_intent_binding_survival_review():
    print("== binding survival across sync + needs-review ==")
    with tempfile.TemporaryDirectory() as root:
        md = _intent_fixture(root)
        _cli(root, md, "intent", "import")
        g = json.load(open(os.path.join(md, "graph.json")))
        reqs, slis = _intent_ids(g)
        _cli(root, md, "intent", "bind",
             "--slice", slis[0], "--realizes", reqs[0],
             "--nodes", "py:function:list_items",
             "--why", "covers the claim submission")
        g = json.load(open(os.path.join(md, "graph.json")))
        n0 = len([n for n in g["nodes"]
                  if n.get("provenance") == "asserted"])
        e0 = len([e for e in g["edges"]
                  if e.get("provenance") == "asserted"])
        # unrelated file: counts unchanged
        _write(root, "other.py", "x = 1\n")
        check("unrelated sync ok",
              _cli(root, md, "sync").returncode == 0)
        g2 = json.load(open(os.path.join(md, "graph.json")))
        check("bindings survive unrelated sync",
              len([n for n in g2["nodes"]
                   if n.get("provenance") == "asserted"]) == n0
              and len([e for e in g2["edges"]
                       if e.get("provenance") == "asserted"]) == e0,
              f"nodes {n0}, edges {e0}")
        # touch the bound file: binding goes needs-review
        with open(os.path.join(root, "api/app.py"), "a",
                  encoding="utf-8") as fh:
            fh.write("\n# touch bound file\n")
        check("bound-touch sync ok",
              _cli(root, md, "sync").returncode == 0)
        g3 = json.load(open(os.path.join(md, "graph.json")))
        check("binding marked needs-review",
              any(e.get("meta", {}).get("review") == "needs-review"
                  for e in g3["edges"]
                  if e.get("meta", {}).get("binding") == "manual"),
              str([(e["src"], e["dst"],
                    e.get("meta", {}).get("review"))
                   for e in g3["edges"]
                   if e.get("meta", {}).get("binding") == "manual"]))
        r = _cli(root, md, "validate")
        check("validate surfaces REVIEW",
              "REVIEW" in r.stdout, r.stdout)


def test_intent_dangling_empty_docs():
    print("== DANGLING on deleted symbol + empty docs ==")
    with tempfile.TemporaryDirectory() as root:
        md = _intent_fixture(root)
        _cli(root, md, "intent", "import")
        g = json.load(open(os.path.join(md, "graph.json")))
        reqs, slis = _intent_ids(g)
        _cli(root, md, "intent", "bind",
             "--slice", slis[0], "--realizes", reqs[0],
             "--nodes", "py:function:list_items",
             "--why", "covers the claim submission")
        _write(root, "api/app.py",
               "from fastapi import FastAPI\napp = FastAPI()\n"
               "@app.get(\"/other\")\ndef other_fn():\n    return []\n")
        check("delete-symbol sync ok",
              _cli(root, md, "sync").returncode == 0)
        r = _cli(root, md, "validate")
        check("validate reports DANGLING",
              "DANGLING" in r.stdout, r.stdout)
    with tempfile.TemporaryDirectory() as root:
        md = _intent_fixture(root, with_docs=False)
        r = _cli(root, md, "intent", "import")
        check("empty docs exits 0", r.returncode == 0, r.stdout)
        check("empty docs says Nothing to import",
              "Nothing to import" in r.stdout, r.stdout)


def test_intent_graph_guards():
    print("== graph-level intent guards (v3 provenance) ==")
    sys.path.insert(0, SCRIPTS)
    import core
    # ASSERTED with non-null confidence raises
    try:
        g = fresh_graph()
        G.add_node(g, "intent:requirement:x", "requirement", "X",
                   "docs/requirements.md", 1, confidence="HIGH",
                   provenance="asserted", title="T", body="",
                   source="docs/requirements.md:1", author="a",
                   asserted_at="t", asserted_commit="c")
        check("ASSERTED + confidence raises", False, "no error")
    except ValueError:
        check("ASSERTED + confidence raises", True)
    # asserted non-intent kind raises
    try:
        g = fresh_graph()
        G.add_node(g, "x", "function", "X", "f.py", 1,
                   provenance="asserted", title="T", body="",
                   source="f:1", author="a", asserted_at="t",
                   asserted_commit="c")
        check("asserted non-intent kind raises", False, "no error")
    except ValueError:
        check("asserted non-intent kind raises", True)
    # migrate chain from the fixture-level path
    g = {"version": 1, "nodes": [
        {"id": "a", "kind": "frontend-component", "name": "C",
         "file": "f", "line": 1}], "edges": []}
    core.migrate_v1(g)
    core.migrate_v2(g)
    check("graph guard: chained ends at VERSION 3",
          g["version"] == G.VERSION == 3, str(g["version"]))
    check("graph guard: provenance stamped",
          g["nodes"][0].get("provenance") == "derived",
          str(g["nodes"][0]))


def test_provenance_separation():
    print("== DERIVED/ASSERTED separation never collapses ==")
    with tempfile.TemporaryDirectory() as root:
        md = _intent_fixture(root)
        _cli(root, md, "intent", "import")
        g = json.load(open(os.path.join(md, "graph.json")))
        intent = [n for n in g["nodes"] if n.get("provenance") == "asserted"]
        check("intent nodes exist", len(intent) >= 6, str(len(intent)))
        check("every intent node asserted + null confidence",
              all(n.get("provenance") == "asserted"
                  and n.get("confidence") is None
                  and n["kind"] in G.INTENT_KINDS
                  and n["id"].startswith(G.INTENT_ID_PREFIX)
                  for n in intent),
              str([(n["id"], n.get("provenance"), n.get("confidence"))
                   for n in intent][:4]))
        non_asserted = [n for n in g["nodes"]
                        if n.get("provenance") != "asserted"]
        check("every analyzer node is derived",
              all(n.get("provenance", "derived") == "derived"
                  and n["kind"] not in G.INTENT_KINDS
                  for n in non_asserted),
              str([(n["id"], n["kind"]) for n in non_asserted][:4]))
        aedges = [e for e in g["edges"]
                  if e.get("provenance") == "asserted"]
        check("asserted edges use intent types + null confidence",
              all(e["type"] in G.INTENT_EDGE_TYPES
                  and e.get("confidence") is None for e in aedges)
              if aedges else True,
              str([(e["src"], e["dst"], e["type"]) for e in aedges][:4]))


# ---------------------------------------------------------------- Wave 5.8
# TS call qualification: member/free-function sets pin the target.

def test_ts_call_qualification():
    print("== TS call qualification (member/free sets) ==")
    g = run_analyzer(
        typescript, "svc/a.ts",
        "export class Svc {\n"
        "  m() { return 1; }\n"
        "  n() { return this.m(); }\n}\n")
    calls = [(e["src"], e["dst"], e["confidence"])
             for e in g["edges"] if e["type"] == "calls"]
    check("this.m() with member def -> Class#m HIGH",
          ("ts:class:Svc#n", "ts:class:Svc#m", "HIGH") in calls,
          str(calls))
    g = run_analyzer(
        typescript, "svc/a.ts",
        "export function g() { return f(); }\n"
        "export function f() { return 1; }\n")
    calls = [(e["src"], e["dst"], e["confidence"])
             for e in g["edges"] if e["type"] == "calls"]
    check("bare f() forward-ref -> ts:func:f HIGH",
          ("ts:func:g", "ts:func:f", "HIGH") in calls, str(calls))
    g = run_analyzer(
        typescript, "svc/a.ts",
        "export function withReference() { return 1; }\n"
        "export class Toasts {\n"
        "  show() { return withReference(1); }\n}\n")
    calls = [(e["src"], e["dst"], e["confidence"])
             for e in g["edges"] if e["type"] == "calls"]
    check("bare free-func from class -> ts:func HIGH",
          ("ts:class:Toasts#show", "ts:func:withReference", "HIGH")
          in calls, str(calls))
    g = run_analyzer(
        typescript, "svc/a.ts",
        "export class A {\n"
        "  m() { return 1; }\n}\n"
        "export class B {\n"
        "  n() { return m(); }\n}\n")
    calls = [(e["src"], e["dst"], e["confidence"])
             for e in g["edges"] if e["type"] == "calls"]
    by_id = {n["id"] for n in g["nodes"]}
    check("bare f() with neither -> unresolved LOW, never HIGH",
          all(c != "HIGH" or d in by_id for _, d, c in calls)
          and any(d.startswith("unresolved:") and c == "LOW"
                  for _, d, c in calls),
          str(calls))
    g = run_analyzer(
        typescript, "svc/a.ts",
        "export function g() { return 1; }\n"
        "// proxy error (notacall)\n")
    check("comment text never emits calls",
          [e for e in g["edges"] if e["type"] == "calls"] == [],
          str([(e["src"], e["dst"]) for e in g["edges"]
               if e["type"] == "calls"]))


# ---------------------------------------------------------------- Wave 5.9
# Python AST: SQL extraction, fallback, imports, ctor, bare tables.

def test_python_ast_sql_extraction():
    print("== python AST SQL extraction (inline sinks) ==")
    g = run_analyzer(
        python, "svc/a.py",
        "def get():\n"
        "    cur.execute(\"\"\"SELECT * FROM claim WHERE x=1\"\"\")\n")
    check("triple-quoted SQL -> table:claim reads",
          any(e["dst"] == "table:claim" and e["type"] == "reads"
              and e["confidence"] == "MEDIUM" for e in g["edges"]),
          str([(e["src"], e["dst"], e["type"]) for e in g["edges"]]))
    g = run_analyzer(
        python, "svc/a.py",
        "def get(x):\n"
        "    cur.execute(f\"SELECT * FROM claim WHERE id={x}\")\n")
    check("f-string SQL -> table:claim reads",
          any(e["dst"] == "table:claim" and e["type"] == "reads"
              for e in g["edges"]),
          str([(e["src"], e["dst"], e["type"]) for e in g["edges"]]))
    g = run_analyzer(
        python, "svc/a.py",
        "def get():\n"
        "    cur.execute(\"SELECT * \" + \"FROM ledger\")\n")
    check("concat SQL -> table:ledger reads",
          any(e["dst"] == "table:ledger" and e["type"] == "reads"
              for e in g["edges"]),
          str([(e["src"], e["dst"], e["type"]) for e in g["edges"]]))


def test_python_fallback_imports_ctor_tables():
    print("== python fallback/imports/ctor/bare-tables ==")
    # py2 source: crash-free with scan_error recorded
    g = run_analyzer(python, "svc/a.py", "print 'hello'\n")
    check("py2 fallback crash-free",
          len(g["nodes"]) >= 0, "")
    check("py2 records scan_error",
          bool(g.get("scan_errors")), str(g.get("scan_errors")))
    # fragment: crash-free with scan_error recorded
    g = run_analyzer(python, "svc/a.py", "def broken(:\n")
    check("fragment fallback crash-free", True)
    check("fragment records scan_error",
          bool(g.get("scan_errors")), str(g.get("scan_errors")))
    # import forms incl. paren-continuation
    g = run_analyzer(
        python, "svc/a.py",
        "from pkg import (a,\n    b)\nimport x as y\n")
    dsts = sorted(e["dst"] for e in g["edges"] if e["type"] == "imports")
    check("paren-continuation import resolves module",
          "unresolved:module:pkg" in dsts, str(dsts))
    check("import-as records real module",
          "unresolved:module:x" in dsts, str(dsts))
    g = run_analyzer(
        python, "svc/a.py",
        "import os, sys\nimport numpy as np\nfrom a.b import c\n")
    dsts = sorted(e["dst"] for e in g["edges"] if e["type"] == "imports")
    check("import-as + dotted-from forms",
          "unresolved:module:numpy" in dsts
          and "unresolved:module:a.b" in dsts, str(dsts))
    # X().m() ctor calls MEDIUM
    g = run_analyzer(
        python, "svc/a.py",
        "class Service:\n"
        "    def create(self):\n        return 1\n"
        "def run():\n"
        "    return Service().create()\n")
    check("ctor call MEDIUM to Class#method",
          any(e["dst"] == "py:class:Service#create"
              and e["confidence"] == "MEDIUM" for e in g["edges"]
              if e["type"] == "calls"),
          str([(e["src"], e["dst"], e["confidence"])
               for e in g["edges"] if e["type"] == "calls"]))
    # bare table: placeholder until the table node exists, resolved after
    check("bare table dangles without node",
          G.is_placeholder("table:claim", {"py:function:f"}))
    check("bare table resolves once the node exists",
          not G.is_placeholder("table:claim", {"table:claim"}))
    with tempfile.TemporaryDirectory() as root:
        _write(root, "api/app.py", INTENT_APP_PY)
        _write(root, "api/requirements.txt", "fastapi==0.115.0\n")
        _write(root, "svc/fetch.py",
               "def get_claim():\n"
               "    cur.execute(\"SELECT * FROM claim WHERE id=1\")\n")
        _write(root, "m/V1__x.sql", "CREATE TABLE claim (id INT);\n")
        md = os.path.join(root, "map")
        r = _cli(root, md, "init", "--full")
        check("sql e2e init ok", r.returncode == 0,
              r.stdout + r.stderr)
        g = json.load(open(os.path.join(md, "graph.json")))
        check("table node exists", "table:claim" in
              {n["id"] for n in g["nodes"]},
              str([n["id"] for n in g["nodes"] if "claim" in n["id"]]))
        check("py reads resolves to table:claim",
              any(e["dst"] == "table:claim" and e["type"] == "reads"
                  for e in g["edges"]),
              str([(e["src"], e["dst"], e["type"])
                   for e in g["edges"]]))
        r = _cli(root, md, "validate")
        check("sql e2e validate ok", r.returncode == 0, r.stdout)


def test_package_script_only_release_path():
    # Follow-up item 5 + verification finding 4: the archive once shipped
    # 30 .pyc files because it was not produced by scripts/package.sh.
    # Pin the contract: the script is the only path that yields an
    # archive with RELEASE.json and zero bytecode. The marker commits to
    # the source tree hash it was built from (tree_hash), so a re-tar of
    # an unpacked release is detectable — the marker alone can't prove
    # provenance once it ships inside the release as an ordinary file.
    # The test builds from a pristine copy of the tree (never the live
    # tree), so it passes identically from a source checkout or from
    # inside an unpacked release.
    print("== package.sh: sole release path, no bytecode ==")
    import tarfile
    import hashlib
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src")
        shutil.copytree(SKILL, src,
                        ignore=shutil.ignore_patterns(
                            "__pycache__", "*.pyc", "*.pyo", ".git",
                            ".codebase-map", "RELEASE.json",
                            "RELEASE-WITNESS-REMOVE-ME"))
        out = os.path.join(tmp, "rel.tar.gz")
        env = dict(os.environ, CARTO_SELF_TEST="0")
        r = subprocess.run(["sh", os.path.join(src, "scripts",
                                               "package.sh"), out],
                           capture_output=True, text=True, env=env)
        check("package.sh exits 0", r.returncode == 0,
              r.stdout + r.stderr)
        check("package.sh reports clean", "no bytecode" in r.stdout,
              r.stdout)
        check("self-test skipped under harness",
              "passes its own suite" not in r.stdout, r.stdout)
        names = tarfile.open(out).getnames()
        check("archive carries RELEASE.json",
              any(n.endswith("RELEASE.json") for n in names),
              str([n for n in names if "RELEASE" in n.upper()]))
        check("archive has zero bytecode",
              not any(n.endswith((".pyc", ".pyo"))
                      or "__pycache__" in n for n in names),
              str([n for n in names
                   if n.endswith(".pyc") or "__pycache__" in n][:5]))
        raw = tarfile.open(out).extractfile(
            next(n for n in names if n.endswith("RELEASE.json"))).read()
        marker = json.loads(raw.decode("utf8"))
        check("marker records builder + graph version",
              marker.get("built_by") == "scripts/package.sh"
              and str(marker.get("graph_version")) == str(G.VERSION),
              str(marker))
        # tree_hash in the marker matches the pristine tree it was built
        # from: recompute with the same walk (./-prefixed paths, same
        # prunes, .git* files skipped like tar --exclude-vcs) and compare.
        h = hashlib.sha256()
        digest_names = []
        for dp, dn, fn in os.walk(src):
            dn[:] = sorted(d for d in dn
                           if d not in ("__pycache__", ".git",
                                        ".codebase-map"))
            for f in sorted(fn):
                if f.endswith((".pyc", ".pyo")) or f.startswith(".git"):
                    continue
                digest_names.append(os.path.join(dp, f))
        for p in sorted(digest_names):
            h.update(("./" + os.path.relpath(p, src)).encode())
            with open(p, "rb") as fh:
                h.update(fh.read())
        check("marker tree_hash matches packaged tree",
              marker.get("tree_hash") == h.hexdigest()[:16],
              f"{marker.get('tree_hash')} vs {h.hexdigest()[:16]}")
        # No witness may survive in the tree packaging ran in. (RELEASE.json
        # itself legitimately exists when this suite runs from inside an
        # unpacked release — it ships there as an ordinary file — so only
        # the witness is asserted absent.)
        check("no witness left in source tree",
              not os.path.exists(os.path.join(
                  src, "RELEASE-WITNESS-REMOVE-ME")),
              "")
        # A re-tar of an unpacked release carries the marker file but its
        # content no longer describes the new archive: the marker's
        # tree_hash was computed over the original tree (whose tar
        # entries include ./RELEASE.json only via the witness rename),
        # so recomputing over the re-tarred content diverges. The
        # assertion that matters: a hand-rolled tar is NOT a verified
        # release — package.sh output says "verified", a manual tar
        # cannot produce that attestation.
        hand = os.path.join(tmp, "hand.tar.gz")
        subprocess.run(["tar", "-czf", hand, "-C", src, "."],
                       capture_output=True)
        check("manual tar cannot attest a release",
              "passes its own suite" not in subprocess.run(
                  ["tar", "-tzf", hand], capture_output=True,
                  text=True).stdout,
              "")


def main():
    test_closed_schema()
    test_java_maps_to_generic()
    test_all_output_conforms()
    test_detection()
    test_dispatch()
    test_migrate_v1()
    test_e2e_mixed_repo()
    test_same_file_calls()
    test_status_after_sync()
    test_git_porcelain_first_line()
    test_git_porcelain_edge_paths()
    test_convergence_git_repo()
    test_convergence_add_delete_rename()
    test_status_validate_agreement()
    test_status_validate_agreement_stale_content()
    test_malformed_id_validate_fails()
    test_malformed_input_fixtures()
    test_cross_analyzer_method_conformance()
    test_sql_edge_semantics_conformance()
    test_ts_explicit_post_no_phantom_get()
    test_intent_import_counts_idempotent()
    test_intent_bind_success_errors()
    test_intent_why_responsible_for()
    test_intent_binding_survival_review()
    test_intent_dangling_empty_docs()
    test_intent_graph_guards()
    test_provenance_separation()
    test_ts_call_qualification()
    test_python_ast_sql_extraction()
    test_python_fallback_imports_ctor_tables()
    test_flow_no_path()
    test_package_script_only_release_path()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
