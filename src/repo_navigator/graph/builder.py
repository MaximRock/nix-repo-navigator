"""Graph builder: turns :class:`ParseResult` into persisted graph state.

Coordinates writes to SQLite (source of truth) and the in-memory
:class:`NxGraph` (derived view).  For every file the sub-graph is
fully replaced: old file-owned nodes/edges are removed, new ones are
inserted, external targets get synthetic placeholders so foreign-key
constraints and ``NxGraph`` edges stay valid.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.models.edges import Edge, EdgeType, RawEdge
from repo_navigator.models.nodes import Node, NodeType, RawNode
from repo_navigator.models.queries import ParseResult

log = logging.getLogger(__name__)


class GraphBuilder:
    """Builds the graph from parser outputs."""

    def __init__(self, db: Database, nx_graph: NxGraph) -> None:
        self.db = db
        self.nx_graph = nx_graph

    # ------------------------------------------------------------------ public

    def build_file(self, path: Path | str, parse_result: ParseResult) -> None:
        """Full replacement of the sub-graph owned by *path*."""
        path_str = str(path)

        # Snapshot old state before mutation (for delta).
        old_nodes = [n for n in self.db.get_all_nodes() if n.path == path_str]
        old_edges = self.db.get_edges_for_file(path_str)
        old_node_ids = {n.id for n in old_nodes}
        old_edge_ids = {e.id for e in old_edges}

        # Prepare new state (deterministic ids, placeholders).
        new_nodes, new_edges = self._prepare(parse_result, path_str)

        # ---- SQLite: replace file-owned sub-graph ------------------------
        # Delete old outgoing edges explicitly (avoid orphaned edges when
        # we later delete nodes with FK OFF).
        with self.db._lock, self.db.transaction():
            if old_edge_ids:
                placeholders = ",".join("?" for _ in old_edge_ids)
                self.db._conn.execute(
                    f"DELETE FROM edges WHERE id IN ({placeholders})",
                    tuple(old_edge_ids),
                )
            # Delete file-owned nodes with FK OFF to preserve incoming
            # edges from other files (e.g. A imports B).
            self.db._conn.execute("PRAGMA foreign_keys=OFF")
            try:
                self.db._conn.execute("DELETE FROM nodes WHERE path=?", (path_str,))
            finally:
                self.db._conn.execute("PRAGMA foreign_keys=ON")

        # Insert new nodes (including placeholders for external targets).
        for node in new_nodes:
            self.db.upsert_node(node)

        # Ensure every edge target exists (placeholder already in new_nodes).
        for edge in new_edges:
            self.db.upsert_edge(edge)

        # ---- NxGraph delta ----------------------------------------------
        new_node_ids = {n.id for n in new_nodes}
        new_edge_ids = {e.id for e in new_edges}

        removed_node_ids = list(old_node_ids - new_node_ids)
        # For edges we already deleted old outgoing, but we need to tell
        # NxGraph which edges to drop and which to add.
        removed_edge_ids = list(old_edge_ids - new_edge_ids)

        self.nx_graph.apply_delta(
            added_nodes=new_nodes,
            removed_node_ids=removed_node_ids,
            added_edges=new_edges,
            removed_edge_ids=removed_edge_ids,
        )

        self.db.inc_generation_id()

    def build_all(self, items: list[tuple[Path | str, ParseResult]]) -> None:
        """Bulk rebuild from *items* (full rescan).

        *items* is a list of ``(path, ParseResult)`` pairs.  All file-owned
        nodes/edges are replaced and the in-memory graph is rebuilt from
        the DB snapshot.  ``generation_id`` is incremented once.
        """
        # Collect all paths being rebuilt.
        paths = [str(p) for p, _ in items]

        # Snapshot old file-owned nodes/edges for the given paths.
        old_nodes = [n for n in self.db.get_all_nodes() if n.path in paths]
        old_node_ids = {n.id for n in old_nodes}
        old_edges: list[Edge] = []
        for p in paths:
            old_edges.extend(self.db.get_edges_for_file(p))
        old_edge_ids = {e.id for e in old_edges}

        # ---- SQLite: bulk delete ----------------------------------------
        with self.db._lock, self.db.transaction():
            if old_edge_ids:
                placeholders = ",".join("?" for _ in old_edge_ids)
                self.db._conn.execute(
                    f"DELETE FROM edges WHERE id IN ({placeholders})",
                    tuple(old_edge_ids),
                )
            self.db._conn.execute("PRAGMA foreign_keys=OFF")
            try:
                if paths:
                    placeholders = ",".join("?" for _ in paths)
                    self.db._conn.execute(
                        f"DELETE FROM nodes WHERE path IN ({placeholders})",
                        tuple(paths),
                    )
            finally:
                self.db._conn.execute("PRAGMA foreign_keys=ON")

        # Prepare and insert new state.
        all_new_nodes: list[Node] = []
        all_new_edges: list[Edge] = []
        for path, parse_result in items:
            path_str = str(path)
            new_nodes, new_edges = self._prepare(parse_result, path_str)
            all_new_nodes.extend(new_nodes)
            all_new_edges.extend(new_edges)

        # Deduplicate by id (last wins, e.g. same nix_option declared twice).
        dedup_nodes: dict[str, Node] = {}
        for n in all_new_nodes:
            dedup_nodes[n.id] = n
        dedup_edges: dict[str, Edge] = {}
        for e in all_new_edges:
            dedup_edges[e.id] = e

        for node in dedup_nodes.values():
            self.db.upsert_node(node)
        for edge in dedup_edges.values():
            self.db.upsert_edge(edge)

        # ---- NxGraph full rebuild ---------------------------------------
        # Rebuild from DB to guarantee consistency (includes placeholders
        # and nodes from files not in this batch that already existed).
        all_nodes = self.db.get_all_nodes()
        all_edges = self.db.get_all_edges()
        self.nx_graph.rebuild(nodes=all_nodes, edges=all_edges)

        self.db.inc_generation_id()

    # ---------------------------------------------------------------- helpers

    def _prepare(
        self, parse_result: ParseResult, path_str: str
    ) -> tuple[list[Node], list[Edge]]:
        """Convert raw nodes/edges to persisted models + placeholders."""
        # RawNode -> Node
        nodes: list[Node] = []
        seen_ids: set[str] = set()
        for raw in parse_result.nodes:
            if raw.id in seen_ids:
                continue
            seen_ids.add(raw.id)
            nodes.append(_raw_to_node(raw))

        # Collect ids for placeholder check.
        node_ids = {n.id for n in nodes}

        # RawEdge -> Edge (deterministic id).
        edges: list[Edge] = []
        seen_edge_ids: set[str] = set()
        for raw in parse_result.edges:
            edge = _raw_to_edge(raw)
            if edge.id in seen_edge_ids:
                continue
            seen_edge_ids.add(edge.id)
            edges.append(edge)

        # Placeholders for external targets missing from this ParseResult.
        # We also check DB: if target already exists as a node, no placeholder needed.
        placeholders: list[Node] = []
        placeholder_ids: set[str] = set()
        for edge in edges:
            if edge.target in node_ids or edge.target in placeholder_ids:
                continue
            if self.db.get_node(edge.target) is not None:
                continue
            ph = _placeholder_for_target(edge.target)
            if ph is not None and ph.id not in node_ids and ph.id not in placeholder_ids:
                placeholders.append(ph)
                placeholder_ids.add(ph.id)

        # Placeholders first (FK target must exist before edge insert).
        # Dedup placeholders by id as well.
        all_nodes = placeholders + nodes
        # Final dedup (in case placeholder collides with real node id).
        dedup: dict[str, Node] = {}
        for n in all_nodes:
            dedup[n.id] = n
        return list(dedup.values()), edges


# ------------------------------------------------------------------ converters


def _raw_to_node(raw: RawNode) -> Node:
    return Node(
        id=raw.id,
        type=raw.type,
        name=raw.name,
        path=raw.path,
        lang=raw.lang,
        metadata=dict(raw.metadata),
    )


def _raw_to_edge(raw: RawEdge) -> Edge:
    edge_id = _edge_id(raw.source, raw.type, raw.target, raw.metadata)
    return Edge(
        id=edge_id,
        source=raw.source,
        target=raw.target,
        type=raw.type,
        metadata=dict(raw.metadata),
        weight=float(raw.weight),
    )


def _edge_id(source: str, type_: EdgeType, target: str, metadata: dict) -> str:
    """Deterministic edge id. Metadata (except line) is hashed to avoid collisions."""
    base = f"{source}->{type_.value}->{target}"
    # Include a hash of metadata that affects semantics (conditional, priority, etc.)
    # but ignore volatile line numbers for stability.
    meta_for_hash = {k: v for k, v in metadata.items() if k != "line"}
    if not meta_for_hash:
        return base
    h = hashlib.sha256(str(sorted(meta_for_hash.items())).encode()).hexdigest()[:8]
    return f"{base}:{h}"


def _placeholder_for_target(target: str) -> Node | None:
    """Create a synthetic node for an external *target* id."""
    # Order matters: more specific prefixes first.
    if target.startswith("nix_option:"):
        attr = target.removeprefix("nix_option:")
        return Node(
            id=target,
            type=NodeType.nix_option,
            name=attr,
            path=None,
            lang="nix",
            metadata={"synthetic": True},
        )
    if target.startswith("nix_function:"):
        # nix_function:{path}:{name}
        rest = target.removeprefix("nix_function:")
        name = rest.rsplit(":", 1)[-1] if ":" in rest else rest
        return Node(
            id=target,
            type=NodeType.nix_function,
            name=name,
            path=None,
            lang="nix",
            metadata={"synthetic": True},
        )
    if target.startswith("nix_module:"):
        rest = target.removeprefix("nix_module:")
        return Node(
            id=target,
            type=NodeType.nix_module,
            name=rest,
            path=rest.split("::")[0] if "::" in rest else rest,
            lang="nix",
            metadata={"synthetic": True},
        )
    if target.startswith("nix:"):
        p = target.removeprefix("nix:")
        return Node(
            id=target,
            type=NodeType.nix_module,
            name=p,
            path=p,
            lang="nix",
            metadata={"synthetic": True},
        )
    if target.startswith("package:"):
        attr = target.removeprefix("package:")
        return Node(
            id=target,
            type=NodeType.package_ref,
            name=attr,
            path=None,
            lang="nix",
            metadata={"synthetic": True},
        )
    if target.startswith("file:"):
        p = target.removeprefix("file:")
        return Node(
            id=target,
            type=NodeType.file,
            name=p,
            path=None,
            lang="nix",
            metadata={"synthetic": True},
        )
    # Fallback: generic file node
    return Node(
        id=target,
        type=NodeType.file,
        name=target,
        path=None,
        lang="nix",
        metadata={"synthetic": True},
    )
