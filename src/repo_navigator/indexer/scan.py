"""File discovery and bulk indexing.

Walks a repository root, finds files handled by registered parsers,
parses them via the registry (with exception isolation) and feeds the
results to :class:`GraphBuilder`.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from repo_navigator.config import Config
from repo_navigator.graph.builder import GraphBuilder
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.parsers.registry import get_parser_for_file, safe_parse, should_parse_file

log = logging.getLogger(__name__)

# Directories that are never indexed (even if they contain .nix files).
SKIP_DIRS = {".git", ".venv", ".direnv", "__pycache__", "result", ".mypy_cache", ".pytest_cache", "target", "node_modules"}


def collect_files(
    root: Path,
    config: Config | None = None,
    graph: NxGraph | None = None,
) -> list[Path]:
    """Recursively find files under *root* that should be parsed.

    - If *root* is a file, returns ``[root]`` when a parser exists and
      ``should_parse_file`` allows it.
    - If *root* is a directory, walks it breadth-first, skipping
      directories in ``SKIP_DIRS`` and hidden ``.*`` entries (except
      ``.config`` which is required for Nix-first tier 1–3).
    """
    root = Path(root)

    if root.is_file():
        parser = get_parser_for_file(root)
        if parser is None:
            return []
        if not should_parse_file(root, graph=graph, config=config):
            return []
        return [root]

    if not root.is_dir():
        return []

    found: list[Path] = []
    stack: list[Path] = [root]

    while stack:
        cur = stack.pop()
        try:
            entries = list(cur.iterdir())
        except PermissionError:
            continue
        for entry in entries:
            name = entry.name
            # Skip hidden files/dirs except .config
            if name.startswith(".") and name != ".config":
                # But allow .nix hidden? No, skip hidden entirely except .config dir
                if entry.is_dir() and name == ".config":
                    pass  # allow
                else:
                    continue
            if entry.is_dir():
                if name in SKIP_DIRS:
                    continue
                stack.append(entry)
            elif entry.is_file():
                parser = get_parser_for_file(entry)
                if parser is None:
                    continue
                if not should_parse_file(entry, graph=graph, config=config):
                    continue
                found.append(entry)

    return sorted(found)


def index_repo(
    root: Path,
    db: Database,
    nx_graph: NxGraph,
    config: Config | None = None,
) -> dict[str, int | float]:
    """Index *root* into *db* / *nx_graph*.

    Returns a stats dict: ``{"files": ..., "nodes": ..., "edges": ..., "elapsed_ms": ...}``.
    The graph is fully replaced for every discovered file (via
    ``GraphBuilder.build_all``) and stale file nodes (deleted from
    filesystem) are purged.
    """
    root = Path(root).resolve()
    start = time.monotonic()

    # Ensure DB is ready.
    db.init_db()

    # 1. Discover files
    files = collect_files(root, config=config, graph=nx_graph)

    # 2. Parse each file -> ParseResult, using relative path for stable IDs.
    items: list[tuple[str, object]] = []  # (rel_path, ParseResult)
    is_single_file = root.is_file()
    for abs_path in files:
        if is_single_file:
            # Root itself is the file; use its name (or relative to parent if we want dir prefix)
            # For consistency with CLI single-file mode, use name relative to parent.
            try:
                rel = abs_path.relative_to(root.parent).as_posix()
            except ValueError:
                rel = abs_path.name
        else:
            try:
                rel = abs_path.relative_to(root).as_posix()
            except ValueError:
                rel = abs_path.as_posix()
        try:
            content = abs_path.read_text(encoding="utf-8")
        except Exception as exc:
            log.warning("Skipping unreadable file %s: %s", abs_path, exc)
            continue
        # Use relative path for parser so IDs are repo-relative.
        parse_result = safe_parse(Path(rel), content)
        items.append((rel, parse_result))  # type: ignore[arg-type]

    # 3. Purge stale files (previously indexed but no longer on disk).
    # This must happen before build_all so generation is not double-counted
    # for the bulk operation.  We do it as a single batch delete.
    old_paths = {n.path for n in db.get_all_nodes() if n.path is not None}
    new_paths = {rel for rel, _ in items}
    stale_paths = old_paths - new_paths
    if stale_paths:
        _purge_paths(db, nx_graph, stale_paths)

    # 4. Bulk build
    builder = GraphBuilder(db, nx_graph)
    # builder expects list[tuple[Path|str, ParseResult]]
    builder.build_all([(Path(p), pr) for p, pr in items])  # type: ignore[arg-type]

    elapsed_ms = (time.monotonic() - start) * 1000
    return {
        "files": len(items),
        "nodes": db.count_nodes(),
        "edges": db.count_edges(),
        "generation": db.get_generation_id(),
        "elapsed_ms": elapsed_ms,
    }


def _purge_paths(db: Database, nx_graph: NxGraph, stale_paths: set[str | None]) -> None:
    """Remove graph sub-graphs for deleted files."""
    stale = {p for p in stale_paths if p is not None}
    if not stale:
        return

    # Collect outgoing edges for stale files
    stale_edge_ids: set[str] = set()
    stale_node_ids: set[str] = set()
    for p in stale:
        for edge in db.get_edges_for_file(p):  # type: ignore[arg-type]
            stale_edge_ids.add(edge.id)
        for node in db.get_all_nodes():
            if node.path == p:
                stale_node_ids.add(node.id)

    # DB: delete edges then nodes (FK OFF to keep incoming edges from other files)
    with db._lock, db.transaction():
        if stale_edge_ids:
            placeholders = ",".join("?" for _ in stale_edge_ids)
            db._conn.execute(
                f"DELETE FROM edges WHERE id IN ({placeholders})",
                tuple(stale_edge_ids),
            )
        if stale:
            placeholders = ",".join("?" for _ in stale)
            db._conn.execute("PRAGMA foreign_keys=OFF")
            try:
                db._conn.execute(
                    f"DELETE FROM nodes WHERE path IN ({placeholders})",
                    tuple(stale),
                )
            finally:
                db._conn.execute("PRAGMA foreign_keys=ON")

    # NxGraph delta
    nx_graph.apply_delta(
        removed_node_ids=list(stale_node_ids),
        removed_edge_ids=list(stale_edge_ids),
    )
    # Note: generation is incremented by build_all; for purge-only case
    # (e.g. files deleted but no new files) we increment here.
    # However index_repo calls purge before build_all which will inc.
    # If build_all is empty, we still need to inc for purge.
    # The caller (index_repo) will inc via build_all even when items is empty,
    # so we don't double inc here.  This helper is internal to index_repo
    # which always follows with build_all, so no inc here.
