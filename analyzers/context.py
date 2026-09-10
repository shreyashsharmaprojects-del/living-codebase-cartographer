"""Per-file scan context passed to analyzers."""


class ScanContext:
    """Thin facade over the generic graph bound to one file + commit."""

    def __init__(self, graph, add_node, add_edge, path, commit):
        self._graph = graph
        self._add_node = add_node
        self._add_edge = add_edge
        self.path = path
        self.commit = commit

    def node(self, nid, kind, name, line=None, confidence="HIGH", meta=None):
        return self._add_node(self._graph, nid, kind, name, self.path,
                              line, confidence, meta, self.commit)

    def edge(self, src, dst, etype, line=None, confidence="MEDIUM", meta=None):
        return self._add_edge(self._graph, src, dst, etype, self.path,
                              line, confidence, meta, self.commit)

    def scan_error(self, exc):
        self._graph.setdefault("scan_errors", []).append(f"{self.path}: {exc}")
