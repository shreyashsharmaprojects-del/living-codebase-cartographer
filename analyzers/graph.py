"""Generic graph model: closed vocabularies, node/edge helpers, detection record.

Provenance (Wave 4a / schema 5.2):
  DERIVED  = analyzer output from code. Carries file/line evidence plus a
             HIGH/MEDIUM/LOW/UNKNOWN confidence. Everything the scanner
             produced before Wave 4a is DERIVED.
  ASSERTED = human/agent claim (intent layer: capabilities, requirements,
             concepts, slices, decisions, non-goals, and their bindings).
             Carries author, timestamp, asserted_commit and a source doc
             (file+line). ASSERTED entries carry NO confidence level —
             confidence is the wrong axis for a claim; it is either the
             current human position (status active) or it is not.
Every node/edge dict exposes provenance via ``node.get("provenance",
"derived")`` / ``edge.get("provenance", "derived")`` (lowercase on the
wire; absent means derived — pre-4a graphs stay valid).
"""

VERSION = 3

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
    # Intent layer (Wave 4a, ASSERTED provenance): human/agent claims
    # parsed from docs/*.md by `intent import` or written by `intent bind`.
    # Never emitted by analyzers; never inferred.
    "capability", "requirement", "concept", "slice", "decision", "non-goal",
})

# Technology-independent relationship types. Same rule: no tech-specific types.
EDGE_TYPES = frozenset({
    "contains", "imports", "references", "calls", "implements", "extends",
    "depends-on", "injects", "exposes", "consumes", "publishes",
    "reads", "writes", "queries", "invokes", "transforms",
    "configures", "authenticates", "authorizes", "tests", "deploys-to",
    "defines", "handled-by", "guarded-by", "navigates", "creates",
    "modifies", "seeds", "triggered-by", "maps-to",
    # Intent-layer relationships (Wave 4a, ASSERTED provenance).
    # `violates` is DEFERRED (later wave) and intentionally absent.
    "realizes", "part-of", "denotes", "motivated-by", "delivered-in",
})

CONFIDENCES = ("HIGH", "MEDIUM", "LOW", "UNKNOWN")

# Intent-layer node kinds: ASSERTED provenance, never emitted by analyzers.
INTENT_KINDS = frozenset({
    "capability", "requirement", "concept", "slice", "decision", "non-goal",
})

# Intent-layer edge types: ASSERTED provenance. `violates` deferred.
INTENT_EDGE_TYPES = frozenset({
    "realizes", "part-of", "denotes", "motivated-by", "delivered-in",
})

# Intent node ids carry this prefix, so bindings and intent nodes are
# trivially enumerable and never collide with analyzer-emitted ids.
INTENT_ID_PREFIX = "intent:"

# Intent node lifecycle. `active` = current human position; `superseded` =
# replaced by a newer import (same id, new title/body); `needs-review` =
# a bound code node changed materially or vanished (reason recorded in
# meta.review_reason). Coverage of needs-review marking: see core.sync.
INTENT_STATUSES = ("active", "superseded", "needs-review")

# Provenance values (lowercase on the wire). Absent == "derived".
PROVENANCES = ("derived", "asserted")

# CLI marker for ASSERTED entries: every intent node/edge printed by the
# CLI carries this prefix/suffix so the two provenances are visually
# distinguishable at a glance.
ASSERTED_MARKER = "[ASSERTED]"

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


def _check_provenance(provenance, kind=None, etype=None):
    if provenance not in PROVENANCES:
        raise ValueError(f"unknown provenance: {provenance}")
    if provenance == "asserted":
        if kind is not None and kind not in INTENT_KINDS:
            raise ValueError(
                f"asserted nodes must use an intent kind, got: {kind}")
        if etype is not None and etype not in INTENT_EDGE_TYPES:
            raise ValueError(
                f"asserted edges must use an intent edge type, got: {etype}")
    return provenance


def _check_assertion_fields(kind, title=None, source=None, author=None,
                            asserted_at=None, asserted_commit=None,
                            status="active"):
    """Every intent (ASSERTED) node stores: id, kind, title, body, source
    (doc file+line), author, asserted_at, asserted_commit,
    status (active/superseded/needs-review)."""
    if kind not in INTENT_KINDS:
        return
    if status not in INTENT_STATUSES:
        raise ValueError(f"unknown intent status: {status}")
    missing = [k for k, v in (("title", title), ("source", source),
                              ("author", author),
                              ("asserted_at", asserted_at))
               if not v]
    if missing:
        raise ValueError(
            f"asserted {kind} node missing required fields: "
            f"{', '.join(missing)}")


def add_node(graph, nid, kind, name, evidence_file, line=None,
             confidence="HIGH", meta=None, commit=None, provenance="derived",
             title=None, body=None, source=None, author=None,
             asserted_at=None, asserted_commit=None, status="active"):
    if kind not in NODE_KINDS:
        raise ValueError(f"unknown node kind: {kind}")
    _check_provenance(provenance, kind=kind)
    if provenance == "asserted":
        if confidence is not None:
            raise ValueError(
                "asserted nodes carry no confidence level — confidence is "
                "the wrong axis for a claim")
        _check_assertion_fields(kind, title, source, author, asserted_at,
                                asserted_commit, status)
    elif confidence not in CONFIDENCES:
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
        "evidence": "assertion" if provenance == "asserted" else "source-code",
        "confidence": None if provenance == "asserted" else confidence,
        "last_verified_commit": commit,
        "provenance": provenance,
        "meta": meta or {},
    }
    if provenance == "asserted":
        node.update({
            "title": title,
            "body": body or "",
            "source": source,
            "author": author,
            "asserted_at": asserted_at,
            "asserted_commit": asserted_commit,
            "status": status,
        })
    graph["nodes"].append(node)
    return node


def add_edge(graph, src, dst, etype, evidence_file, line=None,
             confidence="MEDIUM", meta=None, commit=None,
             provenance="derived", author=None, asserted_at=None,
             asserted_commit=None):
    if etype not in EDGE_TYPES:
        raise ValueError(f"unknown edge type: {etype}")
    _check_provenance(provenance, etype=etype)
    if provenance == "asserted":
        if confidence is not None:
            raise ValueError(
                "asserted edges carry no confidence level — confidence is "
                "the wrong axis for a claim")
        if not author or not asserted_at:
            raise ValueError(
                "asserted edges require author and asserted_at")
    elif confidence not in CONFIDENCES:
        raise ValueError(f"unknown confidence: {confidence}")
    for e in graph["edges"]:
        if e["src"] == src and e["dst"] == dst and e["type"] == etype:
            return e
    edge = {
        "src": src, "dst": dst, "type": etype,
        "file": evidence_file, "line": line,
        "evidence": "assertion" if provenance == "asserted" else "source-code",
        "confidence": None if provenance == "asserted" else confidence,
        "last_verified_commit": commit,
        "provenance": provenance,
        "meta": meta or {},
    }
    if provenance == "asserted":
        edge.update({
            "author": author,
            "asserted_at": asserted_at,
            "asserted_commit": asserted_commit,
        })
    graph["edges"].append(edge)
    return edge


def is_asserted(entry):
    """True when a node/edge dict is an ASSERTED (intent-layer) entry."""
    return entry.get("provenance") == "asserted"


def asserted_label(entry):
    """CLI display suffix/prefix marker: ASSERTED entries are tagged."""
    if is_asserted(entry):
        return ASSERTED_MARKER
    return ""
