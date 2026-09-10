"""Generic graph model: closed vocabularies, node/edge helpers, detection record."""

VERSION = 2

# Technology-independent node categories. Analyzers MUST use only these kinds;
# technology nuance goes in meta (lang/framework/stereotype/...).
NODE_KINDS = frozenset({
    "repository", "application", "module", "package", "directory", "file",
    "class", "interface", "enum", "function", "method",
    "component", "service", "controller", "handler", "endpoint",
    "route", "guard", "interceptor", "state",
    "event", "queue", "topic", "job", "schedule",
    "database", "table", "view", "query", "procedure", "sequence",
    "collection", "cache", "cache-key", "entity", "stereotype",
    "external-service", "configuration", "environment", "configuration-key",
    "deployment-unit", "test", "test-case", "migration", "ci-job",
})

# Technology-independent relationship types. Same rule: no tech-specific types.
EDGE_TYPES = frozenset({
    "contains", "imports", "references", "calls", "implements", "extends",
    "depends-on", "injects", "exposes", "consumes", "publishes",
    "reads", "writes", "queries", "invokes", "transforms",
    "configures", "authenticates", "authorizes", "tests", "deploys-to",
    "defines", "handled-by", "guarded-by", "navigates", "creates",
    "modifies", "seeds", "triggered-by",
})

CONFIDENCES = ("HIGH", "MEDIUM", "LOW", "UNKNOWN")

# Node kinds that trigger an architectural change record on sync.
SIGNIFICANT_KINDS = frozenset({
    "controller", "service", "handler", "endpoint", "route",
    "class", "interface", "component", "table", "view", "procedure",
    "collection", "queue", "topic", "event", "job", "migration",
    "external-service", "database", "deployment-unit",
})

# Edge targets that are references without a resolved node yet.
# Validation treats them as navigable-but-unverified, never as broken.
# "unresolved:" is always a placeholder. Other bare-name references
# (table:/entity:/sequence:/...) are placeholders ONLY when no node with
# that id exists in the graph — resolve-time code must check membership.
UNRESOLVED_PREFIX = "unresolved:"
PLACEHOLDER_PREFIXES = (
    "unresolved:", "table:", "entity:", "env:", "config-key:",
    "sequence:", "stereotype:", "schedule:",
)


def is_placeholder(dst, ids=None):
    """True when `dst` has no node to navigate to.

    With the graph's id set, bare-name refs (table:claim) resolve when
    their node exists and only dangle when it does not. Without ids
    (streaming scan context), any non-unresolved target is assumed
    navigable and only "unresolved:" is a placeholder.
    """
    if dst.startswith(UNRESOLVED_PREFIX):
        return True
    if ids is None:
        return False
    if dst in ids:
        return False
    return dst.startswith(PLACEHOLDER_PREFIXES[1:])


def new_graph(root, commit=None, time=None):
    return {
        "version": VERSION,
        "root": root,
        "init_commit": commit,
        "last_sync_commit": commit,
        "last_sync_time": time,
        "nodes": [],
        "edges": [],
        "flow_candidates": [],
        "unresolved": [],
        "detection": {},
    }


def add_node(graph, nid, kind, name, evidence_file, line=None,
             confidence="HIGH", meta=None, commit=None):
    if kind not in NODE_KINDS:
        raise ValueError(f"unknown node kind: {kind}")
    if confidence not in CONFIDENCES:
        raise ValueError(f"unknown confidence: {confidence}")
    for n in graph["nodes"]:
        if n["id"] == nid:
            return n
    node = {
        "id": nid,
        "kind": kind,
        "name": name,
        "file": evidence_file,
        "line": line,
        "relationship": "defines",
        "evidence": "source-code",
        "confidence": confidence,
        "last_verified_commit": commit,
        "meta": meta or {},
    }
    graph["nodes"].append(node)
    return node


def add_edge(graph, src, dst, etype, evidence_file, line=None,
             confidence="MEDIUM", meta=None, commit=None):
    if etype not in EDGE_TYPES:
        raise ValueError(f"unknown edge type: {etype}")
    if confidence not in CONFIDENCES:
        raise ValueError(f"unknown confidence: {confidence}")
    for e in graph["edges"]:
        if e["src"] == src and e["dst"] == dst and e["type"] == etype:
            return e
    edge = {
        "src": src, "dst": dst, "type": etype,
        "file": evidence_file, "line": line,
        "evidence": "source-code",
        "confidence": confidence,
        "last_verified_commit": commit,
        "meta": meta or {},
    }
    graph["edges"].append(edge)
    return edge
