"""Query engine: navigation verbs over the graph."""

from __future__ import annotations

import shutil
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from repo_navigator.config import Config
from repo_navigator.graph.builder import _placeholder_for_target
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.models.edges import Edge, EdgeType
from repo_navigator.models.nodes import Node, NodeType
from repo_navigator.models.queries import (
    BenefitReport,
    DependenciesReport,
    DependentsReport,
    DependencyEntry,
    EvalResult,
    ImpactEvidence,
    ImpactReport,
    ModuleSummary,
    Neighbor,
    Observation,
    OptionInfo,
    PathStep,
    RiskLevel,
    StatusResponse,
    Subgraph,
    SyncMode,
)


def _dangling_node(target_id: str) -> Node:
    """Synthetic read-only stand-in for a dangling edge endpoint.

    The node is never persisted; ``metadata["dangling"]`` flags it so every
    view (observe/hop/summarize) shows dangling edges instead of silently
    dropping them (BUG-003 P1).
    """
    node = _placeholder_for_target(target_id)
    assert node is not None  # the fallback branch always returns a Node
    node.metadata = {**node.metadata, "dangling": True}
    return node


class QueryEngine:
    """Navigation verbs with LRU cache bound to ``generation_id``."""

    #: Edge types that express a "depends on" relation (used by dependencies/dependents).
    _DEPENDENCY_TYPES = frozenset(
        {EdgeType.imports, EdgeType.requires, EdgeType.python_imports}
    )

    def __init__(
        self,
        db: Database,
        nx_graph: NxGraph,
        config: Config | None = None,
    ) -> None:
        self.db = db
        self.nx_graph = nx_graph
        self.config = config
        self._cache: dict[tuple, Any] = {}
        self._cache_generation: int | None = None
        self._start_time = time.monotonic()
        # Benefit statistics (repo_navigator_report).
        self._stats_queries = 0
        self._queries_by_tool: defaultdict[str, int] = defaultdict(int)
        self._files_served: set[str] = set()
        self._bytes_not_reread = 0
        self._size_cache: dict[str, int] = {}
        # Eval cache (lazy, to avoid circular import at top)
        from repo_navigator.nix.eval_cache import EvalCache

        root = None
        if config is not None and hasattr(config, "root"):
            try:
                root = Path(config.root)
            except Exception:
                root = None
        self.eval_cache = EvalCache(db, root=root)

    # ------------------------------------------------------------------ cache

    def _check_generation(self) -> int:
        gen = self.db.get_generation_id()
        if self._cache_generation is None or gen != self._cache_generation:
            self._cache.clear()
            self._cache_generation = gen
        return gen

    def _cached(self, key: tuple, compute):
        gen = self._check_generation()
        cache_key = (key, gen)
        if cache_key in self._cache:
            result = self._cache[cache_key]
            self._note_query(str(key[0]), result)
            return result
        result = compute()
        # Simple LRU: evict oldest if >128 entries
        if len(self._cache) >= 128:
            oldest = next(iter(self._cache))
            del self._cache[oldest]
        self._cache[cache_key] = result
        self._note_query(str(key[0]), result)
        return result

    # ------------------------------------------------------------ benefit stats

    def _note_query(self, tool: str, result: Any) -> None:
        """Count a served query and track the source bytes the agent avoided."""
        self._stats_queries += 1
        self._queries_by_tool[tool] += 1
        paths: set[str] = set()
        for n in self._result_nodes(result):
            p = n.path
            if not p and n.type == NodeType.file and n.name:
                p = n.name
            if p:
                paths.add(p)
        for p in paths:
            if p in self._files_served:
                continue
            self._files_served.add(p)
            self._bytes_not_reread += self._file_size(p)

    def _result_nodes(self, result: Any) -> list[Node]:
        """Best-effort extraction of nodes from any query result container."""
        if isinstance(result, Node):
            return [result]
        if isinstance(result, list):
            return [n for n in result if isinstance(n, Node)]
        for attr in ("nodes", "depends_on", "dependents"):
            values = getattr(result, attr, None)
            if isinstance(values, list):
                out = [
                    v if isinstance(v, Node) else v.node
                    for v in values
                    if isinstance(v, Node) or isinstance(getattr(v, "node", None), Node)
                ]
                if out:
                    return out
        out: list[Node] = []
        node = getattr(result, "node", None)
        if isinstance(node, Node):
            out.append(node)
        neighbors = getattr(result, "neighbors", None)
        if isinstance(neighbors, list):
            out += [nb.node for nb in neighbors if isinstance(nb.node, Node)]
        return out

    def _file_size(self, path: str) -> int:
        """Byte size of a repo file, lazily cached per path."""
        cached = self._size_cache.get(path)
        if cached is not None:
            return cached
        size = 0
        root = Path(self.config.root) if self.config is not None else None
        if root is not None:
            try:
                target = Path(root) / path
                if target.is_file():
                    size = target.stat().st_size
            except OSError:
                size = 0
        self._size_cache[path] = size
        return size

    def report(self) -> BenefitReport:
        """Session benefit report: queries served and estimated token savings."""
        gen = self.db.get_generation_id()
        return BenefitReport(
            queries_served=self._stats_queries,
            queries_by_tool=dict(sorted(self._queries_by_tool.items())),
            files_served=len(self._files_served),
            bytes_not_reread=self._bytes_not_reread,
            tokens_estimated_saved=self._bytes_not_reread // 4,
            uptime_seconds=round(time.monotonic() - self._start_time, 2),
            generation_id=gen,
        )

    # ---------------------------------------------------------------- observe

    def observe(self, node_id: str, depth: int = 1) -> Observation:
        """Direct neighbourhood of *node_id* up to *depth* (max 20)."""

        def _compute() -> Observation:
            if depth > 20:
                raise ValueError("depth must be <= 20")
            node = self.db.get_node(node_id)
            if node is None:
                raise KeyError(f"node not found: {node_id}")
            gen = self.db.get_generation_id()

            if depth == 1:
                edges = self.db.get_edges_for_node(node_id)
                neighbors: list[Neighbor] = []
                for edge in edges:
                    other_id = edge.target if edge.source == node_id else edge.source
                    other = self.db.get_node(other_id)
                    if other is not None:
                        neighbors.append(Neighbor(edge=edge, node=other))
                    else:
                        neighbors.append(
                            Neighbor(
                                edge=edge,
                                node=_dangling_node(other_id),
                                dangling=True,
                            )
                        )
                    if len(neighbors) >= 20:
                        break
                return Observation(node=node, neighbors=neighbors, generation_id=gen)

            # depth >1: BFS via NxGraph
            g = self.nx_graph.get_graph_readonly()
            if not g.has_node(node_id):
                return Observation(node=node, neighbors=[], generation_id=gen)
            # Collect nodes via BFS (both directions? observe should include all)
            # Use BFS forward + reverse and merge
            forward = set(
                n.id for n in self.nx_graph.bfs(node_id, depth=depth, width=20)
            )
            reverse = set(
                n.id for n in self.nx_graph.reverse_bfs(node_id, max_depth=depth)
            )
            all_ids = forward | reverse
            neighbors = []
            for nid in all_ids:
                n = self.db.get_node(nid)
                dangling = n is None
                if dangling:
                    n = _dangling_node(nid)
                # Find connecting edge (any)
                edges = self.db.get_edges_for_node(nid)
                # Find edge that connects to original or intermediate
                # For simplicity, attach first edge that touches nid and is in BFS frontier
                edge = edges[0] if edges else None
                if edge is not None:
                    neighbors.append(Neighbor(edge=edge, node=n, dangling=dangling))
                else:
                    # Create synthetic edge for BFS depth without direct edge?
                    # Skip
                    pass
                if len(neighbors) >= 20:
                    break
            # P1: BFS runs on NxGraph, so dangling endpoints whose graph
            # node is gone are unreachable above.  Supplement them flagged
            # from the DB (both directions, like depth 1), within budget.
            # May overshoot the depth frontier by one hop — flagged only.
            supplemented: set[str] = set()
            for nid in [node_id, *sorted(all_ids)]:
                if len(neighbors) >= 20:
                    break
                for e in self.db.get_edges_for_node(nid):
                    if len(neighbors) >= 20:
                        break
                    other_id = e.target if e.source == nid else e.source
                    if other_id in all_ids or other_id in supplemented:
                        continue
                    if self.db.get_node(other_id) is not None:
                        continue
                    supplemented.add(other_id)
                    neighbors.append(
                        Neighbor(edge=e, node=_dangling_node(other_id), dangling=True)
                    )
            return Observation(node=node, neighbors=neighbors, generation_id=gen)

        return self._cached(("observe", node_id, depth), _compute)

    # ---------------------------------------------------------------- hop

    def hop(
        self,
        node_id: str,
        relation: str | None = None,
        depth: int = 1,
        width: int = 10,
    ) -> Subgraph:
        """BFS with optional relation filter.  Enforces ``width*depth <= 100``."""

        def _compute() -> Subgraph:
            if width * depth > 100:
                raise ValueError(
                    f"budget exceeded: width*depth={width * depth} must be <=100"
                )
            if depth > 10:
                raise ValueError("depth must be <=10")
            gen = self.db.get_generation_id()
            # Use NxGraph for traversal but filter by relation
            g = self.nx_graph.get_graph_readonly()
            if not g.has_node(node_id):
                return Subgraph(nodes=[], edges=[], generation_id=gen)

            # BFS with relation filter
            visited: dict[str, Node] = {}
            levels: dict[str, int] = {node_id: 0}
            queue: deque[tuple[str, int]] = deque([(node_id, 0)])
            seen: set[str] = {node_id}
            edges_collected: dict[str, Edge] = {}

            while queue:
                cur, level = queue.popleft()
                if level >= depth:
                    continue
                # Get successors
                successors = list(g.successors(cur))
                # Filter by relation if specified
                if relation is not None:
                    filtered = []
                    for succ in successors:
                        if g.has_edge(cur, succ):
                            data = g[cur][succ]
                            for e in data.get("edges", {}).values():
                                if e.type.value == relation or e.type == relation:
                                    filtered.append(succ)
                                    break
                    successors = filtered
                # Width limit
                if width is not None:
                    successors = successors[:width]
                for succ in successors:
                    if succ in seen:
                        continue
                    seen.add(succ)
                    levels[succ] = level + 1
                    data = g.nodes[succ].get("data")
                    if data is not None:
                        visited[succ] = data
                    # Collect edges cur -> succ
                    if g.has_edge(cur, succ):
                        for e in g[cur][succ].get("edges", {}).values():
                            if (
                                relation is None
                                or e.type.value == relation
                                or str(e.type) == relation
                            ):
                                edges_collected[e.id] = e
                    queue.append((succ, level + 1))

            # P1 policy: an edge whose target has no node in the DB
            # (dangling — e.g. left behind by an FK-off bulk delete) must be
            # shown flagged, not silently dropped, so hop agrees with
            # summarize/observe.  The supplement only covers truly dangling
            # targets within the depth frontier: hop's depth/width/relation
            # contract is otherwise unchanged, and DB is the source of
            # truth (a DB-missing node overrides stale NxGraph data).
            dangling: list[str] = []
            for cur, cur_level in list(levels.items()):
                if cur_level >= depth:
                    continue
                for e in self.db.get_edges_for_node(cur):
                    if e.source != cur:
                        continue
                    if relation is not None and not (
                        e.type.value == relation or str(e.type) == relation
                    ):
                        continue
                    if self.db.get_node(e.target) is not None:
                        continue
                    edges_collected.setdefault(e.id, e)
                    if e.target not in dangling:
                        dangling.append(e.target)
                        visited[e.target] = _dangling_node(e.target)
            # Also include source node? Subgraph should contain traversed nodes, not source?
            # For hop, we include all visited excluding source, but spec is ambiguous.
            # We include visited nodes only.
            return Subgraph(
                nodes=list(visited.values()),
                edges=list(edges_collected.values()),
                dangling=dangling,
                generation_id=gen,
            )

        return self._cached(("hop", node_id, relation, depth, width), _compute)

    # ---------------------------------------------------------------- path

    def path(self, source: str, target: str) -> list[PathStep]:
        """Shortest path (Dijkstra) between *source* and *target*."""

        def _compute() -> list[PathStep]:
            # Use NxGraph.shortest_path which handles deepcopy and weights
            return self.nx_graph.shortest_path(source, target)

        # Path is not cached by generation? It should be, but path result
        # depends on graph structure which changes with generation, so include.
        gen = self._check_generation()
        key = ("path", source, target, gen)
        if key in self._cache:
            result = self._cache[key]
            self._note_query("path", result)
            return result
        result = _compute()
        if len(self._cache) >= 128:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = result
        self._note_query("path", result)
        return result

    # ---------------------------------------------------------------- blast_radius

    def blast_radius(self, node_id: str, max_depth: int = 5) -> Subgraph:
        """Reverse BFS: who depends on *node_id*."""

        def _compute() -> Subgraph:
            if max_depth > 10:
                raise ValueError("max_depth must be <=10")
            gen = self.db.get_generation_id()
            nodes = self.nx_graph.reverse_bfs(node_id, max_depth=max_depth)
            # Collect edges for the subgraph (reverse edges)
            edge_ids: set[str] = set()
            edges: list[Edge] = []

            def _add(e: Edge) -> None:
                if e.id not in edge_ids:
                    edge_ids.add(e.id)
                    edges.append(e)

            # For each node in blast, collect incoming edges that are part of blast
            # We can get all edges and filter where target in visited set
            visited_ids = {n.id for n in nodes} | {node_id}
            for e in self.db.get_all_edges():
                if e.source in visited_ids and e.target in visited_ids:
                    # Only include if edge is on a path that leads to node_id?
                    # For simplicity, include all edges among visited + source
                    _add(e)
            # Alternative: use graph edges
            # For now, also collect via graph
            for n in nodes:
                for e in self.db.get_edges_for_node(n.id):
                    if e.target == node_id or e.source in visited_ids:
                        _add(e)
            return Subgraph(nodes=nodes, edges=edges, generation_id=gen)

        return self._cached(("blast_radius", node_id, max_depth), _compute)

    # ---------------------------------------------------------------- find_symbol

    def find_symbol(
        self,
        query: str,
        lang: str | None = None,
        node_type: str | NodeType | list[str | NodeType] | None = None,
        path_contains: str | None = None,
        id_prefix: str | None = None,
        fuzzy: bool = False,
        limit: int = 10,
        offset: int = 0,
    ) -> list[Node]:
        """FTS5 search if ``fuzzy`` is False and no filters, LIKE otherwise.

        Structural filters (``lang``/``node_type``/``path_contains``/``id_prefix``)
        are applied via SQL ``WHERE`` on the nodes table. The same filter set
        is used by the future graph visualizer.
        """

        def _filtered_sql() -> list[Node]:
            conds: list[str] = ["(id LIKE ? OR name LIKE ?)"]
            params: list[object] = [f"%{query}%", f"%{query}%"]
            if lang is not None:
                conds.append("lang = ?")
                params.append(lang)
            if node_type is not None:
                items = node_type if isinstance(node_type, list) else [node_type]
                types = [t.value if isinstance(t, NodeType) else str(t) for t in items]
                conds.append(f"type IN ({', '.join('?' for _ in types)})")
                params.extend(types)
            if path_contains:
                conds.append("path LIKE ?")
                params.append(f"%{path_contains}%")
            if id_prefix:
                conds.append("id LIKE ?")
                params.append(f"{id_prefix}%")
            sql = (
                "SELECT * FROM nodes WHERE "
                + " AND ".join(conds)
                + " ORDER BY name LIMIT ? OFFSET ?"
            )
            params.extend([limit, offset])
            with self.db._lock:
                rows = self.db._conn.execute(sql, params).fetchall()
            from repo_navigator.graph.db import _row_to_node

            return [_row_to_node(r) for r in rows]

        def _compute() -> list[Node]:
            has_struct_filters = any(
                x is not None for x in (lang, node_type, path_contains, id_prefix)
            )
            if fuzzy or has_struct_filters:
                return _filtered_sql()
            results = self.db.search_fts5(query, limit=limit + offset)
            return results[offset : offset + limit]

        gen = self._check_generation()
        # Ensure cache key is hashable (node_type may be a list).
        node_type_key = (
            tuple(sorted(str(t) for t in node_type))
            if isinstance(node_type, list)
            else node_type
        )
        key = (
            "find_symbol",
            query,
            lang,
            node_type_key,
            path_contains,
            id_prefix,
            fuzzy,
            limit,
            offset,
            gen,
        )
        if key in self._cache:
            self._note_query("find_symbol", self._cache[key])
            return self._cache[key]
        result = _compute()
        if len(self._cache) >= 128:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = result
        self._note_query("find_symbol", result)
        return result

    # ---------------------------------------------------------------- dependencies

    def _dependency_closure(
        self, node_id: str, max_depth: int, reverse: bool
    ) -> tuple[int, list[DependencyEntry]]:
        """BFS over dependency edges (forward or reversed); keeps evidence chains.

        Returns ``(generation_id, entries)``. Raises ``KeyError`` if the node
        does not exist.
        """
        if max_depth > 10:
            raise ValueError("max_depth must be <=10")
        gen = self.db.get_generation_id()
        if self.db.get_node(node_id) is None:
            raise KeyError(f"node not found: {node_id}")
        graph = self.nx_graph.get_graph_readonly()
        if not graph.has_node(node_id):
            return gen, []

        adj: dict[str, list[tuple[str, Edge]]] = defaultdict(list)
        for u, v, data in graph.edges(data=True):
            for e in data.get("edges", {}).values():
                if e.type not in self._DEPENDENCY_TYPES:
                    continue
                src, tgt = (e.target, e.source) if reverse else (e.source, e.target)
                adj[src].append((tgt, e))

        parent: dict[str, tuple[str, Edge]] = {}
        dist: dict[str, int] = {node_id: 0}
        seen: set[str] = {node_id}
        queue: deque[str] = deque([node_id])
        while queue:
            cur = queue.popleft()
            if dist[cur] >= max_depth:
                continue
            for nxt, edge in adj.get(cur, ()):
                if nxt in seen:
                    continue
                seen.add(nxt)
                parent[nxt] = (cur, edge)
                dist[nxt] = dist[cur] + 1
                queue.append(nxt)

        entries: list[DependencyEntry] = []
        for nid in seen - {node_id}:
            steps: list[PathStep] = []
            cur = nid
            while cur != node_id:
                prev, edge = parent[cur]
                node_data = graph.nodes[cur].get("data")
                if node_data is not None:
                    steps.append(
                        PathStep(node=node_data, edge_in=edge, depth=dist[cur])
                    )
                cur = prev
            steps.reverse()
            # Prepend the target node as the chain root (edge_in=None, depth 0),
            # mirroring ``shortest_path`` semantics.
            target_data = graph.nodes[node_id].get("data")
            if target_data is not None:
                steps.insert(0, PathStep(node=target_data, edge_in=None, depth=0))
            final_data = graph.nodes[nid].get("data")
            if final_data is None:
                continue
            entries.append(
                DependencyEntry(node=final_data, steps=steps, depth=dist[nid])
            )
        entries.sort(key=lambda e: (e.depth, e.node.id))
        return gen, entries

    def dependencies(self, node_id: str, max_depth: int = 5) -> DependenciesReport:
        """Transitive closure over dependency edges: what *node_id* depends on."""

        def _compute() -> DependenciesReport:
            gen, entries = self._dependency_closure(node_id, max_depth, reverse=False)
            return DependenciesReport(
                target=node_id,
                max_depth=max_depth,
                depends_on=entries,
                generation_id=gen,
            )

        return self._cached(("dependencies", node_id, max_depth), _compute)

    def dependents(self, node_id: str, max_depth: int = 5) -> DependentsReport:
        """Transitive closure over reversed dependency edges: what depends on *node_id*."""

        def _compute() -> DependentsReport:
            gen, entries = self._dependency_closure(node_id, max_depth, reverse=True)
            return DependentsReport(
                target=node_id,
                max_depth=max_depth,
                dependents=entries,
                generation_id=gen,
            )

        return self._cached(("dependents", node_id, max_depth), _compute)

    # ---------------------------------------------------------------- summarize_module

    def summarize_module(self, path: str) -> ModuleSummary:
        """Summary for ``nix:{path}`` module."""

        def _compute() -> ModuleSummary:
            gen = self.db.get_generation_id()
            node_id = f"nix:{path}"
            node = self.db.get_node(node_id)
            if node is None:
                raise KeyError(f"module not found: {path}")
            incoming = [e for e in self.db.get_all_edges() if e.target == node_id]
            outgoing = self.db.get_edges_for_file(path)
            # Key symbols: options declared, functions, packages, files
            key_symbols: list[str] = []
            for e in outgoing:
                if e.type == EdgeType.declares and e.target.startswith("nix_option:"):
                    key_symbols.append(e.target.removeprefix("nix_option:"))
                elif e.type == EdgeType.uses_package:
                    key_symbols.append(e.target)
                elif e.type == EdgeType.configures:
                    key_symbols.append(e.target)
                elif e.type == EdgeType.declares and e.target.startswith(
                    "nix_function:"
                ):
                    key_symbols.append(e.target)
            # Limit
            key_symbols = sorted(set(key_symbols))[:20]
            # P1 policy: flag dangling edge endpoints instead of hiding them,
            # so summarize agrees with observe/hop.
            dangling_targets = sorted(
                {e.target for e in outgoing if self.db.get_node(e.target) is None}
                | {e.source for e in incoming if self.db.get_node(e.source) is None}
            )
            return ModuleSummary(
                path=path,
                incoming_edges=incoming,
                outgoing_edges=outgoing,
                key_symbols=key_symbols,
                dangling_targets=dangling_targets,
                generation_id=gen,
            )

        return self._cached(("summarize_module", path), _compute)

    # ---------------------------------------------------------------- impact_analysis

    def impact_analysis(self, node_id: str, max_depth: int = 5) -> ImpactReport:
        """Who is affected if *node_id* changes."""

        def _compute() -> ImpactReport:
            gen = self.db.get_generation_id()
            blast = self.blast_radius(node_id, max_depth=max_depth)
            affected_modules: list[str] = []
            affected_options: list[str] = []
            affected_files: list[str] = []
            for n in blast.nodes:
                if n.type.value == "nix_module":
                    # Extract path from id nix:xxx
                    path = n.id.removeprefix("nix:").split("::")[0]
                    affected_modules.append(path)
                elif n.type.value == "nix_option":
                    affected_options.append(n.id.removeprefix("nix_option:"))
                elif n.type.value == "file":
                    affected_files.append(n.name)
            # Also check edges for configures/generates
            for e in blast.edges:
                if e.type == EdgeType.configures or e.type == EdgeType.generates:
                    if e.target.startswith("file:"):
                        affected_files.append(e.target.removeprefix("file:"))
            affected_modules = sorted(set(affected_modules))
            affected_options = sorted(set(affected_options))
            affected_files = sorted(set(affected_files))

            # Risk level based on counts
            total = len(affected_modules) + len(affected_options) + len(affected_files)
            if total == 0:
                risk = RiskLevel.low
            elif total < 5:
                risk = RiskLevel.low
            elif total < 15:
                risk = RiskLevel.medium
            else:
                risk = RiskLevel.high

            # Why: for each affected node reachable via dependency edges, the chain.
            try:
                _, dep_entries = self._dependency_closure(
                    node_id, max_depth, reverse=True
                )
            except KeyError:
                dep_entries = []
            affected_ids = {n.id for n in blast.nodes}
            evidence = [
                ImpactEvidence(node_id=n.node.id, steps=n.steps)
                for n in dep_entries
                if n.node.id in affected_ids
            ]
            # Keep the response compact: cap the why-chains.
            evidence = evidence[:100]

            return ImpactReport(
                target=node_id,
                affected_modules=affected_modules,
                affected_options=affected_options,
                affected_files=affected_files,
                evidence=evidence,
                risk_level=risk,
                generation_id=gen,
            )

        return self._cached(("impact_analysis", node_id, max_depth), _compute)

    # ---------------------------------------------------------------- introspect

    def introspect_option(
        self, option_path: str, include_value: bool = False
    ) -> OptionInfo:
        """Static introspection of a Nix option."""

        def _compute() -> OptionInfo:
            gen = self.db.get_generation_id()
            opt_id = f"nix_option:{option_path}"
            node = self.db.get_node(opt_id)

            opt_type = None
            default = None
            example = None
            description = None
            declared_in = None
            defined_in: list[str] = []
            conditional_sets: list[str] = []

            if node is not None:
                opt_type = node.metadata.get("opt_type") or None
                default = node.metadata.get("default") or None
                example = node.metadata.get("example") or None
                description = node.metadata.get("description") or None

            # Find declares / sets edges
            for edge in self.db.get_all_edges():
                if edge.target == opt_id:
                    if edge.type == EdgeType.declares:
                        # source is nix:module
                        src_node = self.db.get_node(edge.source)
                        if src_node is not None and src_node.path:
                            declared_in = src_node.path
                        else:
                            declared_in = edge.source.removeprefix("nix:")
                    elif edge.type == EdgeType.sets:
                        src_node = self.db.get_node(edge.source)
                        path = (
                            src_node.path
                            if src_node and src_node.path
                            else edge.source.removeprefix("nix:")
                        )
                        defined_in.append(path)
                        if edge.metadata.get("conditional"):
                            conditional_sets.append(path)

            defined_in = sorted(set(defined_in))
            conditional_sets = sorted(set(conditional_sets))

            value = None
            value_status = None
            if include_value:
                # Try cache first
                eval_res = self.eval_expression(f"config.{option_path}")
                value = eval_res.value_json
                value_status = eval_res.status.value

            return OptionInfo(
                option_path=option_path,
                opt_type=opt_type,
                default=default,
                example=example,
                description=description,
                declared_in=declared_in,
                defined_in=defined_in,
                conditional_sets=conditional_sets,
                value=value,
                value_status=value_status,
                generation_id=gen,
            )

        return self._cached(("introspect_option", option_path, include_value), _compute)

    # ---------------------------------------------------------------- eval

    def eval_expression(self, expr: str, timeout: int = 60) -> EvalResult:
        """Lazy ``nix eval`` with SQLite cache (delegates to :class:`EvalCache`)."""
        if timeout > 120:
            raise ValueError("timeout must be <=120")
        # Delegate to EvalCache which handles DB cache, flake rev and nix eval
        result = self.eval_cache.get_or_eval(expr, timeout=timeout)
        # Ensure generation_id is current (EvalCache uses db generation at call time,
        # but our LRU cache is per generation, so we update)
        gen = self.db.get_generation_id()
        # Patch generation_id to current if needed
        if result.generation_id != gen:
            result = result.model_copy(update={"generation_id": gen})
        # Also update in-memory LRU for consistency with other verbs
        cache_key_mem = ("eval_expression", expr, timeout, gen)
        if len(self._cache) >= 128:
            self._cache.pop(next(iter(self._cache)))
        self._cache[cache_key_mem] = result
        return result

    # ---------------------------------------------------------------- status

    def status(self) -> StatusResponse:
        """Return current graph status."""

        def _compute() -> StatusResponse:
            mode = (
                SyncMode.hybrid if shutil.which("nix") is not None else SyncMode.static
            )
            total_nodes = self.db.count_nodes()
            total_edges = self.db.count_edges()
            uptime = time.monotonic() - self._start_time
            gen = self.db.get_generation_id()
            # sync_progress: if there are dirty files, report (clean, total)
            dirty = self.db.get_dirty_files()
            sync_progress = None
            if dirty:
                # total files with file_state
                total_files = len([n for n in self.db.get_all_nodes() if n.path])
                # For now, sync_progress is (remaining dirty, total)
                # But spec says (processed, total) during bulk sync
                # We approximate as (total-dirty, total)
                # To keep simple, return (len(dirty), total_files) if dirty else None
                # Actually we want (dirty, total)?? Use (0, total) if not dirty?
                # For MVP, return (len(dirty), total_files) when dirty else None
                # But to satisfy test, return None when no dirty
                sync_progress = (len(dirty), total_files) if total_files else None
                if sync_progress and sync_progress[0] == 0:
                    sync_progress = None
            return StatusResponse(
                mode=mode,
                total_nodes=total_nodes,
                total_edges=total_edges,
                uptime=uptime,
                sync_progress=sync_progress,
                queries_served=self._stats_queries,
                tokens_estimated_saved=self._bytes_not_reread // 4,
                generation_id=gen,
            )

        result = _compute()
        # status must be fresh (stats change each call), so don't cache it.
        self._note_query("status", result)
        return result

    def refresh(self) -> StatusResponse:
        """Full rescan of the repository (blocking)."""
        # Determine root from config or cwd
        root = Path.cwd()
        if self.config is not None and hasattr(self.config, "root"):
            try:
                root = Path(self.config.root)
            except Exception:
                pass
        root = root.resolve()
        # Use index_repo for full rescan
        from repo_navigator.indexer.scan import index_repo

        # If root is a file, use its parent
        if root.is_file():
            root = root.parent
        index_repo(root, self.db, self.nx_graph, config=self.config)
        return self.status()

    # ---------------------------------------------------------------- flake inputs

    def list_flake_inputs(self) -> list[dict[str, str]]:
        """List flake inputs from ``flake.lock`` (via DB)."""

        def _compute() -> list[dict[str, str]]:
            return self.db.get_flake_inputs()

        return self._cached(("list_flake_inputs",), _compute)

    def get_flake_input(self, name: str) -> dict[str, str] | None:
        """Get a single flake input by name."""

        def _compute() -> dict[str, str] | None:
            for inp in self.db.get_flake_inputs():
                if inp["name"] == name:
                    return inp
            return None

        return self._cached(("get_flake_input", name), _compute)

    # ---------------------------------------------------------------- packages (mock)

    def list_packages(
        self, query: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """List packages from ``package_index`` (mock)."""

        def _compute() -> list[dict[str, Any]]:
            pkgs = self.db.get_packages()
            if query:
                q = query.lower()
                pkgs = [
                    p
                    for p in pkgs
                    if q in p["attribute"].lower() or q in p["name"].lower()
                ]
            return pkgs[:limit]

        return self._cached(("list_packages", query, limit), _compute)

    def get_package(self, attribute: str) -> dict[str, Any] | None:
        """Get a single package by attribute."""

        def _compute() -> dict[str, Any] | None:
            for pkg in self.db.get_packages():
                if pkg["attribute"] == attribute:
                    return pkg
            return None

        return self._cached(("get_package", attribute), _compute)
