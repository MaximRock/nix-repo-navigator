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
from repo_navigator.parsers.registry import (
    get_parser_for_file,
    safe_parse,
    should_parse_file,
)

log = logging.getLogger(__name__)

# Directories that are never indexed (even if they contain .nix files).
SKIP_DIRS = {
    ".git",
    ".venv",
    ".repo-navigator",
    ".direnv",
    "__pycache__",
    "result",
    ".mypy_cache",
    ".pytest_cache",
    "target",
    "node_modules",
}


def collect_files(
    root: Path,
    config: Config | None = None,
    graph: NxGraph | None = None,
    tiers: set[int] | None = None,
) -> list[Path]:
    """Recursively find files under *root* that should be parsed.

    - If *root* is a file, returns ``[root]`` when a parser exists and
      the plugin is enabled. Single-file mode is an explicit request, so
      the Nix-first reference rule is bypassed.
    - If *root* is a directory, walks it breadth-first, skipping
      directories in ``SKIP_DIRS`` and hidden ``.*`` entries (except
      ``.config`` which is required for Nix-first tier 1–3).
    - *tiers* restricts collection to parsers whose ``tier`` is in the set
      (used for the two-pass bulk index: pass 1 = ``{0}`` Nix, pass 2 = ``{1,2,3}``).
    """
    root = Path(root)

    if root.is_file():
        parser = get_parser_for_file(root)
        if parser is None:
            return []
        if tiers is not None and getattr(parser, "tier", 0) not in tiers:
            return []
        if not getattr(parser, "enabled", True):
            return []
        if getattr(parser, "tier", 0) > 0:
            # Explicit single-file request: plugin must be enabled, but no
            # Nix reference / .config/ requirement.
            if (
                config is not None
                and hasattr(config, "plugins")
                and parser.language not in config.plugins
            ):
                return []
            return [root]
        if not should_parse_file(root, graph=graph, config=config):
            return []
        return [root]
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
                if tiers is not None and getattr(parser, "tier", 0) not in tiers:
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

    Two-pass bulk index: Nix (tier 0) is parsed first so that
    ``configures``/``generates`` edges exist — then tier 1-3 files are
    collected using the populated graph. This makes a ``home.file``
    reference discoverable on the very first run (no re-index needed).
    ``generation_id`` is incremented exactly once, after both passes.
    """
    root = Path(root).resolve()
    start = time.monotonic()

    # Ensure DB is ready.
    db.init_db()

    is_single_file = root.is_file()
    builder = GraphBuilder(db, nx_graph)

    items: list[tuple[str, object]] = []  # (rel_path, ParseResult)

    if is_single_file:
        # Single-file explicit request: no two-pass needed.
        files = collect_files(root, config=config, graph=nx_graph)
        items = _parse_files(root, files, is_single_file=True)
    else:
        # Pass 1: Nix (tier 0) — build the graph first.
        nix_files = collect_files(root, config=config, graph=nx_graph, tiers={0})
        nix_items = _parse_files(root, nix_files, is_single_file=False)
        items.extend(nix_items)
        builder.build_all(
            [(Path(p), pr) for p, pr in nix_items],  # type: ignore[arg-type]
            increment_generation=False,
        )

        # Pass 2: tier 1-3 — the graph now knows configures/generates edges.
        plugin_files = collect_files(
            root, config=config, graph=nx_graph, tiers={1, 2, 3}
        )
        plugin_items = _parse_files(root, plugin_files, is_single_file=False)
        items.extend(plugin_items)

    # Purge stale files (previously indexed but no longer on disk).
    if not is_single_file:
        old_paths = {n.path for n in db.get_all_nodes() if n.path is not None}
        new_paths = {rel for rel, _ in items}
        stale_paths = old_paths - new_paths
        if stale_paths:
            _purge_paths(db, nx_graph, stale_paths)

    # Build the plugin pass and bump generation exactly once at the end.
    if is_single_file:
        builder.build_all([(Path(p), pr) for p, pr in items])  # type: ignore[arg-type]
    else:
        builder.build_all(
            [(Path(p), pr) for p, pr in plugin_items],  # type: ignore[arg-type]
            increment_generation=False,
        )
        db.inc_generation_id()

    # Flake inputs (if flake.lock exists)
    _index_flake_inputs(root, db, nx_graph)

    # Package index (mock, from package_ref nodes)
    try:
        from repo_navigator.nix.package_index import PackageIndexBuilder

        pkg_builder = PackageIndexBuilder(db, root=root)
        pkg_builder.refresh()
    except Exception:
        log.debug("package index refresh failed", exc_info=True)

    elapsed_ms = (time.monotonic() - start) * 1000
    return {
        "files": len(items),
        "nodes": db.count_nodes(),
        "edges": db.count_edges(),
        "generation": db.get_generation_id(),
        "elapsed_ms": elapsed_ms,
    }


def _parse_files(
    root: Path, files: list[Path], is_single_file: bool
) -> list[tuple[str, object]]:
    """Parse *files* into ``(rel_path, ParseResult)`` pairs for *root*."""
    items: list[tuple[str, object]] = []
    for abs_path in files:
        if is_single_file:
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
        parse_result = safe_parse(Path(rel), content)
        items.append((rel, parse_result))
    return items


def _index_flake_inputs(root: Path, db: Database, nx_graph: NxGraph) -> None:
    """Parse ``flake.lock`` and upsert flake inputs + graph nodes."""
    lock_path = root / "flake.lock" if root.is_dir() else root.parent / "flake.lock"
    if not lock_path.is_file():
        # Also try root itself if it's a file's parent already checked
        alt = (
            Path(root).resolve().parent / "flake.lock" if Path(root).is_file() else None
        )
        if alt is None or not alt.is_file():
            return
        lock_path = alt
    try:
        from repo_navigator.parsers.nix.flake_parser import parse_flake_lock

        inputs = parse_flake_lock(lock_path)
    except Exception:
        log.debug("Failed to parse flake.lock at %s", lock_path, exc_info=True)
        return

    # Upsert DB and create graph nodes
    from repo_navigator.models.nodes import Node, NodeType

    for inp in inputs:
        try:
            db.upsert_flake_input(inp.name, inp.url or "", inp.rev or "")
        except Exception:
            log.debug("upsert_flake_input failed for %s", inp.name, exc_info=True)
        # Create graph node for flake_input
        node_id = f"flake_input:{inp.name}"
        # Avoid duplicate if already exists
        if db.get_node(node_id) is None:
            node = Node(
                id=node_id,
                type=NodeType.flake_input,
                name=inp.name,
                path=None,
                lang="nix",
                metadata={
                    "url": inp.url or "",
                    "rev": inp.rev or "",
                    "type": inp.type or "",
                },
            )
            try:
                db.upsert_node(node)
                nx_graph.apply_delta(added_nodes=[node])
            except Exception:
                log.debug(
                    "Failed to upsert flake_input node %s", node_id, exc_info=True
                )
        else:
            # Update existing node's metadata
            existing = db.get_node(node_id)
            if existing is not None:
                existing.metadata = {
                    "url": inp.url or "",
                    "rev": inp.rev or "",
                    "type": inp.type or "",
                }
                try:
                    db.upsert_node(existing)
                    nx_graph.apply_delta(added_nodes=[existing])
                except Exception:
                    pass
    # Purge stale flake inputs (those in DB but not in current lock)
    try:
        existing_inputs = {
            row["name"]
            for row in db._conn.execute("SELECT name FROM flake_inputs").fetchall()
        }
        current_names = {inp.name for inp in inputs}
        stale = existing_inputs - current_names
        for name in stale:
            try:
                with db._lock, db.transaction():
                    db._conn.execute("DELETE FROM flake_inputs WHERE name=?", (name,))
                    db._conn.execute(
                        "DELETE FROM nodes WHERE id=?", (f"flake_input:{name}",)
                    )
                nx_graph.apply_delta(removed_node_ids=[f"flake_input:{name}"])
            except Exception:
                pass
    except Exception:
        pass


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
