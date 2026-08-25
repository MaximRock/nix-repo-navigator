"""Thread-safe NetworkX view over the graph (strictly derived from SQLite).

Writes go through :meth:`NxGraph.rebuild` / :meth:`NxGraph.apply_delta` only.
Long traversals operate on a deepcopy (see :meth:`NxGraph.get_graph_readonly`)
and never hold the read lock, so writers are never starved.

Parallel edges between the same pair of nodes are supported: all ``Edge``
objects for a pair are stored in a dict keyed by edge id under the attribute
``"edges"``; the node payload lives under ``"data"``.
"""

from __future__ import annotations

import copy
import threading
from collections import deque
from contextlib import contextmanager
from typing import Iterator

import networkx as nx

from repo_navigator.models.edges import Edge
from repo_navigator.models.nodes import Node
from repo_navigator.models.queries import PathStep


class RWLock:
    """Writer-preferring shared-read / exclusive-write lock.

    Readers arriving while a writer is *waiting* also block, so a steady
    stream of readers can never starve the writer.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._readers = 0
        self._writer = False
        self._writers_waiting = 0

    @contextmanager
    def read(self) -> Iterator[None]:
        with self._cond:
            while self._writer or self._writers_waiting:
                self._cond.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._cond:
                self._readers -= 1
                if self._readers == 0:
                    self._cond.notify_all()

    @contextmanager
    def write(self) -> Iterator[None]:
        with self._cond:
            self._writers_waiting += 1
            try:
                while self._writer or self._readers:
                    self._cond.wait()
                self._writer = True
            finally:
                self._writers_waiting -= 1
        try:
            yield
        finally:
            with self._cond:
                self._writer = False
                self._cond.notify_all()


class NxGraph:
    """In-memory DiGraph mirroring the SQLite store."""

    def __init__(self) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()
        self._edge_pos: dict[str, tuple[str, str]] = {}  # edge_id -> (source, target)
        self._lock = RWLock()

    # ------------------------------------------------------------ mutations

    def rebuild(self, nodes: list[Node], edges: list[Edge]) -> None:
        """Full replacement from SQLite contents (cold start)."""
        fresh: nx.DiGraph = nx.DiGraph()
        positions: dict[str, tuple[str, str]] = {}
        for node in nodes:
            fresh.add_node(node.id, data=node)
        for edge in edges:
            if fresh.has_node(edge.source) and fresh.has_node(edge.target):
                _attach_edge(fresh, edge)
                positions[edge.id] = (edge.source, edge.target)
        with self._lock.write():
            self._graph = fresh
            self._edge_pos = positions

    def apply_delta(
        self,
        added_nodes: list[Node] | None = None,
        removed_node_ids: list[str] | None = None,
        added_edges: list[Edge] | None = None,
        removed_edge_ids: list[str] | None = None,
    ) -> None:
        """Incremental update: add/remove individual nodes and edges."""
        with self._lock.write():
            for node_id in removed_node_ids or []:
                if self._graph.has_node(node_id):
                    self._graph.remove_node(node_id)  # incident edges die too
                    self._edge_pos = {
                        eid: pair
                        for eid, pair in self._edge_pos.items()
                        if node_id not in pair
                    }
            for node in added_nodes or []:
                self._graph.add_node(node.id, data=node)
            for edge_id in removed_edge_ids or []:
                self._detach_edge(edge_id)
            for edge in added_edges or []:
                if self._graph.has_node(edge.source) and self._graph.has_node(edge.target):
                    _attach_edge(self._graph, edge)
                    self._edge_pos[edge.id] = (edge.source, edge.target)

    # -------------------------------------------------------------- reading

    def get_graph_readonly(self) -> nx.DiGraph:
        """Deepcopy of the graph for long traversals (no lock held afterwards)."""
        with self._lock.read():
            return copy.deepcopy(self._graph)

    def has_node(self, node_id: str) -> bool:
        with self._lock.read():
            return self._graph.has_node(node_id)

    def number_of_nodes(self) -> int:
        with self._lock.read():
            return self._graph.number_of_nodes()

    def number_of_edges(self) -> int:
        with self._lock.read():
            return len(self._edge_pos)

    def __len__(self) -> int:
        return self.number_of_nodes()

    # -------------------------------------------------------------- traversal

    def bfs(self, source: str, depth: int, width: int) -> list[Node]:
        """Forward BFS up to ``depth`` levels, at most ``width`` neighbors per node."""
        return self._bfs_impl(source, depth=depth, width=width, reverse=False)

    def reverse_bfs(self, source: str, max_depth: int) -> list[Node]:
        """BFS over predecessors: everything that (transitively) depends on source."""
        return self._bfs_impl(source, depth=max_depth, width=None, reverse=True)

    def shortest_path(self, source: str, target: str) -> list[PathStep]:
        """Cheapest path by summed edge weight; empty list if unreachable."""
        graph = self.get_graph_readonly()
        if not (graph.has_node(source) and graph.has_node(target)):
            return []
        try:
            node_ids: list[str] = nx.shortest_path(
                graph, source, target, weight=_min_edge_weight
            )
        except nx.NetworkXNoPath:
            return []

        steps: list[PathStep] = []
        for i, node_id in enumerate(node_ids):
            edge_in = None
            if i > 0:
                prev = node_ids[i - 1]
                candidates = graph[prev][node_id].get("edges", {}).values()
                edge_in = min(candidates, key=lambda e: e.weight, default=None)
            steps.append(
                PathStep(node=graph.nodes[node_id]["data"], edge_in=edge_in, depth=i)
            )
        return steps

    # ---------------------------------------------------------------- internals

    def _detach_edge(self, edge_id: str) -> None:
        pair = self._edge_pos.pop(edge_id, None)
        if pair is None or not self._graph.has_edge(*pair):
            return
        bucket = self._graph[pair[0]][pair[1]].get("edges", {})
        bucket.pop(edge_id, None)
        if not bucket:
            self._graph.remove_edge(pair[0], pair[1])

    def _bfs_impl(
        self, source: str, *, depth: int, width: int | None, reverse: bool
    ) -> list[Node]:
        with self._lock.read():
            graph = self._graph
            if not graph.has_node(source):
                return []
            successors = graph.predecessors if reverse else graph.successors

            visited: dict[str, Node] = {}
            queue: deque[tuple[str, int]] = deque([(source, 0)])
            seen: set[str] = {source}

            while queue:
                current, level = queue.popleft()
                if level >= depth:
                    continue
                expanded = 0
                for neighbor in successors(current):
                    if width is not None and expanded >= width:
                        break
                    expanded += 1
                    if neighbor in seen:
                        continue
                    seen.add(neighbor)
                    data = graph.nodes[neighbor].get("data")
                    if data is not None:
                        visited[neighbor] = data
                    queue.append((neighbor, level + 1))

            return list(visited.values())


def _attach_edge(graph: nx.DiGraph, edge: Edge) -> None:
    if graph.has_edge(edge.source, edge.target):
        graph[edge.source][edge.target]["edges"][edge.id] = edge
    else:
        graph.add_edge(edge.source, edge.target, edges={edge.id: edge})


def _min_edge_weight(u: str, v: str, attrs: dict) -> float:
    edges: dict[str, Edge] = attrs.get("edges", {})
    return min((e.weight for e in edges.values()), default=1.0)
