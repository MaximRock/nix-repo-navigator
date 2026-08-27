"""Cascade engine: propagate dirty flags through the import graph."""

from __future__ import annotations

import logging
from collections import deque

from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.indexer.hash_engine import merkle_hash
from repo_navigator.models.edges import EdgeType

log = logging.getLogger(__name__)


def _module_id(path: str) -> str:
    return f"nix:{path}"


def _direct_importers(db: Database, nx_graph: NxGraph, path: str) -> list[str]:
    """Return file paths that directly import *path* (reverse imports edge)."""
    target_id = _module_id(path)
    importers: list[str] = []

    # Prefer NxGraph for speed (in-memory), fallback to DB scan
    try:
        g = nx_graph.get_graph_readonly()
        if not g.has_node(target_id):
            # Fallback to DB scan if node not in NxGraph (e.g. placeholder)
            raise KeyError
        for pred in g.predecessors(target_id):
            # Check if there's an imports edge pred -> target
            if g.has_edge(pred, target_id):
                data = g[pred][target_id]
                edges = data.get("edges", {})
                for edge in edges.values():
                    if edge.type == EdgeType.imports:
                        # pred is "nix:importer_path"
                        if pred.startswith("nix:"):
                            importer_path = pred.removeprefix("nix:")
                            # Filter out synthetic placeholders that have no file_state
                            # but keep them if they correspond to real files
                            importers.append(importer_path)
                        break
        return importers
    except Exception:
        # Fallback: scan DB edges
        for edge in db.get_all_edges():
            if edge.type == EdgeType.imports and edge.target == target_id:
                if edge.source.startswith("nix:"):
                    importers.append(edge.source.removeprefix("nix:"))
        return importers


def _direct_dependencies(db: Database, nx_graph: NxGraph, path: str) -> list[str]:
    """Return files that *path* directly imports (forward imports)."""
    source_id = _module_id(path)
    deps: list[str] = []
    try:
        g = nx_graph.get_graph_readonly()
        if not g.has_node(source_id):
            raise KeyError
        for succ in g.successors(source_id):
            if g.has_edge(source_id, succ):
                data = g[source_id][succ]
                edges = data.get("edges", {})
                for edge in edges.values():
                    if edge.type == EdgeType.imports and succ.startswith("nix:"):
                        deps.append(succ.removeprefix("nix:"))
                        break
        return deps
    except Exception:
        for edge in db.get_all_edges():
            if edge.type == EdgeType.imports and edge.source == source_id:
                if edge.target.startswith("nix:"):
                    deps.append(edge.target.removeprefix("nix:"))
        return deps


def cascade_dirty(
    db: Database,
    nx_graph: NxGraph,
    changed_path: str,
) -> list[str]:
    """Propagate dirty flag from *changed_path* to all transitive importers.

    1. Find all files that (transitively) import ``changed_path`` via
       ``imports`` edges (reverse BFS).
    2. Mark each importer ``dirty=1`` in ``file_state`` and recompute its
       ``merkle_hash`` (``ast_hash`` + sorted dependency merkle hashes).
    3. Invalidate cached option values for the affected set.

    Returns the list of affected importer paths (excluding ``changed_path``
    itself), in BFS order (closest first).
    """
    # BFS over reverse imports
    visited: set[str] = set()
    queue: deque[str] = deque([changed_path])
    visited.add(changed_path)
    affected: list[str] = []

    while queue:
        cur = queue.popleft()
        for importer in _direct_importers(db, nx_graph, cur):
            if importer in visited:
                continue
            visited.add(importer)
            queue.append(importer)
            affected.append(importer)

    if not affected:
        # No importers, but still invalidate option values for the changed file itself
        try:
            db.invalidate_option_values([changed_path])
        except Exception:
            log.exception("cascade: invalidate_option_values failed")
        return []

    # Recompute merkle for each affected file in BFS order (closest first,
    # so dependencies (closer to changed) are already updated when we
    # process their importers)
    for importer_path in affected:
        state = db.get_file_state(importer_path)
        if state is None or state.ast_hash is None:
            # No state yet (file not yet indexed) -> just mark dirty if exists
            # If no state, create a minimal dirty entry? For now skip.
            continue
        dep_paths = _direct_dependencies(db, nx_graph, importer_path)
        dep_hashes: list[str] = []
        for dep in dep_paths:
            dep_state = db.get_file_state(dep)
            if dep_state is not None and dep_state.merkle_hash is not None:
                dep_hashes.append(dep_state.merkle_hash)
            elif dep_state is not None and dep_state.ast_hash is not None:
                dep_hashes.append(dep_state.ast_hash)
            # else: dependency not indexed, skip

        new_merkle = merkle_hash(state.ast_hash, dep_hashes)
        # Mark dirty and update merkle
        # We need to upsert file_state with dirty=True
        from datetime import UTC, datetime

        from repo_navigator.models.file_state import FileState

        new_state = FileState(
            path=importer_path,
            lang=state.lang,
            content_hash=state.content_hash,
            ast_hash=state.ast_hash,
            merkle_hash=new_merkle,
            dirty=True,
            last_parsed=state.last_parsed,
            detail_level=state.detail_level,
        )
        db.upsert_file_state(new_state)
        log.debug("cascade: marked dirty %s (merkle %s)", importer_path, new_merkle[:8])

    # Invalidate option values that mention any affected file
    try:
        all_affected = [changed_path] + affected
        db.invalidate_option_values(all_affected)
    except Exception:
        log.exception("cascade: invalidate_option_values failed")

    return affected


async def cascade_dirty_async(
    db: Database,
    nx_graph: NxGraph,
    changed_path: str,
) -> list[str]:
    """Async wrapper for :func:`cascade_dirty`."""
    return cascade_dirty(db, nx_graph, changed_path)
