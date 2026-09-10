"""Core engine: file walking, dispatch, resolve, views, sync, CLI.

Technology-agnostic: all language/framework/database knowledge lives in
`analyzers/`. This module owns the generic graph, detection record,
incremental sync, validation, and derived Markdown views.
"""

import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analyzers import graph as G
from analyzers import analyzers_for, detect_tech, CONFIG_FILENAMES
from analyzers.context import ScanContext

VERSION = G.VERSION
DEFAULT_MAP_DIR = ".codebase-map"

SKIP_DIRS = {
    ".git", "node_modules", "dist", "target", "build", "out", ".pylibs",
    "test-results", "playwright-report", "blob-report", "uploads",
    "__pycache__", ".venv", "venv", ".idea", ".vscode", "coverage",
    ".next", ".angular", "shots", "preview-page", ".codebase-map",
    ".dsh",
}
SKIP_FILE_SUFFIX = (".min.js", ".min.css", ".map", ".lock", "-lock.json")
SECRET_FILES = {".env"}
UNSCANNABLE_PREFIXES = (".dsh/",)  # internal tooling: never enters the map
DEFAULT_MAX_FILE_BYTES = 1024 * 1024  # oversized files are skipped, not parsed
MAX_ID_LEN = 512  # node/edge IDs beyond this are schema violations


# Claimable source/config extensions: anything an analyzer may want.
# Markdown/prose files (.md, .http) are documentation, not code: the fallback
# analyzer must not claim them, and "no analyzer claimed" is the correct
# outcome (not a scan error) — see scan_file below.
SOURCE_EXTS = {
    ".java", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py",
    ".go", ".rs", ".cs", ".rb", ".php", ".swift", ".kt", ".kts",
    ".scala", ".dart", ".ex", ".exs", ".cpp", ".cc", ".cxx", ".h",
    ".hpp", ".c", ".vue", ".svelte", ".sql", ".properties", ".yml",
    ".yaml", ".toml", ".ini", ".cfg", ".json", ".xml", ".html",
    ".css", ".md", ".http", ".proto", ".graphql", ".gql",
}


_SKILL_ROOT_BASENAMES = set()  # reserved; prefixes resolved via _skill_rel_prefixes


def _skill_rel_prefixes(root):
    """Path prefix(es) of this skill's own install, relative to root.

    Resolved at runtime from __file__ so the exclusion follows the skill
    wherever it is installed (.dsh/, .claude/skills/, ...). Returns a set
    of 'prefix/' strings to skip."""
    prefixes = {".dsh/"}  # default install keeps working even off-repo
    try:
        skill_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), ".."))
        rel = os.path.relpath(skill_root, os.path.abspath(root))
    except ValueError:
        return prefixes
    if rel != "." and not rel.startswith(".." + os.sep) and rel != "..":
        prefixes.add(rel.replace(os.sep, "/").rstrip("/") + "/")
    return prefixes


def _load_map_config(root, map_dir):
    """Optional JSON config at <map_dir>/config.json: {max_file_bytes,
    ignore[...]}. Missing/unreadable -> {}."""
    try:
        with open(os.path.join(root, map_dir, "config.json"),
                  encoding="utf-8") as fh:
            cfg = json.load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def _max_file_bytes(root, map_dir):
    cfg = _load_map_config(root, map_dir)
    try:
        return int(cfg.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES))
    except (TypeError, ValueError):
        return DEFAULT_MAX_FILE_BYTES


def _map_rel_base(root, map_dir):
    """First path segment of the map dir relative to root (or None if the
    map dir lives outside the scanned root)."""
    try:
        rel = os.path.relpath(os.path.abspath(os.path.join(root, map_dir)),
                              os.path.abspath(root))
    except ValueError:
        return None
    if rel == "." or rel.startswith(".." + os.sep) or rel == "..":
        return None
    return rel.replace(os.sep, "/").split("/")[0]


def load_gitignore_basenames(root):
    extra = set()
    try:
        with open(os.path.join(root, ".gitignore"), encoding="utf-8",
                  errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("!"):
                    continue
                line = line.lstrip("/").rstrip("/")
                if "/" not in line and "*" not in line:
                    extra.add(line)
    except OSError:
        pass
    return extra


def _is_ignored_by_config(rel, cfg):
    ignore = cfg.get("ignore", [])
    if not isinstance(ignore, list):
        return False
    for pat in ignore:
        if not isinstance(pat, str) or not pat:
            continue
        if pat.endswith("/"):
            if rel.startswith(pat) or rel + "/" == pat:
                return True
        elif "*" in pat:
            if fnmatch.fnmatch(rel, pat):
                return True
        elif rel == pat:
            return True
    return False


def iter_repo_files(root, extra_skip, map_dir=DEFAULT_MAP_DIR):
    skip_dirs = SKIP_DIRS | extra_skip
    # Only skip the map dir when it is INSIDE the scanned root; an absolute
    # --map-dir outside the root (used by tests) must not exclude everything.
    map_abs = os.path.abspath(os.path.join(root, map_dir))
    root_abs = os.path.abspath(root)
    skip_map = os.path.commonpath([root_abs, map_abs]) == root_abs
    map_base = _map_rel_base(root, map_dir) if skip_map else None
    cfg = _load_map_config(root, map_dir)
    try:
        max_bytes = int(cfg.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES))
    except (TypeError, ValueError):
        max_bytes = DEFAULT_MAX_FILE_BYTES
    skip_prefixes = _skill_rel_prefixes(root)
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in skip_dirs
            and (map_base is None or d != map_base)
            and not d.startswith(".shots")
        )
        for fn in sorted(filenames):
            if fn in SECRET_FILES:
                continue
            if fn.endswith(SKIP_FILE_SUFFIX):
                continue
            ap = os.path.join(dirpath, fn)
            rel = os.path.relpath(ap, root).replace(os.sep, "/")
            if rel.startswith(tuple(skip_prefixes)):
                continue
            if _is_ignored_by_config(rel, cfg):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext not in SOURCE_EXTS and fn not in _config_filenames():
                continue
            try:
                if os.path.getsize(ap) > max_bytes:
                    continue
            except OSError:
                continue
            results.append((rel, ap))
    return results


def _config_filenames():
    try:
        return CONFIG_FILENAMES
    except Exception:
        return set()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


# --------------------------------------------------------------------------
# Git helpers
# --------------------------------------------------------------------------

def git(root, *args):
    try:
        out = subprocess.run(["git", "-C", root] + list(args),
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def git_raw(root, *args):
    """Like git() but preserves leading whitespace (rstrip newlines only).

    Required for `status --porcelain`, whose first column may be a space
    (' M file' = unstaged modification); git().strip() eats that space on
    the first line and shifts the path slice by one."""
    try:
        out = subprocess.run(["git", "-C", root] + list(args),
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.rstrip("\n").rstrip("\r")


def head_commit(root):
    return git(root, "rev-parse", "HEAD")


def _parse_porcelain_path(line):
    """Parse one `git status --porcelain` line to a repo-relative path.

    Format: two fixed status columns XY, a space, then the path. Renames
    look like 'R  old -> new' (take the destination). Quoted paths
    (spaces/unicode escapes) are unquoted and unicode-decoded."""
    if len(line) < 4:
        return None
    xy = line[:2]
    if line[2] != " ":
        return None
    path = line[3:]
    if xy[0] == "R" or " -> " in path:
        path = path.rsplit(" -> ", 1)[1].strip()
    path = path.strip()
    if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
        inner = path[1:-1]
        try:
            path = inner.encode("utf-8").decode("unicode_escape").encode(
                "latin-1").decode("utf-8")
        except (ValueError, UnicodeError):
            path = inner
    return path or None


def changed_since(root, base, map_dir=DEFAULT_MAP_DIR):
    root_abs = os.path.abspath(root)
    # Git reports paths relative to the REPO top level, which may differ
    # from root (e.g. scanning a subdirectory/fixture). Normalize both to
    # root-relative so sync/status compare like with like.
    prefix = None
    toplevel = git(root_abs, "rev-parse", "--show-toplevel")
    if toplevel:
        try:
            prefix = os.path.relpath(root_abs,
                                     os.path.abspath(toplevel)).replace(
                                         os.sep, "/")
            if prefix == ".":
                prefix = None
        except ValueError:
            prefix = None
    changed = set()
    if base:
        diff = git(root, "diff", "--name-only", f"{base}..HEAD")
        if diff:
            changed.update(diff.splitlines())
    status = git_raw(root, "status", "--porcelain")
    if status:
        for line in status.splitlines():
            path = _parse_porcelain_path(line)
            if path:
                changed.add(path)
    normalized = set()
    for c in changed:
        if prefix and c.startswith(prefix + "/"):
            c = c[len(prefix) + 1:]
        elif prefix:
            continue  # change outside the scanned root
        normalized.add(c)
    _mb = _map_rel_base(root, map_dir)
    skip_prefixes = _skill_rel_prefixes(root)
    return {c for c in normalized
            if c and (_mb is None or not c.startswith(_mb + "/"))
            and not c.startswith(tuple(skip_prefixes))}


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")


# --------------------------------------------------------------------------
# Scan dispatch
# --------------------------------------------------------------------------

def scan_file(graph, rel, ap, commit):
    if os.path.basename(ap) == ".env":
        return
    try:
        text = read_text(ap)
    except OSError:
        return
    ctx = ScanContext(graph, G.add_node, G.add_edge, rel, commit)
    try:
        claimed = analyzers_for(rel, text)
    except Exception as exc:
        graph.setdefault("scan_errors", []).append(f"{rel}: dispatch: {exc}")
        return
    ran = False
    for analyzer in claimed:
        try:
            analyzer.scan(ctx, rel, text)
            ran = True
        except Exception as exc:
            graph.setdefault("scan_errors", []).append(
                f"{rel} [{analyzer.NAME}]: {exc}")
    if not ran and not claimed:
        # No analyzer claims this file (e.g. prose docs): correct outcome,
        # only worth noting at high volume.
        graph.setdefault("unclaimed_files", []).append(rel)


def full_scan(root, commit, map_dir=DEFAULT_MAP_DIR):
    graph = G.new_graph(os.path.abspath(root), commit, utcnow())
    extra = load_gitignore_basenames(root)
    files = iter_repo_files(root, extra, map_dir)
    hashes = {}
    for rel, ap in files:
        try:
            hashes[rel] = sha256_file(ap)
        except OSError:
            continue
        scan_file(graph, rel, ap, commit)
    # detection record (evidence-based, stored in the graph)
    try:
        by_rel = {rel: ap for rel, ap in files}
        graph["detection"] = detect_tech(
            files, lambda r: _safe_read(by_rel.get(r)))
    except Exception as exc:
        graph["detection"] = {"error": str(exc)}
    resolve_references(graph)
    compute_flow_candidates(graph)
    graph["init_commit"] = commit
    graph["last_sync_commit"] = commit
    graph["last_sync_time"] = utcnow()
    return graph, hashes


def _safe_read(ap):
    if not ap:
        return ""
    try:
        with open(ap, encoding="utf-8", errors="replace") as fh:
            return fh.read(40000)
    except OSError:
        return ""


# --------------------------------------------------------------------------
# Generic reference resolution (language-independent symbol tables)
# --------------------------------------------------------------------------

def _simple_class(nid):
    # java:class:com.Foo.Bar / cs:class:N.Bar / go:type:p.T / py:class:T
    # -> simple type name
    tail = nid.split(":")[-1]
    return tail.split(".")[-1].split("#")[0]


def resolve_references(graph):
    def dedupe(ids):
        seen = set()
        return [i for i in ids if not (i in seen or seen.add(i))]
    by_class = {}
    by_method = {}
    by_component = {}
    by_endpoint_path = {}
    for n in graph["nodes"]:
        nid = n["id"]
        if n["kind"] in ("class", "interface", "controller", "service",
                         "handler", "configuration"):
            if nid.split(":")[0] in ("java", "cs", "go", "py", "rs",
                                     "ts", "fallback") or ":" in nid:
                by_class.setdefault(_simple_class(nid), []).append(nid)
                by_class.setdefault(n["name"], []).append(nid)
        elif n["kind"] in ("method", "function"):
            if "#" in nid:
                head, meth = nid.rsplit("#", 1)
                by_method.setdefault((_simple_class(head), meth),
                                    []).append(nid)
                by_method.setdefault((n["name"].split(".")[-1], meth),
                                    []).append(nid)
        elif n["kind"] in ("component", "service", "guard", "interceptor",
                           "handler"):
            if nid.startswith(("ts:", "py:", "go:", "rs:", "cs:")):
                by_component.setdefault(n["name"], []).append(nid)
        elif n["kind"] == "endpoint":
            # any-method index: path -> known-method endpoint node ids.
            # The TS analyzer emits `endpoint:* <path>` LOW stubs for
            # genuinely-unknown methods; the edge branch below rewrites
            # those to the single known-method endpoint for the same path.
            if nid.startswith("endpoint:"):
                method, sep, epath = nid[len("endpoint:"):].partition(" ")
                if sep and method != "*" and epath:
                    by_endpoint_path.setdefault(epath, []).append(nid)
    kept_unresolved = []
    for e in graph["edges"]:
        dst = e["dst"]
        if dst.startswith("unresolved:method:"):
            key = dst[len("unresolved:method:"):]
            cls, _, meth = key.partition("#")
            targets = by_method.get((cls, meth), [])
            if len(targets) == 1:
                e["dst"] = targets[0]
                e["confidence"] = "MEDIUM"
                e["meta"]["resolved"] = "name-match"
            elif cls in by_class:
                e["dst"] = by_class[cls][0]
                e["confidence"] = "MEDIUM"
                e["meta"]["resolved"] = "class-member"
            else:
                e["confidence"] = "LOW"
                kept_unresolved.append(dst)
        elif dst.startswith("unresolved:class:"):
            cls = dst[len("unresolved:class:"):]
            targets = dedupe(by_class.get(cls, []))
            if len(targets) == 1:
                e["dst"] = targets[0]
                e["confidence"] = "MEDIUM"
                e["meta"]["resolved"] = "name-match"
            else:
                e["confidence"] = "LOW"
                kept_unresolved.append(dst)
        elif dst.startswith("unresolved:component:"):
            comp = dst[len("unresolved:component:"):]
            targets = by_component.get(comp, [])
            if len(targets) == 1:
                e["dst"] = targets[0]
                e["confidence"] = "HIGH"
            else:
                e["confidence"] = "LOW"
                kept_unresolved.append(dst)
        elif dst.startswith("unresolved:guard:"):
            comp = dst[len("unresolved:guard:"):]
            targets = by_component.get(comp, [])
            if len(targets) == 1:
                e["dst"] = targets[0]
                e["confidence"] = "MEDIUM"
                e["meta"]["resolved"] = "name-match"
            else:
                e["confidence"] = "LOW"
                kept_unresolved.append(dst)
        elif dst.startswith("unresolved:handler:"):
            h = dst[len("unresolved:handler:"):].split(".")[-1]
            targets = by_component.get(h, [])
            if len(targets) == 1:
                e["dst"] = targets[0]
                e["confidence"] = "MEDIUM"
                e["meta"]["resolved"] = "name-match"
            else:
                e["confidence"] = "LOW"
                kept_unresolved.append(dst)
        elif dst.startswith("endpoint:* "):
            epath = dst[len("endpoint:* "):]
            targets = dedupe(by_endpoint_path.get(epath, []))
            if len(targets) == 1:
                e["dst"] = targets[0]
                e["confidence"] = "LOW"
                e["meta"]["resolved"] = "any-method-match"
            # zero or 2+ known methods: leave the stub as-is (ambiguous)
    graph["unresolved"] = sorted(set(kept_unresolved))


# --------------------------------------------------------------------------
# Generic flow skeletons (language-independent walk)
# --------------------------------------------------------------------------

# Entry kinds that seed flows: any boundary crossing, not just HTTP.
FLOW_SEEDS = ("endpoint", "route", "queue", "topic", "event", "job",
              "schedule")
FLOW_WALK_TYPES = ("calls", "reads", "writes", "queries", "invokes",
                   "injects", "consumes", "publishes", "transforms",
                   "handled-by", "navigates", "guarded-by")


def compute_flow_candidates(graph):
    by_id = {n["id"]: n for n in graph["nodes"]}
    out = {}
    for e in graph["edges"]:
        out.setdefault(e["src"], []).append(e)
    flows = []
    for n in graph["nodes"]:
        if n["kind"] == "endpoint":
            handler = None
            for e in out.get(n["id"], []):
                if e["type"] == "handled-by":
                    handler = e["dst"]
            chain = [n["id"]]
            seen = set(chain)
            if handler:
                chain.append(handler)
                seen.add(handler)
                _walk(out, handler, chain, seen)
            flows.append({"seed": n["id"], "kind": "endpoint",
                          "chain": chain})
        elif n["kind"] in ("queue", "topic", "event", "job", "route"):
            chain = [n["id"]]
            seen = set(chain)
            _walk(out, n["id"], chain, seen, depth=4)
            flows.append({"seed": n["id"], "kind": n["kind"],
                          "chain": chain})
    # back-compat: older views read flow["endpoint"]
    for f in flows:
        f.setdefault("endpoint", f["seed"])
    graph["flow_candidates"] = flows


def _walk(out, start, chain, seen, depth=3):
    frontier = [start]
    for _ in range(depth):
        nxt = []
        for fid in frontier:
            for e in out.get(fid, []):
                if e["type"] in FLOW_WALK_TYPES:
                    if e["dst"] not in seen and not e["dst"].startswith(
                            "unresolved:") and not e["dst"].startswith(
                            "stereotype:"):
                        chain.append(e["dst"])
                        seen.add(e["dst"])
                        nxt.append(e["dst"])
                        if len(chain) >= 8:
                            break
            if len(chain) >= 8:
                break
        frontier = nxt
        if not frontier:
            break


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def map_paths(root, map_dir):
    md = os.path.join(root, map_dir)
    return {
        "dir": md,
        "graph": os.path.join(md, "graph.json"),
        "state": os.path.join(md, "state", "sync-state.json"),
        "hashes": os.path.join(md, "state", "file-hashes.json"),
        "changes": os.path.join(md, "changes"),
        "flows": os.path.join(md, "business-flows"),
        "detection": os.path.join(md, "stack.md"),
    }


def save_graph(paths, graph):
    os.makedirs(os.path.dirname(paths["graph"]), exist_ok=True)
    tmp = paths["graph"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(graph, fh, indent=1, sort_keys=True)
    os.replace(tmp, paths["graph"])


def load_graph(paths):
    with open(paths["graph"], encoding="utf-8") as fh:
        g = json.load(fh)
    if g.get("version") != VERSION:
        raise SystemExit(
            f"graph.json version {g.get('version')} != tool version "
            f"{VERSION}; run `init --full` to rebuild.")
    return g


def save_state(paths, graph, hashes):
    os.makedirs(os.path.dirname(paths["state"]), exist_ok=True)
    state = {
        "version": VERSION,
        "last_sync_commit": graph["last_sync_commit"],
        "last_sync_time": graph["last_sync_time"],
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
    }
    with open(paths["state"], "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    with open(paths["hashes"], "w", encoding="utf-8") as fh:
        json.dump(hashes, fh, indent=1, sort_keys=True)


def load_hashes(paths):
    try:
        with open(paths["hashes"], encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------------------
# Derived views (deterministic; generic wording throughout)
# --------------------------------------------------------------------------

GENERATED = "<!-- generated by living-codebase-cartographer: do not edit -->\n\n"

DETECTION_ORDER = ("languages", "frameworks", "databases",
                   "infrastructure", "unsupported")


def cap_table(rows, limit=60):
    if len(rows) > limit:
        return rows[:limit] + [
            [f"…and {len(rows) - limit} more (see graph.json)"]
            + [""] * (len(rows[0]) - 1)]
    return rows


def md_cell(value):
    """Sanitize one value interpolated into generated Markdown tables/views:
    pipes would split cells, newlines would split rows."""
    return str(value).replace("|", "\\|").replace("\r", " ").replace(
        "\n", " ")


def md_table(headers, rows):
    out = ["| " + " | ".join(md_cell(h) for h in headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(md_cell(c) for c in r) + " |")
    return "\n".join(out) + "\n"


def kinds(graph):
    counts = {}
    for n in graph["nodes"]:
        counts[n["kind"]] = counts.get(n["kind"], 0) + 1
    return counts


def edges_by_type(graph):
    counts = {}
    for e in graph["edges"]:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    return counts


def _write(path, content):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def detection_markdown(detection):
    out = ["# Detected Stack\n",
           "Evidence-based technology detection (manifests, config contents, "
           "imports, file census). Confidence HIGH = manifest + content "
           "agreement; MEDIUM = manifest or strong content signal; LOW = "
           "file-presence only. Never treat LOW as confirmed.\n"]
    empty = True
    for section in DETECTION_ORDER:
        items = (detection or {}).get(section, [])
        if not items:
            continue
        empty = False
        out.append(f"## {section.capitalize()}\n")
        for it in items:
            extra = f" — {it['reason']}" if it.get("reason") else ""
            out.append(f"- `{it['name']}` ({it.get('confidence', '?')})"
                       f"{extra}\n")
        out.append("\n")
    if empty:
        out.append("_No technologies detected._\n")
    return "".join(out)


def write_views(root, map_dir, graph):
    md = os.path.join(root, map_dir)
    os.makedirs(os.path.join(md, "business-flows"), exist_ok=True)
    os.makedirs(os.path.join(md, "decisions"), exist_ok=True)
    os.makedirs(os.path.join(md, "changes"), exist_ok=True)
    os.makedirs(os.path.join(md, "state"), exist_ok=True)
    commit = graph.get("last_sync_commit") or "unknown"
    kc = kinds(graph)
    ec = edges_by_type(graph)
    by_id = {n["id"]: n for n in graph["nodes"]}

    _write(os.path.join(md, "stack.md"),
           GENERATED + detection_markdown(graph.get("detection")))

    _write(os.path.join(md, "README.md"),
           GENERATED + "# Codebase Map\n\n"
           "Technology-agnostic living map of this repository. "
           f"Source of truth: `graph.json` ({len(graph['nodes'])} nodes, "
           f"{len(graph['edges'])} edges). Last sync: "
           f"{graph.get('last_sync_time')} "
           f"(commit `{commit[:12] if len(commit) > 12 else commit}`).\n\n"
           "Detected stack: `stack.md`. "
           "Markdown files here are **derived views** — regenerate with "
           "`cartographer.py sync`. `architecture.md` and "
           "`business-flows/*.md` (except `_candidates.md`) are "
           "agent-curated and never overwritten.\n\n"
           "Confidence: HIGH = direct declaration evidence · MEDIUM = "
           "resolved reference · LOW/UNKNOWN = heuristic, do not treat as "
           "confirmed.\n")

    # modules.md — packages/namespaces present in meta
    mods = {}
    for n in graph["nodes"]:
        if n["kind"] in ("controller", "service", "handler", "class",
                         "interface", "component", "configuration",
                         "function", "method"):
            pkg = n.get("meta", {}).get("package") or \
                n.get("meta", {}).get("namespace") or ""
            lang = n.get("meta", {}).get("lang", "")
            mods.setdefault((pkg, lang), []).append(n)
    rows = [[f"`{p or '(default)'}`", lang or "—", str(len(v)),
             ", ".join(sorted({x["name"] for x in v})[:8])]
            for (p, lang), v in sorted(mods.items())]
    _write(os.path.join(md, "modules.md"),
           GENERATED + "# Modules / Packages\n\n" +
           md_table(["Package", "Language", "Symbols", "Examples"],
                    cap_table(rows)))

    # symbols.md
    rows = [[n["kind"], f"`{n['name']}`",
             f"`{n['file']}`" + (f":{n['line']}" if n.get("line") else ""),
             n["confidence"]]
            for n in sorted(graph["nodes"],
                            key=lambda x: (x["kind"], x["name"]))
            if n["kind"] not in ("file", "configuration-key",
                                 "environment", "test-case")]
    _write(os.path.join(md, "symbols.md"),
           GENERATED + f"# Symbol Index ({len(rows)} symbols)\n\n" +
           md_table(["Kind", "Symbol", "Location", "Confidence"],
                    cap_table(rows, 200)))

    # dependencies.md
    rows = []
    for e in sorted(graph["edges"], key=lambda x: (x["type"], x["src"])):
        if e["type"] in ("injects", "implements", "extends",
                         "references", "imports", "depends-on",
                         "configures", "guarded-by"):
            s = by_id.get(e["src"], {}).get("name", e["src"])
            d = by_id.get(e["dst"], {}).get("name", e["dst"])
            rows.append([e["type"], f"`{s}`", f"`{d}`", e["confidence"]])
    _write(os.path.join(md, "dependencies.md"),
           GENERATED + "# Dependencies\n\n" +
           md_table(["Type", "From", "To", "Confidence"],
                    cap_table(rows, 200)))

    # call-graph.md
    rows = []
    for e in sorted(graph["edges"], key=lambda x: (x["src"], x["dst"])):
        if e["type"] == "calls" and not e["dst"].startswith("unresolved:"):
            s = by_id.get(e["src"], {}).get("name", e["src"])
            d = by_id.get(e["dst"], {}).get("name", e["dst"])
            rows.append([f"`{s}`", f"`{d}`", e["confidence"],
                         f"`{e['file']}`:{e['line']}"])
    _write(os.path.join(md, "call-graph.md"),
           GENERATED + "# Call Graph (resolved references)\n\n" +
           md_table(["Caller", "Callee", "Confidence", "Evidence"],
                    cap_table(rows, 200)))

    # data-flow.md — generic seed chains (endpoint/queue/event/job/route)
    with open(os.path.join(md, "data-flow.md"), "w",
              encoding="utf-8") as fh:
        fh.write(GENERATED + "# Data Flow (skeletons)\n\n"
                 "Deterministic skeletons from `flow_candidates` (any entry "
                 "kind: endpoint, queue/topic, event, job, route); curate "
                 "real business flows in `business-flows/`.\n\n")
        for f in graph.get("flow_candidates", [])[:80]:
            names = [by_id.get(c, {}).get("name", c) for c in f["chain"]]
            fh.write(f"## `{f.get('endpoint', f.get('seed'))}` "
                     f"({f.get('kind', 'endpoint')})\n\n" +
                     " → ".join(f"`{x}`" for x in names) + "\n\n")

    # api-map.md — every endpoint + handler + downstream + consumers
    handlers = {}
    for e in graph["edges"]:
        if e["type"] == "handled-by":
            handlers[e["src"]] = e["dst"]
    out_edges = {}
    for e in graph["edges"]:
        out_edges.setdefault(e["src"], []).append(e)
    with open(os.path.join(md, "api-map.md"), "w", encoding="utf-8") as fh:
        fh.write(GENERATED + "# API / Entry-Point Map\n\n"
                 "Endpoints, message handlers, jobs and routes with handlers, "
                 "downstream dependencies and consumers.\n\n")
        eps = [n for n in graph["nodes"] if n["kind"] == "endpoint"]
        for n in sorted(eps, key=lambda x: x["name"]):
            h = handlers.get(n["id"])
            fw = n.get("meta", {}).get("framework") or \
                n.get("meta", {}).get("via") or ""
            fh.write(f"## `{n['name']}`\n\n"
                     f"- Handler: `{by_id.get(h, {}).get('name', '?')}` "
                     f"(`{n['file']}`:{n['line']}, {n['confidence']})"
                     + (f" [{fw}]" if fw else "") + "\n")
            if h:
                downstream = [e for e in out_edges.get(h, [])
                              if e["type"] in ("calls", "reads", "writes",
                                               "queries", "invokes",
                                               "publishes")]
                if downstream:
                    fh.write("- Downstream:\n")
                    for e in downstream[:12]:
                        d = by_id.get(e["dst"], {}).get("name", e["dst"])
                        fh.write(f"  - {e['type']} `{d}` "
                                 f"({e['confidence']})\n")
            consumers = sorted({by_id.get(e["src"], {}).get("file", e["src"])
                                for e in graph["edges"]
                                if e["dst"] == n["id"]
                                and e["type"] in ("consumes", "exposes")})
            if consumers:
                fh.write("- Consumers:\n")
                for c in consumers[:12]:
                    fh.write(f"  - `{c}`\n")
            fh.write("\n")

    # database.md — generic: tables/views/collections/caches/procedures
    with open(os.path.join(md, "database.md"), "w", encoding="utf-8") as fh:
        fh.write(GENERATED + "# Data Map\n\n")
        for kind, title in (("table", "Tables"), ("view", "Views"),
                            ("collection", "Collections"),
                            ("procedure", "Procedures/Functions"),
                            ("sequence", "Sequences"),
                            ("cache", "Caches")):
            names = sorted({n["name"] for n in graph["nodes"]
                            if n["kind"] == kind or
                            (kind == "table" and n["id"].startswith("table:"))})
            if names:
                fh.write(f"## {title}\n\n" +
                         "".join(f"- `{t}`\n" for t in names) + "\n")
        migs = sorted([n for n in graph["nodes"]
                       if n["kind"] == "migration"],
                      key=lambda x: x["name"])
        if migs:
            fh.write("## Migrations\n\n")
            for m in migs:
                ops = [e for e in graph["edges"] if e["src"] == m["id"]]
                detail = ", ".join(
                    f"{e['type']} "
                    f"`{by_id.get(e['dst'], {}).get('name', e['dst'])}`"
                    for e in ops[:10])
                fh.write(f"- `{m['name']}` (`{m['file']}`): {detail}\n")
            fh.write("\n")
        fh.write("## Application → Data access → Store\n\n")
        for e in sorted(graph["edges"], key=lambda x: (x["src"], x["dst"])):
            if e["type"] in ("reads", "writes", "queries") and (
                    e["dst"].startswith("table:")
                    or e["dst"].startswith("entity:")
                    or e["dst"].startswith("collection:")):
                s = by_id.get(e["src"], {}).get("name", e["src"])
                d = by_id.get(e["dst"], {}).get("name", e["dst"])
                fh.write(f"- `{s}` —{e['type']}→ `{d}` "
                         f"({e['confidence']})\n")

    # external-services.md / authentication.md / configuration.md / deployment.md
    with open(os.path.join(md, "external-services.md"), "w",
              encoding="utf-8") as fh:
        fh.write(GENERATED + "# External Services\n\n")
        for n in sorted([x for x in graph["nodes"]
                         if x["kind"] == "external-service"],
                        key=lambda x: x["name"]):
            fh.write(f"- `{n['name']}` — {n['id']} "
                     f"(`{n['file']}`:{n.get('line')}, {n['confidence']})")
            if n.get("meta"):
                fh.write(f" {json.dumps(n['meta'])}")
            fh.write("\n")
    with open(os.path.join(md, "authentication.md"), "w",
              encoding="utf-8") as fh:
        fh.write(GENERATED + "# Authentication & Guards\n\n")
        for n in [x for x in graph["nodes"]
                  if x["kind"] in ("configuration", "guard")
                  and ("auth" in x["id"] or x["kind"] == "guard")]:
            fh.write(f"- `{n['name']}` (`{n['file']}`, {n['confidence']})\n")
        guards = [e for e in graph["edges"] if e["type"] == "guarded-by"]
        if guards:
            fh.write("\n## Route guards\n\n")
            for e in guards:
                s = by_id.get(e["src"], {}).get("name", e["src"])
                fh.write(f"- `{s}` guarded by `{e['dst']}` "
                         f"({e['confidence']})\n")
    with open(os.path.join(md, "configuration.md"), "w",
              encoding="utf-8") as fh:
        fh.write(GENERATED + "# Configuration\n\nKey names only — "
                 "values/secrets are never stored.\n\n")
        for n in sorted([x for x in graph["nodes"]
                         if x["kind"] in ("configuration-key",
                                          "environment")],
                        key=lambda x: x["name"]):
            fh.write(f"- `{n['name']}` (`{n['file']}`:{n.get('line')})\n")
    with open(os.path.join(md, "deployment.md"), "w",
              encoding="utf-8") as fh:
        fh.write(GENERATED + "# Deployment\n\n")
        for n in [x for x in graph["nodes"]
                  if x["kind"] in ("deployment-unit", "ci-job")]:
            fh.write(f"- `{n['name']}` (`{n['file']}`, {n['confidence']})\n")

    with open(os.path.join(md, "business-flows", "_candidates.md"), "w",
              encoding="utf-8") as fh:
        fh.write(GENERATED + "# Flow Candidates (needs agent curation)\n\n"
                 "Each skeleton below is tool evidence. Promote real "
                 "end-to-end flows to `<flow-name>.md` files with narrative "
                 "+ evidence links; do not invent steps beyond the "
                 "evidence.\n\n")
        for f in graph.get("flow_candidates", [])[:60]:
            names = [by_id.get(c, {}).get("name", c) for c in f["chain"]]
            fh.write(f"## `{f.get('endpoint', f.get('seed'))}` "
                     f"({f.get('kind', 'endpoint')})\n\n" +
                     " → ".join(f"`{x}`" for x in names) + "\n\n")

    arch = os.path.join(md, "architecture.md")
    if not os.path.exists(arch):
        with open(arch, "w", encoding="utf-8") as fh:
            fh.write(
                "# Architecture (agent-curated)\n\n"
                "> This mapper is technology-agnostic: write this narrative "
                "from tool evidence (`stack.md`, `api-map.md`, `database.md`, "
                "`data-flow.md`). Name the actual detected stack; never assert "
                "relationships marked LOW/UNKNOWN.\n\n"
                "## Detected stack\n\n(TODO: summarize `stack.md`.)\n\n"
                "## System overview\n\n(TODO: 5–10 sentences from evidence.)\n\n"
                "## Entry points\n\n(TODO: from `api-map.md` — endpoints, "
                "consumers, jobs, routes.)\n\n"
                "## Service boundaries\n\n(TODO: from `modules.md` + "
                "`dependencies.md`.)\n\n"
                "## Data architecture\n\n(TODO: from `database.md`.)\n\n"
                "## External systems\n\n(TODO: from `external-services.md`.)\n\n"
                "## Evidence snapshot\n\n"
                f"- Nodes: {len(graph['nodes'])}, "
                f"Edges: {len(graph['edges'])}\n"
                f"- Kinds: {json.dumps(kc, sort_keys=True)}\n"
                f"- Edge types: {json.dumps(ec, sort_keys=True)}\n"
                f"- Sync commit: `{commit}`\n")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def freshness_verdict(root, map_dir=DEFAULT_MAP_DIR):
    """Shared freshness verdict for status and validate.

    Returns dict(git_changed=[raw git signal], stale=[content differs],
    verdict='CURRENT'|'NEEDS_SYNC'). Verdict is NEEDS_SYNC when any
    tracked file's bytes differ from what sync recorded; the git list is
    display-only and never drives the verdict, so the two commands agree.
    """
    fresh = map_freshness(root, map_dir)
    root_abs = os.path.abspath(root)
    graph = None
    try:
        graph = load_graph(map_paths(root_abs, map_dir))
    except (OSError, ValueError, SystemExit):
        graph = None
    base = graph.get("last_sync_commit") if graph else None
    try:
        git_changed = sorted(changed_since(root_abs, base, map_dir))
    except Exception:
        git_changed = []
    stale = list(fresh.get("changed", []))
    verdict = "NEEDS_SYNC" if stale else "CURRENT"
    return {"git_changed": git_changed, "stale": stale,
            "verdict": verdict}


def malformed_ids(graph, cap=MAX_ID_LEN):
    """Node/edge IDs containing control chars (newline/tab/...) or beyond
    the sane length cap. Returned as (node_ids, edge_pairs) lists."""
    bad_nodes, bad_edges = [], []
    for n in graph.get("nodes", []):
        nid = n.get("id", "")
        if not isinstance(nid, str):
            bad_nodes.append(str(nid))
        elif len(nid) > cap or any(ord(c) < 32 for c in nid):
            bad_nodes.append(nid)
    for e in graph.get("edges", []):
        for key in ("src", "dst"):
            val = e.get(key, "")
            if not isinstance(val, str):
                bad_edges.append((e.get("src"), e.get("dst")))
                break
            if len(val) > cap or any(ord(c) < 32 for c in val):
                bad_edges.append((e.get("src"), e.get("dst")))
                break
    return bad_nodes, bad_edges


def snapshot_ids(graph):
    nids = {(n["id"], n["kind"], n["name"], n["file"]) for n in graph["nodes"]}
    eids = {(e["src"], e["dst"], e["type"]) for e in graph["edges"]}
    return nids, eids


def full_scan_cmd(root, commit, map_dir):
    graph, hashes = full_scan(root, commit, map_dir)
    return graph, hashes


def cmd_init(args):
    root = os.path.abspath(args.root)
    paths = map_paths(root, args.map_dir)
    if os.path.exists(paths["graph"]) and not args.full:
        print(f"Map already exists at {paths['graph']}. "
              "Run `sync`, or `init --full` to rebuild.")
        return 0
    commit = head_commit(root)
    graph, hashes = full_scan(root, commit, args.map_dir)
    save_graph(paths, graph)
    save_state(paths, graph, hashes)
    write_views(root, args.map_dir, graph)
    print(f"Initialized map: {len(graph['nodes'])} nodes, "
          f"{len(graph['edges'])} edges, {len(hashes)} files "
          f"-> {paths['dir']}")
    det = graph.get("detection", {})
    langs = [d["name"] for d in det.get("languages", [])]
    if langs:
        print(f"Detected languages: {', '.join(langs)}")
    unsup = det.get("unsupported", [])
    if unsup:
        print(f"Unsupported (fallback, LOW): "
              f"{', '.join(u['name'] for u in unsup)}")
    if graph.get("scan_errors"):
        print(f"scan errors: {len(graph['scan_errors'])} "
              f"(see graph.json scan_errors)")
    return 0


def _relevant(root, changed, map_dir=DEFAULT_MAP_DIR):
    """changed file -> analyzer -> rescan only what an analyzer claims."""
    relevant = set()
    extra = load_gitignore_basenames(root)
    skip_prefixes = _skill_rel_prefixes(root)
    for c in changed:
        if c.startswith(tuple(skip_prefixes)):
            continue  # skill's own install: never enters the map
        ext = os.path.splitext(c)[1].lower()
        if ext in SOURCE_EXTS or os.path.basename(c) in _config_filenames() \
                or c.startswith(".github/workflows/"):
            relevant.add(c)
    return relevant, extra


def cmd_sync(args):
    root = os.path.abspath(args.root)
    paths = map_paths(root, args.map_dir)
    if not os.path.exists(paths["graph"]):
        print("No map found; running full init.")
        return cmd_init(args)
    try:
        graph = load_graph(paths)
    except (OSError, ValueError, SystemExit) as exc:
        print(f"Map unreadable ({exc}); rebuilding from scratch, "
              "preserving curated files.")
        args.full = True
        return cmd_init(args)
    # Migrate older graphs: v1 tech-specific kinds -> generic schema,
    # then v2 -> v3 provenance stamping. Intent (ASSERTED) entries always
    # carry provenance explicitly, so migrate_v2 only fills the default.
    if graph.get("version", 1) < 2:
        graph = migrate_v1(graph)
    if graph.get("version", 1) < VERSION:
        graph = migrate_v2(graph)
    old_n, old_e = snapshot_ids(graph)
    commit = head_commit(root)
    base = graph.get("last_sync_commit")
    changed = changed_since(root, base, args.map_dir)
    relevant, _extra = _relevant(root, changed, args.map_dir)
    old_hashes = load_hashes(paths)
    extra_pre = load_gitignore_basenames(root)
    tracked_now = {rel for rel, _ in iter_repo_files(root, extra_pre,
                                                     args.map_dir)}
    deleted = {rel for rel in old_hashes if rel not in tracked_now}
    if deleted:
        relevant |= deleted
    if not relevant and base == commit and old_hashes:
        extra = load_gitignore_basenames(root)
        dirty = [rel for rel, ap in iter_repo_files(root, extra,
                                                    args.map_dir)
                 if old_hashes.get(rel) != sha256_file(ap)]
        if not dirty:
            print("Map is up to date (no changed files).")
            return 0
        relevant = set(dirty)
    if not relevant:
        graph["last_sync_commit"] = commit
        graph["last_sync_time"] = utcnow()
        save_graph(paths, graph)
        save_state(paths, graph, old_hashes)
        print("Map is up to date.")
        return 0
    # Drop stale corrupted IDs the fixed analyzers no longer emit: rescan
    # purges file-attributed entries, but orphan corrupted IDs from older
    # runs linger until the map self-heals here. A rescan of the owning
    # file would reproduce them iff the bug still existed, so dropping is
    # safe — and validate proves it (BAD_ID count must be 0 after sync).
    bad_id_nodes, bad_id_edges = malformed_ids(graph)
    if bad_id_nodes or bad_id_edges:
        bad_n = set(bad_id_nodes)
        bad_e = {(s, d) for s, d in bad_id_edges}
        graph["nodes"] = [n for n in graph["nodes"]
                          if n["id"] not in bad_n]
        graph["edges"] = [e for e in graph["edges"]
                          if (e["src"], e["dst"]) not in bad_e
                          and e["src"] not in bad_n and e["dst"] not in bad_n]
        print(f"Sync: dropped {len(bad_n)} malformed node IDs and "
              f"{len(bad_e)} malformed edges (stale pre-fix corruption).")
    # Provenance guard (Wave 4a, intent-layer survival): the purge below
    # drops every node/edge file-attributed to a rescanned path. Intent
    # (ASSERTED) entries are never file-attributed to scanned sources —
    # their `file` is a docs/*.md path — but a belt-and-suspenders guard
    # keeps any asserted entry regardless of its file value, so a future
    # caller can never silently purge curated intent.
    intent_nodes = [n for n in graph["nodes"] if G.is_asserted(n)]
    intent_node_ids = {n["id"] for n in intent_nodes}
    intent_edges = [e for e in graph["edges"] if G.is_asserted(e)]
    # changed file -> detect analyzer -> rescan file -> update generic graph
    graph["nodes"] = [n for n in graph["nodes"]
                      if n["file"] not in relevant or G.is_asserted(n)]
    graph["edges"] = [e for e in graph["edges"]
                      if e["file"] not in relevant or G.is_asserted(e)]
    tracked = {rel for rel, _ in iter_repo_files(
        root, load_gitignore_basenames(root), args.map_dir)}
    new_hashes = {k: v for k, v in old_hashes.items() if k not in relevant}
    for rel in sorted(relevant & tracked):
        ap = os.path.join(root, rel)
        try:
            new_hashes[rel] = sha256_file(ap)
        except OSError:
            continue
        scan_file(graph, rel, ap, commit)
    # re-run detection when manifests/configs changed (cheap, evidence-based)
    if any(os.path.basename(r) in ("package.json", "pom.xml",
                                   "requirements.txt", "pyproject.toml",
                                   "go.mod", "Cargo.toml", "go.sum")
           or r.endswith((".csproj", ".sln", "Gemfile")) or "compose" in r
           for r in relevant):
        try:
            by_rel = {r: os.path.join(root, r) for r in tracked}
            graph["detection"] = detect_tech(
                sorted(by_rel.items()), lambda r: _safe_read(by_rel.get(r)))
        except Exception as exc:
            graph.setdefault("scan_errors", []).append(
                f"detection refresh: {exc}")
    resolve_references(graph)
    compute_flow_candidates(graph)
    # Honesty as code moves (5.5 subset): every binding stores
    # asserted_commit at bind time. When a bound code node was rescanned
    # (materially changed) or deleted, mark the binding needs-review with
    # the reason recorded — surface in validate, never silently kept.
    intent_nodes = [n for n in graph.get("nodes", [])
                    if G.is_asserted(n)]
    intent_node_ids = {n["id"] for n in intent_nodes}
    intent_edges = [e for e in graph.get("edges", [])
                    if G.is_asserted(e)]
    if intent_nodes or intent_edges:
        _mark_bindings_review(graph, intent_edges, relevant, commit)
    graph["last_sync_commit"] = commit
    graph["last_sync_time"] = utcnow()
    new_n, new_e = snapshot_ids(graph)
    added_n = {x for x in new_n - old_n if x[1] in G.SIGNIFICANT_KINDS}
    removed_n = {x for x in old_n - new_n if x[1] in G.SIGNIFICANT_KINDS}
    added_e = {x for x in new_e - old_e}
    removed_e = {x for x in old_e - new_e}
    save_graph(paths, graph)
    save_state(paths, graph, new_hashes)
    write_views(root, args.map_dir, graph)
    if added_n or removed_n or added_e or removed_e:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M")
        short = (commit[:8] if commit else "worktree")
        lines = [f"# {stamp} (commit {short})\n",
                 f"\nChanged files ({len(relevant)}):\n"]
        lines += [f"- `{c}`\n" for c in sorted(relevant)[:30]]
        if added_n:
            lines.append("\nAdded:\n")
            lines += [f"- `{x[2]}` ({x[1]}, `{x[3]}`)\n"
                      for x in sorted(added_n)[:40]]
        if removed_n:
            lines.append("\nRemoved:\n")
            lines += [f"- `{x[2]}` ({x[1]}, was `{x[3]}`)\n"
                      for x in sorted(removed_n)[:40]]
        sig_e = [(s, d, t) for s, d, t in (added_e | removed_e)]
        if sig_e:
            lines.append("\nChanged relationships:\n")
            lines += [f"- `{s}` —{t}→ `{d}`\n"
                      for s, d, t in sorted(sig_e)[:40]]
        os.makedirs(paths["changes"], exist_ok=True)
        with open(os.path.join(paths["changes"], f"{stamp}-{short}.md"),
                  "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        print(f"Sync: +{len(added_n)}/-{len(removed_n)} significant nodes, "
              f"{len(added_e | removed_e)} changed edges "
              f"across {len(relevant)} files; change recorded.")
    else:
        print(f"Sync: rescanned {len(relevant)} files; "
              "no architectural change.")
    return 0


# v1 -> v2 kind/edge migration (one-way, preserves curated files).
V1_KIND_MAP = {
    "record": "class", "entity": "class", "repository": "class",
    "configuration": "configuration", "configuration-key": "configuration-key",
    "environment-variable": "environment", "frontend-component": "component",
    "frontend-service": "service", "frontend-route": "route",
    "frontend-guard": "guard", "frontend-interceptor": "interceptor",
    "ts-class": "class", "test-case": "test-case",
    "database-table": "table", "database-view": "view",
    "database-procedure": "procedure", "database-sequence": "sequence",
    "external-service": "external-service", "authentication": "configuration",
    "deployment": "deployment-unit", "ci-job": "ci-job",
    "migration": "migration", "file": "file",
}
V1_EDGE_MAP = {
    "stereotyped-as": "references", "instantiates": "references",
    "creates": "creates", "modifies": "modifies", "seeds": "seeds",
}


def migrate_v1(graph):
    for n in graph["nodes"]:
        n["kind"] = V1_KIND_MAP.get(n["kind"], n["kind"])
    for e in graph["edges"]:
        e["type"] = V1_EDGE_MAP.get(e["type"], e["type"])
    graph["version"] = 2
    graph.setdefault("detection", {})
    return graph


def migrate_v2(graph):
    """v2 -> v3: stamp provenance on every node/edge.

    Pre-4a entries predate the provenance field; absent means derived, so
    stamping the default preserves semantics exactly while making the
    sync-purge guard (`is_asserted`) and validate's intent checks total.
    One-way, preserves curated files; chained after migrate_v1 by cmd_sync
    and the intent commands."""
    for n in graph.get("nodes", []):
        n.setdefault("provenance", "derived")
    for e in graph.get("edges", []):
        e.setdefault("provenance", "derived")
    graph["version"] = VERSION
    return graph


def load_graph_migrated(paths, save=False):
    """Load graph.json, applying pending migrations (v1->v2->v3).

    Used by the intent commands so they work on a freshly-synced map
    without forcing `init --full`. Returns (graph, migrated_bool); when
    save=True the migrated graph is written back."""
    with open(paths["graph"], encoding="utf-8") as fh:
        graph = json.load(fh)
    migrated = False
    if graph.get("version", 1) < 2:
        graph = migrate_v1(graph)
        migrated = True
    if graph.get("version", 1) < VERSION:
        graph = migrate_v2(graph)
        migrated = True
    if migrated and save:
        save_graph(paths, graph)
    return graph, migrated


# --------------------------------------------------------------------------
# Intent layer (Wave 4a): ASSERTED human/agent claims over DERIVED code map.
#
# Single source of truth: docs/*.md remain authoritative; the graph holds a
# parsed projection. `intent import` syncs docs->graph only; docs win on
# disagreement (re-import overwrites the parsed projection fields, never
# hand-added bindings). No LLM-based extraction anywhere: intent is
# ASSERTED by humans/agents, never inferred — the parser below matches
# explicit Markdown structure (headings, bullets) deterministically.
# --------------------------------------------------------------------------

# docs/*.md sources parsed by `intent import`. Missing docs = clean
# "nothing to import", exit 0 — never an error.
INTENT_DOCS = ("docs/requirements.md", "docs/plan.md", "docs/decisions.md")

# Author recorded on import-created nodes/edges: the import is a mechanical
# projection, so the author is the docs themselves. Bind-created entries
# carry the --author flag (default below) since a human/agent asserts them.
IMPORT_AUTHOR = "intent-import(docs)"

# "- [ ] ..." acceptance-criteria bullets under a Flow heading.
ACCEPTANCE_RE = re.compile(r"^-\s*\[[ xX]\]\s*(.+)$")
# "- Not building: **title** — body" non-goal bullets.
NON_GOAL_RE = re.compile(r"^-\s*not building:\s*\*{0,2}(.+?)\*{0,2}"
                         r"\s*[—\-–:]\s*(.+)$", re.IGNORECASE)
# Generic non-goal bullets under "Out of scope" ("- Reopening/appeals ...").
GENERIC_BULLET_RE = re.compile(r"^-\s*(.+)$")
# plan.md data-model fence entries: "  claim\n    id PK, ..." or
# "  policy (seeded, read-only)\n    id PK, ...".
DATA_MODEL_ENTRY_RE = re.compile(r"^([A-Za-z][\w]*)\s*(\(.*\))?\s*$")
# requirements.md data table rows: "| Policy | key fields | ... |".
DATA_TABLE_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|")
# decisions.md "Deliberately not built" / "Not built" bullets -> non-goals.
NOT_BUILT_RE = re.compile(r"deliberately not built|not built", re.IGNORECASE)

# Bind-created edge keys: import only creates/updates nodes + part-of
# slice edges parsed from docs; bindings created by `intent bind` are keyed
# separately (meta.binding == "manual") and preserved across re-imports.
BINDING_MARK = "manual"
IMPORT_MARK = "import"


def _slug(text, max_words=8):
    words = re.findall(r"[a-z0-9]+", text.lower())
    slug = "-".join(words[:max_words]).strip("-")
    return slug or "item"


def _parse_md_sections(text):
    """Split Markdown into (level, title, body_lines, lineno) sections.

    Deterministic: headings `#{1,6} title`; everything else is body. Only
    `##`/`###` headings drive intent rules; other levels are context."""
    sections = []
    current = None
    for i, raw in enumerate(text.splitlines(), start=1):
        m = re.match(r"^(#{1,6})\s+(.*?)\s*$", raw)
        if m:
            if current is not None:
                sections.append(current)
            current = {"level": len(m.group(1)), "title": m.group(2),
                       "body": [], "lineno": i}
        elif current is not None:
            current["body"].append((i, raw))
    if current is not None:
        sections.append(current)
    return sections


def _code_fences(body_lines):
    """Yield (fence_body_lines, start_lineno) for ``` fenced blocks."""
    in_fence = False
    buf = []
    start = 0
    for lineno, raw in body_lines:
        if raw.strip().startswith("```"):
            if in_fence:
                yield buf, start
                buf = []
                in_fence = False
            else:
                in_fence = True
                start = lineno
        elif in_fence:
            buf.append((lineno, raw))
    # Unterminated fence: ignore (deterministic, never half-parse).


def parse_intent_docs(root, docs=INTENT_DOCS):
    """Parse docs/*.md into intent node/edge specs (pure function).

    Returns (nodes, edges) where each node is a dict with
    id/kind/name/title/body/source/file/line/author and each edge has
    src/dst/type/file/line. Ids are stable slugs: re-running on unchanged
    docs yields identical ids (idempotency key). No graph I/O, no LLM —
    headings + bullets only.

    Mapping:
      requirements.md `### Flow N — <t>` -> capability `intent:capability:flow-N-<slug>`
        `- [ ] <criterion>` bullets -> requirement
          `intent:requirement:flow-N-<k>-<slug>`, part-of the capability.
      requirements.md `## Data` table rows + plan.md `## Data model` fence
        entries -> concept `intent:concept:<slug>`.
      requirements.md `## Non-goals` ("- Not building: **T** — B") +
        plan.md `## Out of scope` bullets +
        decisions.md "**Deliberately not built ...:** a, b" bullets ->
        non-goal `intent:non-goal:<slug>` (nodes only, no edges).
      plan.md `### Slice N — <t>` -> slice `intent:slice:slice-N-<slug>`,
        part-of the capabilities its "- Satisfies: Flow N ..." line names
        (parsed from explicit `Flow <N>` references only — never inferred).
      plan.md `### <fork>` under `## Resolved forks` -> concept
        `intent:concept:fork-<slug>`.
      decisions.md `### <date> — <title>` -> decision
        `intent:decision:<date>-<slug>`; `- Satisfies/Plan: ... slice S..`
        cross-refs are ignored (bindings come from `intent bind` only).
    """
    nodes = []
    edges = []
    seen = set()

    def emit_node(nid, kind, name, title, body, doc_rel, lineno):
        if nid in seen:
            return None
        seen.add(nid)
        node = {"id": nid, "kind": kind, "name": name, "title": title,
                "body": body, "file": doc_rel, "line": lineno,
                "source": f"{doc_rel}:{lineno}", "author": IMPORT_AUTHOR}
        nodes.append(node)
        return node

    def emit_edge(src, dst, etype, doc_rel, lineno):
        key = (src, dst, etype)
        if key in seen:
            return
        seen.add(key)
        edges.append({"src": src, "dst": dst, "type": etype,
                      "file": doc_rel, "line": lineno})

    for doc_rel in docs:
        ap = os.path.join(root, doc_rel)
        try:
            with open(ap, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue  # missing docs: skipped; caller reports "nothing"
        base = os.path.basename(doc_rel)
        sections = _parse_md_sections(text)
        for idx, sec in enumerate(sections):
            title = sec["title"]
            level = sec["level"]
            # --- requirements.md flows -> capabilities + requirements ---
            if base == "requirements.md" and level == 3:
                m = re.match(r"^flow\s+(\d+)\s*[—\-–:]\s*(.+)$", title,
                             re.IGNORECASE)
                if m:
                    num, cap_title = m.group(1), m.group(2).strip()
                    cid = (f"{G.INTENT_ID_PREFIX}capability:"
                           f"flow-{num}-{_slug(cap_title)}")
                    body = "\n".join(
                        r for _, r in sec["body"] if r.strip())
                    emit_node(cid, "capability",
                              f"Flow {num} — {cap_title}", cap_title,
                              body, doc_rel, sec["lineno"])
                    k = 0
                    for lineno, raw in sec["body"]:
                        am = ACCEPTANCE_RE.match(raw.strip())
                        if am:
                            k += 1
                            crit = am.group(1).strip()
                            rid = (f"{G.INTENT_ID_PREFIX}requirement:"
                                   f"flow-{num}-{k}-{_slug(crit)}")
                            emit_node(rid, "requirement", crit, crit,
                                      "", doc_rel, lineno)
                            emit_edge(rid, cid, "part-of", doc_rel, lineno)
                    continue
            # --- non-goal sections (requirements + plan) ---
            if level == 2 and re.match(r"^(non-?goals|out of scope)",
                                       title, re.IGNORECASE):
                for lineno, raw in sec["body"]:
                    s = raw.strip()
                    if not s.startswith("-"):
                        continue
                    nm = NON_GOAL_RE.match(s)
                    if nm:
                        ng_title, ng_body = nm.group(1).strip(), \
                            nm.group(2).strip()
                    else:
                        gm = GENERIC_BULLET_RE.match(s)
                        if not gm or len(gm.group(1)) < 8:
                            continue
                        ng_title, ng_body = gm.group(1).strip(), ""
                    nid = (f"{G.INTENT_ID_PREFIX}non-goal:"
                           f"{_slug(ng_title)}")
                    emit_node(nid, "non-goal", ng_title, ng_title,
                              ng_body, doc_rel, lineno)
                continue
            # --- requirements.md data table -> concepts ---
            if base == "requirements.md" and level == 2 \
                    and title.strip().lower() == "data":
                for lineno, raw in sec["body"]:
                    tm = DATA_TABLE_ROW_RE.match(raw.strip())
                    if tm:
                        cell = tm.group(1).strip().strip("*")
                        if cell.lower() in ("entity",):
                            continue  # header row
                        if re.match(r"^-+$", cell):
                            continue  # separator row
                        nid = (f"{G.INTENT_ID_PREFIX}concept:"
                               f"{_slug(cell)}")
                        emit_node(nid, "concept", cell, cell, "",
                                  doc_rel, lineno)
                continue
            # --- plan.md data model fence -> concepts ---
            if base == "plan.md" and level == 2 \
                    and title.strip().lower() == "data model":
                for fence, start in _code_fences(sec["body"]):
                    for lineno, raw in fence:
                        if not raw or raw[:1].isspace():
                            continue  # indented detail line, not an entry
                        dm = DATA_MODEL_ENTRY_RE.match(raw.strip())
                        if dm:
                            entry = dm.group(1).strip()
                            if len(entry) >= 2:
                                nid = (f"{G.INTENT_ID_PREFIX}concept:"
                                       f"{_slug(entry)}")
                                emit_node(nid, "concept", entry, entry,
                                          (dm.group(2) or "").strip("()"),
                                          doc_rel, lineno)
                continue
            # --- plan.md slices -> slice nodes + part-of capabilities ---
            if base == "plan.md" and level == 3:
                m = re.match(r"^slice\s+(\d+)\s*[—\-–:]\s*(.+)$", title,
                             re.IGNORECASE)
                if m:
                    num, sl_title = m.group(1), m.group(2).strip()
                    sid = (f"{G.INTENT_ID_PREFIX}slice:"
                           f"slice-{num}-{_slug(sl_title)}")
                    body = "\n".join(
                        r for _, r in sec["body"] if r.strip())
                    emit_node(sid, "slice", f"Slice {num} — {sl_title}",
                              sl_title, body, doc_rel, sec["lineno"])
                    # Explicit "Flow N" refs in the Satisfies line only.
                    # Target id must match the capability id shape the
                    # requirements parser emits
                    # (intent:capability:flow-N-<slug>); resolve by prefix
                    # match against parsed capabilities.
                    for lineno, raw in sec["body"]:
                        if "satisfies" in raw.lower():
                            for fn in sorted(set(re.findall(
                                    r"flow\s+(\d+)", raw,
                                    re.IGNORECASE))):
                                prefix = (f"{G.INTENT_ID_PREFIX}"
                                          f"capability:flow-{fn}-")
                                for cand in nodes:
                                    if cand["kind"] == "capability" \
                                            and cand["id"].startswith(
                                                prefix):
                                        emit_edge(sid, cand["id"],
                                                  "part-of", doc_rel,
                                                  lineno)
                    continue
                # Resolved forks (concept nodes) — only inside plan.md's
                # "## Resolved forks" section (previous ## fa-level check:
                # nearest preceding level-2 heading must be Resolved forks).
                prev_l2 = None
                for prev in sections[:idx][::-1]:
                    if prev["level"] == 2:
                        prev_l2 = prev["title"]
                        break
                if prev_l2 and prev_l2.strip().lower() == "resolved forks":
                    fork_title = title.strip()
                    if len(fork_title) >= 4:
                        nid = (f"{G.INTENT_ID_PREFIX}concept:"
                               f"fork-{_slug(fork_title)}")
                        body = "\n".join(
                            r for _, r in sec["body"] if r.strip())
                        emit_node(nid, "concept", fork_title, fork_title,
                                  body, doc_rel, sec["lineno"])
                    continue
            # --- decisions.md entries -> decision nodes + non-goals ---
            if base == "decisions.md" and level == 3:
                m = re.match(r"^(\d{4}-\d{2}-\d{2})\s*[—\-–]\s*(.+)$",
                             title)
                if m:
                    date, d_title = m.group(1), m.group(2).strip()
                    did = (f"{G.INTENT_ID_PREFIX}decision:"
                           f"{date}-{_slug(d_title)}")
                    body = "\n".join(
                        r for _, r in sec["body"] if r.strip())
                    emit_node(did, "decision", d_title, d_title, body,
                              doc_rel, sec["lineno"])
                    for lineno, raw in sec["body"]:
                        if NOT_BUILT_RE.search(raw):
                            # "**Deliberately not built (Non-goals):** a, b."
                            items = re.split(r"[:,;]", raw, maxsplit=1)
                            tail = items[-1] if len(items) > 1 else raw
                            tail = re.sub(r"\*+", "", tail).strip().rstrip(
                                ".")
                            for item in re.split(r";", tail):
                                item = item.strip()
                                if len(item) >= 4:
                                    nid = (f"{G.INTENT_ID_PREFIX}"
                                           f"non-goal:{_slug(item)}")
                                    emit_node(nid, "non-goal", item, item,
                                              "", doc_rel, lineno)
                    continue
    return nodes, edges


def _intent_binding_edges(graph):
    """Asserted intent edges created by `intent bind` (preserved on import)."""
    return [e for e in graph.get("edges", [])
            if G.is_asserted(e)
            and e.get("meta", {}).get("binding") == BINDING_MARK]


def cmd_intent_import(args):
    """`intent import` — parse docs/*.md into intent nodes (ASSERTED).

    Idempotent and re-runnable: re-import updates the parsed-projection
    fields (title/body/source) of import-owned nodes, never overwrites
    hand-added bindings (`meta.binding == "manual"` edges are preserved),
    and creates nothing twice (stable slug ids). Missing docs = clean
    "nothing to import", exit 0."""
    root = os.path.abspath(args.root)
    paths = map_paths(root, args.map_dir)
    if not os.path.exists(paths["graph"]):
        print("STATUS: NO_MAP — run `init` first.")
        return 1
    try:
        graph, migrated = load_graph_migrated(paths, save=True)
    except (OSError, ValueError) as exc:
        print(f"STATUS: CORRUPT — graph.json unreadable ({exc}); "
              "run `init --full` to rebuild.")
        return 2
    if migrated:
        print(f"Map migrated to schema v{VERSION}.")
    commit = head_commit(root)
    now = utcnow()
    nodes, edges = parse_intent_docs(root)
    if not nodes and not edges:
        print("Nothing to import: docs/requirements.md, docs/plan.md and "
              "docs/decisions.md are all missing or have no parseable "
              "intent sections.")
        return 0
    by_id = {n["id"]: n for n in graph["nodes"]}
    created_n = updated_n = 0
    for spec in nodes:
        existing = by_id.get(spec["id"])
        if existing is None:
            G.add_node(graph, spec["id"], spec["kind"], spec["name"],
                       spec["file"], spec["line"], confidence=None,
                       provenance="asserted", title=spec["title"],
                       body=spec["body"], source=spec["source"],
                       author=spec["author"], asserted_at=now,
                       asserted_commit=commit, status="active",
                       meta={"binding": IMPORT_MARK})
            created_n += 1
        elif G.is_asserted(existing):
            # Docs win on disagreement: refresh the parsed projection.
            # A superseded/needs-review status is sticky — re-import of
            # identical docs never clears human review state.
            existing["name"] = spec["name"]
            existing["title"] = spec["title"]
            existing["body"] = spec["body"]
            existing["source"] = spec["source"]
            existing["file"] = spec["file"]
            existing["line"] = spec["line"]
            existing["asserted_commit"] = commit
            existing["meta"]["binding"] = IMPORT_MARK
            updated_n += 1
        # else: a DERIVED node somehow owns the id — impossible by prefix,
        # but never clobber it; skip deterministically.
    created_e = 0
    edge_keys = {(e["src"], e["dst"], e["type"])
                 for e in graph["edges"]}
    for spec in edges:
        if (spec["src"], spec["dst"], spec["type"]) in edge_keys:
            continue
        if spec["src"] not in {n["id"] for n in graph["nodes"]}:
            continue  # slice references a capability not in docs: skip
        if spec["dst"] not in {n["id"] for n in graph["nodes"]}:
            continue
        G.add_edge(graph, spec["src"], spec["dst"], spec["type"],
                   spec["file"], spec["line"], confidence=None,
                   provenance="asserted", author=IMPORT_AUTHOR,
                   asserted_at=now, asserted_commit=commit,
                   meta={"binding": IMPORT_MARK})
        created_e += 1
    save_graph(paths, graph)
    print(f"Intent import: {created_n} node(s) created, {updated_n} "
          f"updated, {created_e} edge(s) created "
          f"({len(_intent_binding_edges(graph))} manual binding(s) "
          f"preserved).")
    return 0


def _resolve_code_node(graph, text):
    """Resolve a --nodes entry to a graph node id.

    Accepts an exact node id or a name/id substring (same matching as
    find_nodes). Raises LookupError when nothing matches; on ambiguous
    matches the first id-sorted hit wins deterministically and the caller
    reports it."""
    by_id = {n["id"]: n for n in graph["nodes"]}
    if text in by_id:
        return by_id[text], False
    hits = sorted(find_nodes(graph, text), key=lambda n: n["id"])
    if not hits:
        raise LookupError(f"unknown code node: '{text}'")
    return hits[0], len(hits) > 1


def cmd_intent_bind(args):
    """`intent bind` — programmatic write path: attach code nodes to intent.

    `intent bind --slice <name> --realizes <req-id>
    --nodes <id> [<id>...] [--nodes <id>...]
    --why <text> [--author <name>] [--decision <decision-id>]
    [--declares-files <paths...>]`

    `--nodes` is variadic and repeatable: space-separated ids, the legacy
    comma-separated form, or repeated flags all bind the union.

    `--declares-files` records the step-1 pre-implementation file
    declaration (workflow skill 04-slice capture hook) verbatim on every
    binding edge; re-binding with it updates the stored record, re-binding
    without it preserves the record. Never validated against the tree —
    declaration precedes code.

    Creates realizes (code->requirement/capability) and delivered-in
    (code->slice) edges plus motivated-by (code/slice->decision) where
    --decision is given. Never requires hand-editing Markdown. Validates
    that both the intent ids and the code node ids exist (error +
    non-zero exit otherwise). Stores asserted_commit on every binding."""
    root = os.path.abspath(args.root)
    paths = map_paths(root, args.map_dir)
    if not os.path.exists(paths["graph"]):
        print("STATUS: NO_MAP — run `init` first.")
        return 1
    try:
        graph, migrated = load_graph_migrated(paths, save=True)
    except (OSError, ValueError) as exc:
        print(f"STATUS: CORRUPT — graph.json unreadable ({exc}); "
              "run `init --full` to rebuild.")
        return 2
    if migrated:
        print(f"Map migrated to schema v{VERSION}.")
    by_id = {n["id"]: n for n in graph["nodes"]}
    # --- validate intent ids exist and are intent nodes ---
    problems = []
    slice_hits = sorted(find_nodes(graph, args.slice),
                        key=lambda n: n["id"])
    slice_node = None
    if args.slice in by_id:
        slice_node = by_id[args.slice]
    elif len(slice_hits) == 1:
        slice_node = slice_hits[0]
    elif len(slice_hits) > 1:
        intent_hits = [n for n in slice_hits if G.is_asserted(n)]
        slice_node = intent_hits[0] if len(intent_hits) == 1 else None
        if slice_node is None:
            problems.append(
                f"ambiguous --slice '{args.slice}': matches "
                f"{len(slice_hits)} nodes "
                f"({', '.join(n['id'] for n in slice_hits[:5])}); "
                f"pass an exact intent id")
    if slice_node is None and not any("slice" in p for p in problems):
        problems.append(f"unknown --slice '{args.slice}': no intent node "
                        f"with that id or name (run `intent import` first)")
    elif slice_node is not None and not G.is_asserted(slice_node):
        problems.append(f"--slice '{args.slice}' resolves to a DERIVED "
                        f"code node [{slice_node['id']}]; bind needs an "
                        f"intent slice/requirement/capability id")
    req_node = by_id.get(args.realizes)
    if req_node is None:
        hits = sorted(find_nodes(graph, args.realizes),
                      key=lambda n: n["id"])
        intent_hits = [n for n in hits if G.is_asserted(n)]
        if len(intent_hits) == 1:
            req_node = intent_hits[0]
        else:
            problems.append(
                f"unknown --realizes '{args.realizes}': no intent node "
                f"with that id or name (run `intent import` first)")
    elif not G.is_asserted(req_node):
        problems.append(f"--realizes '{args.realizes}' resolves to a "
                        f"DERIVED code node [{req_node['id']}]; bind needs "
                        f"an intent requirement/capability id")
    dec_node = None
    if args.decision:
        dec_node = by_id.get(args.decision)
        if dec_node is None:
            problems.append(f"unknown --decision '{args.decision}': no "
                            f"intent node with that id")
        elif not G.is_asserted(dec_node):
            problems.append(f"--decision '{args.decision}' is a DERIVED "
                            f"code node; bind needs an intent decision id")
    # --- validate code node ids exist ---
    # --nodes is variadic (space-separated); each entry may itself be
    # comma-separated (legacy single-value form). Both spellings bind the
    # union — repeated --nodes flags accumulate via action="append", never
    # silently keep only the last.
    code_nodes = []
    raw_entries = []
    for group in args.nodes:
        # action="append" + nargs="+" nests one level: each group is the
        # list of tokens from one --nodes occurrence.
        tokens = group if isinstance(group, list) else [group]
        for tok in tokens:
            raw_entries.extend(tok.split(","))
    for raw in raw_entries:
        text = raw.strip()
        if not text:
            continue
        try:
            node, ambiguous = _resolve_code_node(graph, text)
        except LookupError as exc:
            problems.append(str(exc))
            continue
        if G.is_asserted(node):
            problems.append(f"'{text}' resolves to an intent node "
                            f"[{node['id']}]; --nodes needs code nodes")
            continue
        code_nodes.append((node, ambiguous))
    if not code_nodes and not problems:
        problems.append("no --nodes entries: pass at least one code node "
                        "id or name")
    if not args.why or not args.why.strip():
        problems.append("missing --why: a plain-language reason is required")
    if problems:
        for p in problems:
            print(f"ERROR: {p}")
        return 1
    # --- write bindings (idempotent: same src/dst/type reuses the edge) ---
    commit = head_commit(root)
    now = utcnow()
    author = args.author or "agent"
    # --declares-files: the step-1 pre-implementation file declaration
    # (workflow skill 04-slice capture hook). Stored verbatim on every
    # binding edge from this invocation; the deferred `drift` command
    # compares this set against actually-changed files. Pure record —
    # never validated against the tree (declaration precedes code).
    declared = getattr(args, "declares_files", None) or []
    if isinstance(declared, str):
        declared = [declared]
    declared = [d.strip() for d in declared if d and d.strip()]
    made = 0
    for node, ambiguous in code_nodes:
        for etype, target in (("realizes", req_node),
                              ("delivered-in", slice_node)):
            key = (node["id"], target["id"], etype)
            exists = any((e["src"], e["dst"], e["type"]) == key
                         for e in graph["edges"])
            edge_meta = {"binding": BINDING_MARK,
                         "why": args.why.strip()}
            if declared:
                edge_meta["declares_files"] = declared
            edge = G.add_edge(graph, node["id"], target["id"], etype,
                              node.get("file") or target.get("file"),
                              node.get("line"), confidence=None,
                              provenance="asserted", author=author,
                              asserted_at=now, asserted_commit=commit,
                              meta=edge_meta)
            if exists and declared:
                # Re-bind with a declaration updates the stored record:
                # the step-1 declaration is the highest-value capture and
                # must never be silently dropped by idempotent reuse.
                edge.setdefault("meta", {})["declares_files"] = declared
            if not exists:
                made += 1
        if dec_node is not None:
            key = (node["id"], dec_node["id"], "motivated-by")
            exists = any((e["src"], e["dst"], e["type"]) == key
                         for e in graph["edges"])
            G.add_edge(graph, node["id"], dec_node["id"], "motivated-by",
                       node.get("file") or dec_node.get("file"),
                       node.get("line"), confidence=None,
                       provenance="asserted", author=author,
                       asserted_at=now, asserted_commit=commit,
                       meta={"binding": BINDING_MARK,
                             "why": args.why.strip()})
            if not exists:
                made += 1
        if ambiguous:
            print(f"note: '{node['name']}' matched multiple code nodes; "
                  f"bound [{node['id']}] (first id-sorted hit)")
    save_graph(paths, graph)
    print(f"Bound {len(code_nodes)} code node(s) to "
          f"{G.ASSERTED_MARKER} {req_node['id']} (realizes) + "
          f"{G.ASSERTED_MARKER} {slice_node['id']} (delivered-in)"
          + (f" + {G.ASSERTED_MARKER} {dec_node['id']} (motivated-by)"
             if dec_node is not None else "")
          + f": {made} new binding edge(s), author '{author}', "
          f"commit {(commit or '?')[:12]}. Why: {args.why.strip()}")
    return 0


def _mark_bindings_review(graph, intent_edges, relevant, commit):
    """Mark bindings needs-review when a bound code node changed/deleted.

    `relevant` = rescanned-or-deleted scanned files this sync. A binding
    edge whose code endpoint (a) no longer exists in the graph (deleted) or
    (b) was file-attributed to a rescanned path (materially changed) gets
    meta.review = 'needs-review' + meta.review_reason, and the intent node
    it points at gets status needs-review. Reason recorded; counts
    returned for the sync summary. Pure graph mutation (no I/O)."""
    by_id = {n["id"]: n for n in graph["nodes"]}
    marked = 0
    for e in intent_edges:
        if e.get("meta", {}).get("binding") != BINDING_MARK:
            continue
        code_end = None
        intent_end = None
        for end in (e["src"], e["dst"]):
            n = by_id.get(end)
            if n is not None and G.is_asserted(n):
                intent_end = n
            else:
                code_end = end
        if code_end is None or intent_end is None:
            continue
        code_node = by_id.get(code_end)
        if code_node is None:
            reason = (f"bound code node '{code_end}' no longer exists "
                      f"(deleted or renamed)")
        elif code_node.get("file") in (relevant or set()):
            reason = (f"bound code node '{code_end}' changed "
                      f"(`{code_node.get('file')}` rescanned)")
        else:
            continue
        meta = e.setdefault("meta", {})
        if meta.get("review") != "needs-review" \
                or meta.get("review_reason") != reason:
            meta["review"] = "needs-review"
            meta["review_reason"] = reason
            meta["review_commit"] = commit
            marked += 1
        if intent_end.get("status") != "needs-review":
            intent_end["status"] = "needs-review"
            intent_end.setdefault("meta", {})["review_reason"] = reason
    return marked


def cmd_why(args):
    """`why <node>` — reverse lookup: file/symbol/endpoint -> intent.

    Human-first output: plain-language claim first, then evidence, then
    node IDs. Every intent line carries the [ASSERTED] marker."""
    root = os.path.abspath(args.root)
    paths = map_paths(root, args.map_dir)
    if not os.path.exists(paths["graph"]):
        print("STATUS: NO_MAP — run `init` first.")
        return 1
    try:
        graph, _ = load_graph_migrated(paths)
    except (OSError, ValueError) as exc:
        print(f"STATUS: CORRUPT — graph.json unreadable ({exc}); "
              "run `init --full` to rebuild.")
        return 2
    by_id = {n["id"]: n for n in graph["nodes"]}
    hits = find_nodes(graph, args.node)
    if args.node in by_id:
        hits = [by_id[args.node]]
    if not hits:
        print(f"No nodes match '{args.node}'.")
        return 1
    # code node under the query: file substring also matches ("Claim.java"
    # finds every symbol evidenced at that file).
    targets = list(hits)
    for n in graph["nodes"]:
        if G.is_asserted(n):
            continue
        if args.node in (n.get("file") or "") and n not in targets:
            targets.append(n)
    targets = targets[:8]
    # intent edges touching any target (binding endpoint on either side).
    target_ids = {t["id"] for t in targets}
    bindings = [e for e in graph["edges"]
                if G.is_asserted(e)
                and (e["src"] in target_ids or e["dst"] in target_ids)]
    if not bindings:
        print(f"WHY: '{args.node}' has no intent bindings — no requirement "
              f"claims this code yet (bind one with `intent bind`).")
        return 1
    by_kind_edge = {}
    for e in bindings:
        other = e["dst"] if e["src"] in target_ids else e["src"]
        by_kind_edge.setdefault(e["type"], []).append((e, other))
    print(f"WHY: '{args.node}' — "
          f"{len(bindings)} intent binding(s) claim this code:")
    for etype in ("realizes", "delivered-in", "motivated-by", "denotes",
                  "part-of"):
        for e, oid in sorted(by_kind_edge.get(etype, []),
                             key=lambda x: x[1]):
            n = by_id.get(oid, {})
            claim = (n.get("title") or n.get("name", oid))
            why = e.get("meta", {}).get("why", "")
            review = e.get("meta", {}).get("review", "")
            if etype == "realizes":
                print(f"- This code exists to satisfy: \"{claim}\" "
                      f"{G.ASSERTED_MARKER}")
            elif etype == "delivered-in":
                print(f"- This code shipped in: \"{claim}\" "
                      f"{G.ASSERTED_MARKER}")
            elif etype == "motivated-by":
                print(f"- This code is shaped by decision: \"{claim}\" "
                      f"{G.ASSERTED_MARKER}")
            elif etype == "denotes":
                print(f"- This symbol means (domain concept): \"{claim}\" "
                      f"{G.ASSERTED_MARKER}")
            else:
                print(f"- Related ({etype}): \"{claim}\" "
                      f"{G.ASSERTED_MARKER}")
            if why:
                print(f"  Because: {why}")
            ev = [t for t in targets
                  if t["id"] in (e["src"], e["dst"])]
            for t in ev[:3]:
                loc = f"`{t.get('file')}`" + (
                    f":{t.get('line')}" if t.get("line") else "")
                print(f"  Evidence: {t['kind']} `{t['name']}` at {loc} "
                      f"(DERIVED, {t.get('confidence')})")
            if review == "needs-review":
                print(f"  !! NEEDS-REVIEW: "
                      f"{e.get('meta', {}).get('review_reason', '')}")
            print(f"  Intent: [{oid}] source "
                  f"{n.get('source', '?')} by {n.get('author', '?')}")
    return 0


def cmd_responsible_for(args):
    """`responsible-for <intent-id>` — forward lookup: intent -> code.

    Requirement/capability -> realizing code nodes + covering tests.
    Human-first: plain-language claim first, then code, then IDs."""
    root = os.path.abspath(args.root)
    paths = map_paths(root, args.map_dir)
    if not os.path.exists(paths["graph"]):
        print("STATUS: NO_MAP — run `init` first.")
        return 1
    try:
        graph, _ = load_graph_migrated(paths)
    except (OSError, ValueError) as exc:
        print(f"STATUS: CORRUPT — graph.json unreadable ({exc}); "
              "run `init --full` to rebuild.")
        return 2
    by_id = {n["id"]: n for n in graph["nodes"]}
    node = by_id.get(args.intent_id)
    if node is None:
        hits = [n for n in find_nodes(graph, args.intent_id)
                if G.is_asserted(n)]
        if len(hits) == 1:
            node = hits[0]
        elif not hits:
            print(f"No intent node matches '{args.intent_id}' "
                  f"(run `intent import` first).")
            return 1
        else:
            print(f"Ambiguous '{args.intent_id}': matches "
                  f"{len(hits)} intent nodes; pass an exact intent id:")
            for h in hits[:8]:
                print(f"  [{h['id']}] {h.get('title', h['name'])} "
                      f"{G.ASSERTED_MARKER}")
            return 1
    if not G.is_asserted(node):
        print(f"[{node['id']}] is a DERIVED code node, not intent; "
              f"`responsible-for` needs an intent id "
              f"(run `intent import` first).")
        return 1
    iid = node["id"]
    claim = node.get("title") or node["name"]
    print(f"RESPONSIBLE-FOR: \"{claim}\" {G.ASSERTED_MARKER} [{iid}]")
    print(f"Claim: {claim} (status: {node.get('status', 'active')}, "
          f"source {node.get('source', '?')} by {node.get('author', '?')})")
    # realizing code: incoming realizes edges + member requirements'
    # realizes (capability -> its part-of requirements -> their code).
    code_ids = set()
    why_by_code = {}
    for e in graph["edges"]:
        if e["type"] == "realizes" and e["dst"] == iid \
                and e["dst"] in by_id:
            code_ids.add(e["src"])
            why_by_code[e["src"]] = e.get("meta", {}).get("why", "")
    if node["kind"] == "capability":
        member_reqs = [e["src"] for e in graph["edges"]
                       if e["type"] == "part-of" and e["dst"] == iid]
        for e in graph["edges"]:
            if e["type"] == "realizes" and e["dst"] in member_reqs:
                code_ids.add(e["src"])
                why_by_code.setdefault(
                    e["src"], e.get("meta", {}).get("why", ""))
    # slices delivering this intent (delivered-in edges pointing here, or
    # slices part-of this capability).
    slice_ids = {e["src"] for e in graph["edges"]
                 if e["type"] == "delivered-in" and e["dst"] == iid}
    slice_ids |= {e["src"] for e in graph["edges"]
                  if e["type"] == "part-of" and e["dst"] == iid
                  and by_id.get(e["src"], {}).get("kind") == "slice"}
    if slice_ids:
        print("Delivered in:")
        for sid in sorted(slice_ids):
            s = by_id.get(sid, {})
            print(f"  - \"{s.get('title', s.get('name', sid))}\" "
                  f"{G.ASSERTED_MARKER} [{sid}]")
    if not code_ids:
        print("Realizing code: (none bound yet — no `intent bind` edges "
              "point here)")
        return 1
    # covering tests: `tests` edges incident on the realizing code nodes.
    test_ids = set()
    for e in graph["edges"]:
        if e["type"] == "tests" and (
                e["src"] in code_ids or e["dst"] in code_ids):
            test_ids.add(e["dst"] if e["src"] in code_ids else e["src"])
    print(f"Realizing code ({len(code_ids)} node(s)):")
    for cid in sorted(code_ids):
        c = by_id.get(cid, {})
        loc = f"`{c.get('file', '?')}`" + (
            f":{c.get('line')}" if c.get("line") else "")
        flag = ""
        for e in graph["edges"]:
            if e.get("meta", {}).get("review") == "needs-review" and (
                    e["src"] == cid or e["dst"] == cid):
                flag = (f"  !! NEEDS-REVIEW: "
                        f"{e['meta'].get('review_reason', '')}")
                break
        print(f"  - {c.get('kind', '?')} `{c.get('name', cid)}` at {loc} "
              f"(DERIVED, {c.get('confidence', '?')})")
        if why_by_code.get(cid):
            print(f"    Why bound: {why_by_code[cid]}")
        if flag:
            print(flag)
        print(f"    [{cid}]")
    if test_ids:
        print(f"Covering tests ({len(test_ids)}):")
        for tid in sorted(test_ids):
            t = by_id.get(tid, {})
            print(f"  - `{t.get('name', tid)}` "
                  f"`{t.get('file', '?')}` [{tid}]")
    else:
        print("Covering tests: (none recorded — no `tests` edges touch "
              "these code nodes yet)")
    return 0


def cmd_intent(args):
    if args.intent_cmd == "import":
        return cmd_intent_import(args)
    if args.intent_cmd == "bind":
        return cmd_intent_bind(args)
    print(f"Unknown intent subcommand: {args.intent_cmd}")
    return 2


def cmd_status(args):
    root = os.path.abspath(args.root)
    paths = map_paths(root, args.map_dir)
    if not os.path.exists(paths["graph"]):
        print("STATUS: NO_MAP — run `init` first.")
        return 1
    graph = load_graph(paths)
    commit = head_commit(root)
    base = graph.get("last_sync_commit")
    changed = changed_since(root, base, args.map_dir)
    fresh = map_freshness(root, args.map_dir)
    det = graph.get("detection", {})
    langs = ",".join(d["name"] for d in det.get("languages", [])[:6])
    print("CODEBASE MAP STATUS")
    print(f"Nodes: {len(graph['nodes'])}  "
          f"Edges: {len(graph['edges'])}  "
          f"Unresolved refs: {len(graph.get('unresolved', []))}"
          + (f"  Stack: {langs}" if langs else ""))
    print(f"Last sync: {graph.get('last_sync_time')} "
          f"(commit {(base or '?')[:12]})")
    print(f"HEAD:      {(commit or '?')[:12]}")
    # changed = raw git signal (committed + working-tree vs last_sync_commit).
    # stale = hash-verified staleness: a file counts only when its bytes
    # actually differ from what sync recorded. Uncommitted-but-identical
    # files, renames git reports oddly, and map-internal churn show up in
    # `changed` but NOT in `stale` — that is expected, not drift.
    # Both status and validate share freshness_verdict(), so they agree;
    # the verdict is NEEDS_SYNC iff any content is stale.
    verdict = freshness_verdict(root, args.map_dir)
    changed = verdict["git_changed"]
    stale = verdict["stale"]
    print(f"Changed (git signal): {len(changed)}")
    for c in sorted(changed)[:15]:
        print(f"  M `{c}`")
    if len(changed) > 15:
        print(f"  ...and {len(changed) - 15} more")
    if stale:
        print(f"Stale (content differs): {len(stale)}")
        for c in stale[:10]:
            print(f"  S `{c}`")
    print("Status:", verdict["verdict"])
    return 0


def cmd_validate(args):
    root = os.path.abspath(args.root)
    paths = map_paths(root, args.map_dir)
    if not os.path.exists(paths["graph"]):
        print("STATUS: NO_MAP — run `init` first.")
        return 1
    try:
        graph = load_graph(paths)
    except (OSError, ValueError) as exc:
        print(f"STATUS: CORRUPT — graph.json unreadable ({exc}); "
              "run `init --full` to rebuild.")
        return 2
    # version check first: stale-schema graphs must rebuild, not validate
    if graph.get("version") != VERSION:
        print(f"STATUS: STALE_SCHEMA — graph.json version "
              f"{graph.get('version')} != tool version {VERSION}; "
              "run `init --full` to rebuild.")
        return 2
    bad_kinds = {n["id"] for n in graph["nodes"]
                 if n["kind"] not in G.NODE_KINDS}
    bad_edges = [e for e in graph["edges"]
                 if e["type"] not in G.EDGE_TYPES]
    bad_id_nodes, bad_id_edges = malformed_ids(graph)
    ids = {n["id"] for n in graph["nodes"]}
    seen, dupes = set(), set()
    for n in graph["nodes"]:
        if n["id"] in seen:
            dupes.add(n["id"])
        seen.add(n["id"])
    broken, missing_files = [], set()
    for e in graph["edges"]:
        if e["src"] not in ids:
            broken.append(e)
            continue
        dst = e["dst"]
        if dst in ids:
            continue
        if G.is_placeholder(dst, ids):
            continue
        broken.append(e)
    hashes = load_hashes(paths)
    _ = hashes  # staleness comes from the shared verdict below (same as status)
    missing_files = set()
    for n in graph["nodes"]:
        f = n.get("file")
        if not f:
            continue
        if not os.path.exists(os.path.join(root, f)):
            missing_files.add(f)
    # Hash-verified staleness comes from the shared verdict (same check
    # status uses), so the two commands never disagree.
    verdict_pre = freshness_verdict(root, args.map_dir)
    stale_files = set(verdict_pre["stale"])
    no_evidence = [n["id"] for n in graph["nodes"]
                   if not n.get("file") or n.get("confidence") == "UNKNOWN"]
    stale_edges = sum(1 for e in graph["edges"]
                      if e["file"] in stale_files)
    valid = len(graph["edges"]) - len(broken) - stale_edges
    print("CODEBASE MAP VALIDATION")
    print(f"Nodes:              {len(graph['nodes'])}")
    print(f"Relationships:      {len(graph['edges'])}")
    print(f"Valid:              {max(valid, 0)}")
    print(f"Stale:              {stale_edges}")
    print(f"Unverified (LOW):   "
          f"{sum(1 for e in graph['edges'] if e['confidence'] == 'LOW')}")
    print(f"Broken:             {len(broken)}")
    print(f"Duplicate nodes:    {len(dupes)}")
    print(f"Schema violations:  {len(bad_kinds)} bad kinds, "
          f"{len(bad_edges)} bad edge types, "
          f"{len(bad_id_nodes) + len(bad_id_edges)} malformed IDs")
    for nid in bad_id_nodes[:10]:
        print(f"  BAD_ID {md_cell(nid)[:80]}")
    for src, dst in bad_id_edges[:10]:
        print(f"  BAD_EDGE {md_cell(src)[:60]} -> {md_cell(dst)[:60]}")
    print(f"No-evidence nodes:  {len(no_evidence)}")
    print(f"Deleted files referenced: {len(missing_files)}")
    for f in sorted(missing_files)[:10]:
        print(f"  DEL {f}")
    # Intent-layer checks (Wave 4a): bindings pointing at nonexistent code
    # nodes (dangling), and requirements/capabilities with zero bindings.
    # Reported as NEEDS_ATTENTION findings (same severity as other hygiene
    # issues); full drift/violates coverage is deferred to a later wave.
    # Intent doc sources (docs/*.md) are curated, not scanned: they must
    # not trip the missing-files check above.
    for n in graph["nodes"]:
        if G.is_asserted(n) and n.get("file") in missing_files:
            missing_files.discard(n["file"])
    dangling = sorted({
        (e["src"] if e["dst"] in ids and G.is_asserted(
            next(n for n in graph["nodes"] if n["id"] == e["dst"]))
         else e["dst"])
        for e in graph["edges"]
        if G.is_asserted(e) and (
            e["src"] not in ids or e["dst"] not in ids)
    })
    bound_targets = {e["dst"] for e in graph["edges"]
                     if G.is_asserted(e) and e["type"] == "realizes"
                     and e["dst"] in ids}
    member_of = {}
    for e in graph["edges"]:
        if e["type"] == "part-of" and e["dst"] in ids:
            member_of.setdefault(e["dst"], set()).add(e["src"])
    unbound = sorted(
        n["id"] for n in graph["nodes"]
        if G.is_asserted(n)
        and n["kind"] in ("requirement", "capability")
        and n["id"] not in bound_targets
        and not (member_of.get(n["id"], set()) & bound_targets))
    needs_review = sorted(
        n["id"] for n in graph["nodes"]
        if G.is_asserted(n) and n.get("status") == "needs-review")
    print(f"Intent dangling refs: {len(dangling)}")
    for d in dangling[:10]:
        print(f"  DANGLING {md_cell(d)[:80]}")
    print(f"Requirements without bindings: {len(unbound)}")
    for u in unbound[:10]:
        print(f"  UNBOUND {md_cell(u)[:80]}")
    print(f"Intent needs-review: {len(needs_review)}")
    for r in needs_review[:10]:
        print(f"  REVIEW {md_cell(r)[:80]}")
    # Verdict shares freshness_verdict() with status: NEEDS_SYNC iff any
    # content-stale file (same hash check), so the two never disagree.
    verdict = verdict_pre
    print(f"Changed (git signal): {len(verdict['git_changed'])}")
    print(f"Stale (content differs): {len(stale_files)}")
    for f in sorted(stale_files)[:10]:
        print(f"  STALE {f}")
    bad = broken or dupes or missing_files or no_evidence or bad_kinds \
        or bad_edges or bad_id_nodes or bad_id_edges \
        or dangling or unbound or needs_review
    # "Status:" is the integrity verdict (map hygiene); "Freshness verdict:"
    # is the staleness verdict shared with status (hash check). They answer
    # different questions on purpose: a freshly-synced map can still carry
    # integrity findings, and that must not read as drift. Stale content
    # always reports NEEDS_SYNC (agents act on that word); hygiene-only
    # findings on a fresh map report NEEDS_ATTENTION so the two words are
    # never conflated.
    if stale_files:
        status_word = "NEEDS_SYNC"
    elif bad:
        status_word = "NEEDS_ATTENTION"
    else:
        status_word = "OK"
    print("Status:", status_word)
    print("Freshness verdict:", verdict["verdict"])
    return 1 if (bad or stale_files) else 0


def find_nodes(graph, text):
    tl = text.lower()
    return [n for n in graph["nodes"]
            if tl in n["name"].lower() or tl in n["id"].lower()]


# ---------------------------------------------------------------------------
# Reusable analysis (pure functions over a loaded graph).
#
# `cmd_impact` / `cmd_flow` below are thin print wrappers around these; the
# visualization layer (`viz.py`) imports the same functions so there is only
# ONE impact/flow engine, never two divergent implementations.


def graph_indexes(graph):
    """by_id, incoming, outgoing adjacency over RESOLVED edges only."""
    by_id = {n["id"]: n for n in graph["nodes"]}
    incoming, outgoing = {}, {}
    _ids = {n["id"] for n in graph["nodes"]}
    for e in graph["edges"]:
        if G.is_placeholder(e["dst"], _ids):
            continue
        incoming.setdefault(e["dst"], []).append(e)
        outgoing.setdefault(e["src"], []).append(e)
    return by_id, incoming, outgoing


REACH_EDGE_TYPES = frozenset({
    "calls", "handled-by", "injects", "consumes", "navigates", "defines",
    "references", "reads", "writes", "queries", "invokes", "exposes",
    "transforms", "publishes", "extends", "implements",
})

ENTRY_KINDS = ("endpoint", "route", "queue", "topic", "event", "job",
               "schedule")
CONSUMER_KINDS = ("component", "service", "route")


def node_label(by_id, nid):
    n = by_id.get(nid)
    if not n:
        return nid
    lang = n.get("meta", {}).get("lang", "")
    return f"{n['name']} ({n['kind']})" + (f" <{lang}>" if lang else "")


def impact_analysis(graph, symbol, depth=6, incoming_cap=20,
                    outgoing_cap=20):
    """Blast radius for the first node(s) matching `symbol`.

    Returns a list of dicts (one per matched target, max 5):
      target, callers[{edge,node}], callees[{edge,node}],
      entry_points[names], consumers[names].
    Raises LookupError when nothing matches.
    """
    hits = find_nodes(graph, symbol)
    if not hits:
        raise LookupError(f"No nodes match '{symbol}'.")
    by_id, incoming, outgoing = graph_indexes(graph)
    results = []
    for h in hits[:5]:
        seen_eps, seen_fe, frontier = set(), set(), {h["id"]}
        visited = set()
        for _ in range(depth):
            nxt = set()
            for fid in frontier:
                for e in incoming.get(fid, []):
                    if e["src"] in visited:
                        continue
                    visited.add(e["src"])
                    n = by_id.get(e["src"])
                    if n and n["kind"] in ENTRY_KINDS:
                        seen_eps.add(n["name"])
                    if n and n["kind"] in CONSUMER_KINDS:
                        seen_fe.add(n["name"])
                    if e["type"] in REACH_EDGE_TYPES:
                        nxt.add(e["src"])
            frontier = nxt
            if not frontier:
                break
        results.append({
            "target": h,
            "callers": [{"edge": e,
                         "node": by_id.get(e["src"])}
                        for e in incoming.get(h["id"], [])[:incoming_cap]],
            "callees": [{"edge": e,
                         "node": by_id.get(e["dst"])}
                        for e in outgoing.get(h["id"], [])[:outgoing_cap]],
            "entry_points": sorted(seen_eps)[:15],
            "consumers": sorted(seen_fe)[:15],
        })
    return results


def flow_path(graph, from_text, to_text, per_seed=3, fanout_cap=50):
    """Shortest resolved-edge path(s) from `from_text` to `to_text`.

    Returns a list of dicts: seed, path[node...], found(bool).
    Raises LookupError when either side has no match.
    """
    by_id = {n["id"]: n for n in graph["nodes"]}
    srcs = find_nodes(graph, from_text)
    dsts = find_nodes(graph, to_text)
    if not srcs or not dsts:
        raise LookupError("No matching nodes for --from/--to.")
    adj = {}
    _ids = {n["id"] for n in graph["nodes"]}
    for e in graph["edges"]:
        if G.is_placeholder(e["dst"], _ids):
            continue
        adj.setdefault(e["src"], []).append((e["dst"], e["type"], 1))
        adj.setdefault(e["dst"], []).append((e["src"], e["type"] + "^", 1))
    target = {n["id"] for n in dsts}
    results = []
    for s in srcs[:per_seed]:
        prev = {s["id"]: None}
        queue = [s["id"]]
        found = None
        while queue:
            cur = queue.pop(0)
            if cur in target:
                found = cur
                break
            for nb, _, _ in adj.get(cur, [])[:fanout_cap]:
                if nb not in prev:
                    prev[nb] = cur
                    queue.append(nb)
        if not found:
            results.append({"seed": s, "path": [], "found": False})
            continue
        path = [found]
        while prev[path[-1]] is not None:
            path.append(prev[path[-1]])
        path.reverse()
        results.append({"seed": s,
                        "path": [by_id.get(p, {"id": p}) for p in path],
                        "found": True})
    return results


def map_freshness(root, map_dir=DEFAULT_MAP_DIR):
    """Freshness of the map vs working tree. Read-only; never mutates.

    Returns dict: exists, changed[relpaths], stale(bool).
    Only files the scanner tracks count: map-dir-internal churn (derived
    views rewritten by sync/visualize) and untracked scannable files are
    ignored — staleness means a TRACKED source file changed.
    """
    root = os.path.abspath(root)
    paths = map_paths(root, map_dir)
    if not os.path.exists(paths["graph"]):
        return {"exists": False, "changed": [], "stale": True}
    graph = load_graph(paths)
    base = graph.get("last_sync_commit")
    changed = set(changed_since(root, base, map_dir))
    map_base = _map_rel_base(root, map_dir) or ""
    hashes = load_hashes(paths)
    if not changed and hashes:
        # No git signal (non-git root, or git blind to the edit): fall back
        # to the hash check, mirroring cmd_sync's second pass.
        extra = load_gitignore_basenames(root)
        for rel, ap in iter_repo_files(root, extra, map_dir):
            if rel.startswith(map_base + "/") or rel not in hashes:
                continue
            try:
                if sha256_file(ap) != hashes[rel]:
                    changed.add(rel)
            except OSError:
                changed.add(rel)
    relevant = set()
    for c in changed:
        if c.startswith(map_base + "/"):
            continue  # derived views / viz output churn
        if c not in hashes:
            continue  # untracked-by-map file: sync hash check owns this
        ap = os.path.join(root, c)
        try:
            if os.path.isfile(ap) and sha256_file(ap) != hashes[c]:
                relevant.add(c)
        except OSError:
            relevant.add(c)
    return {"exists": True, "changed": sorted(relevant),
            "stale": bool(relevant)}


def cmd_query(args):
    root = os.path.abspath(args.root)
    paths = map_paths(root, args.map_dir)
    graph = load_graph(paths)
    if graph.get("version") != VERSION:
        print(f"Map schema v{graph.get('version')} != tool v{VERSION}; "
              "run `init --full`.")
        return 2
    results = graph["nodes"]
    if args.kind:
        results = [n for n in results if n["kind"] == args.kind]
    if args.name:
        tl = args.name.lower()
        results = [n for n in results
                   if tl in n["name"].lower() or tl in n["id"].lower()]
    if args.lang:
        results = [n for n in results
                   if n.get("meta", {}).get("lang") == args.lang]
    for n in results[:args.limit]:
        lang = n.get("meta", {}).get("lang", "")
        print(f"{n['kind']:16} {n['name']}  "
              f"[{n['file']}{':' + str(n['line']) if n.get('line') else ''}] "
              f"({n['confidence']})" + (f" <{lang}>" if lang else ""))
    print(f"-- {len(results)} match(es) --")
    return 0


def cmd_impact(args):
    root = os.path.abspath(args.root)
    paths = map_paths(root, args.map_dir)
    graph = load_graph(paths)
    try:
        results = impact_analysis(graph, args.symbol)
    except LookupError as exc:
        print(exc)
        return 1
    by_id = {n["id"]: n for n in graph["nodes"]}
    empty = True
    for r in results:
        h = r["target"]
        if r["callers"] or r["callees"] or r["entry_points"] \
                or r["consumers"]:
            empty = False
        print(f"=== BLAST RADIUS: {h['name']} [{h['id']}] ===")
        print("called by / depended on by:")
        for c in r["callers"]:
            print(f"  [{c['edge']['type']}] "
                  f"{node_label(by_id, c['edge']['src'])} "
                  f"({c['edge']['confidence']})")
        print("calls / depends on:")
        for c in r["callees"]:
            print(f"  [{c['edge']['type']}] "
                  f"{node_label(by_id, c['edge']['dst'])} "
                  f"({c['edge']['confidence']})")
        print("reachable entry points:")
        for x in r["entry_points"]:
            print(f"  {x}")
        print("reachable frontend consumers:")
        for x in r["consumers"]:
            print(f"  {x}")
    if empty:
        print(f"IMPACT EMPTY: '{args.symbol}' has no resolved "
              f"relationships in the map — isolated node. Hint: it may be "
              f"genuinely disconnected, or cross-file links failed to "
              f"resolve (check unresolved refs).")
        return 1
    return 0


def _reachable_from(graph, src_id, cap=15):
    """Node names reachable from src_id over resolved edges (BFS, capped)."""
    by_id = {n["id"]: n for n in graph["nodes"]}
    ids = set(by_id)
    adj = {}
    for e in graph["edges"]:
        if G.is_placeholder(e["dst"], ids):
            continue
        adj.setdefault(e["src"], []).append(e["dst"])
    seen, queue, names = {src_id}, [src_id], []
    while queue and len(names) < cap:
        cur = queue.pop(0)
        for nb in adj.get(cur, []):
            if nb not in seen:
                seen.add(nb)
                queue.append(nb)
                names.append(by_id.get(nb, {}).get("name", nb))
    return names


def cmd_flow(args):
    root = os.path.abspath(args.root)
    paths = map_paths(root, args.map_dir)
    graph = load_graph(paths)
    try:
        results = flow_path(graph, args.from_, args.to)
    except LookupError:
        print("No matching nodes for --from/--to.")
        return 1
    by_id = {n["id"]: n for n in graph["nodes"]}
    any_found = False
    for r in results:
        if not r["found"]:
            reach = _reachable_from(graph, r["seed"]["id"])
            print(f"NO-PATH: no path from '{r['seed']['name']}' "
                  f"to '{args.to}'.")
            if reach:
                print(f"  Reachable from source ({len(reach)} shown): "
                      f"{', '.join(reach)}")
            else:
                print("  Reachable from source: (nothing — source is "
                      "isolated in the resolved graph)")
            print("  Likely cause: missing cross-file resolution "
                  "(unresolved refs), confidence-filtered edges, or the "
                  "two symbols are genuinely disconnected.")
            continue
        any_found = True
        print(f"FLOW: {' → '.join(by_id.get(p['id'], {}).get('name', p['id']) for p in r['path'])}")
        for p in r["path"]:
            n = by_id.get(p["id"], {})
            print(f"  - {n.get('name', p['id'])} [{n.get('kind', '?')}] "
                  f"`{n.get('file', '?')}` ({n.get('confidence', '?')})")
    return 0 if any_found else 1


def cmd_detect(args):
    root = os.path.abspath(args.root)
    paths = map_paths(root, args.map_dir)
    graph = load_graph(paths)
    print(detection_markdown(graph.get("detection")))
    return 0


def cmd_visualize(args):
    try:
        from . import viz_gen as VG
    except ImportError:
        import viz_gen as VG
    return VG.generate(
        args.root, map_dir=args.map_dir, mode=args.kind,
        symbol=args.symbol, impact=args.impact, flow=args.flow,
        depth=args.depth, allow_stale=args.allow_stale,
        open_browser=args.open, serve=args.serve,
        serve_port=args.serve_port)


# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".",
                    help="repository root (default: cwd)")
    ap.add_argument("--map-dir", default=DEFAULT_MAP_DIR,
                    help="map directory inside the repo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="build the map from scratch")
    p_init.add_argument("--full", action="store_true",
                        help="rebuild even if a map exists")
    p_init.set_defaults(fn=cmd_init)
    p_sync = sub.add_parser("sync", help="incrementally synchronize the map")
    p_sync.set_defaults(fn=cmd_sync)
    p_status = sub.add_parser("status", help="show map freshness")
    p_status.set_defaults(fn=cmd_status)
    p_val = sub.add_parser("validate", help="validate map integrity")
    p_val.set_defaults(fn=cmd_validate)
    p_q = sub.add_parser("query", help="list nodes by kind/name")
    p_q.add_argument("--kind", default=None)
    p_q.add_argument("--name", default=None)
    p_q.add_argument("--lang", default=None,
                     help="filter by analyzer language (meta.lang)")
    p_q.add_argument("--limit", type=int, default=40)
    p_q.set_defaults(fn=cmd_query)
    p_imp = sub.add_parser("impact", help="blast-radius of a symbol")
    p_imp.add_argument("symbol")
    p_imp.set_defaults(fn=cmd_impact)
    p_flow = sub.add_parser("flow", help="trace a path between symbols")
    p_flow.add_argument("--from", "--from_", dest="from_", required=True)
    p_flow.add_argument("--to", required=True)
    p_flow.set_defaults(fn=cmd_flow)
    p_det = sub.add_parser("detect", help="show detected stack")
    p_det.set_defaults(fn=cmd_detect)
    p_int = sub.add_parser("intent", help="intent layer (asserted claims)")
    p_int_sub = p_int.add_subparsers(dest="intent_cmd", required=True)
    p_imp = p_int_sub.add_parser("import", help="parse docs/*.md into "
                                 "intent nodes (idempotent)")
    p_imp.set_defaults(fn=cmd_intent)
    p_bind = p_int_sub.add_parser("bind", help="attach code nodes to "
                                  "intent nodes")
    p_bind.add_argument("--slice", required=True,
                        help="intent slice/requirement/capability id or name")
    p_bind.add_argument("--realizes", required=True,
                        help="intent requirement/capability id or name")
    p_bind.add_argument("--nodes", required=True, nargs="+",
                        action="append",
                        help="code node ids or names (space-separated; "
                        "comma-separated also accepted; repeatable)")
    p_bind.add_argument("--why", required=True,
                        help="plain-language reason for the binding")
    p_bind.add_argument("--author", default=None,
                        help="binding author (default: agent)")
    p_bind.add_argument("--decision", default=None,
                        help="intent decision id (adds motivated-by edges)")
    p_bind.add_argument("--declares-files", dest="declares_files",
                        nargs="*", default=None,
                        help="pre-implementation file declaration "
                        "(workflow slice step 1: stored verbatim for "
                        "later drift comparison)")
    p_bind.set_defaults(fn=cmd_intent)
    p_why = sub.add_parser("why", help="reverse lookup: code -> intent")
    p_why.add_argument("node", help="symbol, endpoint, file or node id")
    p_why.set_defaults(fn=cmd_why)
    p_resp = sub.add_parser("responsible-for", help="forward lookup: "
                            "intent -> realizing code + tests")
    p_resp.add_argument("intent_id", help="intent node id or name")
    p_resp.set_defaults(fn=cmd_responsible_for)
    p_viz = sub.add_parser("visualize",
                           help="generate interactive HTML visualization")
    p_viz.add_argument("--kind", default="architecture",
                       help="default mode: architecture|dependency|call-graph"
                            "|data-flow|api|database|external|impact|flow")
    p_viz.add_argument("--symbol", default=None,
                       help="focus a symbol (neighborhood view)")
    p_viz.add_argument("--impact", default=None,
                       help="blast-radius focus (reuses impact engine)")
    p_viz.add_argument("--flow", default=None,
                       help='flow focus, e.g. "POST /api/claims" or "A -> B"')
    p_viz.add_argument("--depth", type=int, default=2,
                       help="focus neighborhood depth (default: 2)")
    p_viz.add_argument("--allow-stale", action="store_true",
                       help="generate even when the map is stale")
    p_viz.add_argument("--open", action="store_true",
                       help="open in the default browser")
    p_viz.add_argument("--serve", action="store_true",
                       help="serve locally over HTTP (offline, stdlib only)")
    p_viz.add_argument("--serve-port", type=int, default=8734)
    p_viz.set_defaults(fn=cmd_visualize)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
