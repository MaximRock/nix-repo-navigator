"""Diff engine: compare two :class:`ParseResult` snapshots.

Used only for logging / reporting; it never mutates the graph.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from repo_navigator.models.edges import RawEdge
from repo_navigator.models.nodes import RawNode
from repo_navigator.models.queries import ParseResult


class DiffReport(BaseModel):
    """Result of :func:`diff_graph`."""

    added_nodes: list[RawNode] = Field(default_factory=list)
    removed_nodes: list[RawNode] = Field(default_factory=list)
    changed_nodes: list[RawNode] = Field(default_factory=list)
    added_edges: list[RawEdge] = Field(default_factory=list)
    removed_edges: list[RawEdge] = Field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(
            self.added_nodes
            or self.removed_nodes
            or self.changed_nodes
            or self.added_edges
            or self.removed_edges
        )


def _edge_key(e: RawEdge) -> str:
    """Stable key for an edge: source->type->target plus metadata hash.

    For diff purposes we use the same deterministic id logic as the builder
    would, but if an explicit id is not present we synthesize one from the
    edge's fields.  Since ``RawEdge`` has no ``id``, we use
    ``source|type|target|metadata``.
    """
    # Use sorted metadata for stability, exclude volatile 'line'
    meta = {k: v for k, v in e.metadata.items() if k != "line"}
    meta_str = str(sorted(meta.items())) if meta else ""
    return f"{e.source}|{e.type.value}|{e.target}|{meta_str}|{e.weight}"


def diff_graph(old: ParseResult, new: ParseResult) -> DiffReport:
    """Compare two :class:`ParseResult` snapshots.

    Nodes are keyed by ``id``.  Edges are keyed by
    ``(source, type, target, metadata, weight)`` so that an edge with a
    changed ``conditional`` flag is reported as removed+added rather than
    changed (edges are value types).
    """
    old_nodes: dict[str, RawNode] = {n.id: n for n in old.nodes}
    new_nodes: dict[str, RawNode] = {n.id: n for n in new.nodes}

    old_ids = set(old_nodes)
    new_ids = set(new_nodes)

    added_nodes = [new_nodes[nid] for nid in new_ids - old_ids]
    removed_nodes = [old_nodes[nid] for nid in old_ids - new_ids]

    changed_nodes: list[RawNode] = []
    for nid in old_ids & new_ids:
        old_dump = old_nodes[nid].model_dump(mode="json")
        new_dump = new_nodes[nid].model_dump(mode="json")
        if old_dump != new_dump:
            changed_nodes.append(new_nodes[nid])

    # Edges: RawEdge has no id, so we key by content
    old_edges: dict[str, RawEdge] = {_edge_key(e): e for e in old.edges}
    new_edges: dict[str, RawEdge] = {_edge_key(e): e for e in new.edges}

    old_e_keys = set(old_edges)
    new_e_keys = set(new_edges)

    added_edges = [new_edges[k] for k in new_e_keys - old_e_keys]
    removed_edges = [old_edges[k] for k in old_e_keys - new_e_keys]

    return DiffReport(
        added_nodes=sorted(added_nodes, key=lambda n: n.id),
        removed_nodes=sorted(removed_nodes, key=lambda n: n.id),
        changed_nodes=sorted(changed_nodes, key=lambda n: n.id),
        added_edges=sorted(added_edges, key=lambda e: (e.source, e.target, e.type.value)),
        removed_edges=sorted(removed_edges, key=lambda e: (e.source, e.target, e.type.value)),
    )
