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
    test_ts_explicit_post_no_phantom_get()
    test_flow_no_path()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
