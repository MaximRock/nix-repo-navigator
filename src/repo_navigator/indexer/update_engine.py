"""Update engine: per-file incremental indexing."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from repo_navigator.graph.builder import GraphBuilder
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.indexer.cascade import cascade_dirty
from repo_navigator.indexer.hash_engine import ast_hash, content_hash, merkle_hash
from repo_navigator.models.file_state import FileState
from repo_navigator.parsers.registry import get_parser_for_file, safe_parse

log = logging.getLogger(__name__)


def _lang_for_path(path: str | Path) -> str:
    parser = get_parser_for_file(str(path))
    return parser.language if parser is not None else "unknown"


def _compute_merkle(db: Database, nx_graph: NxGraph, path: str, file_ast_hash: str) -> str:
    """Compute merkle for *path* from its direct imports."""
    # Find direct dependencies via imports edges
    source_id = f"nix:{path}"
    dep_hashes: list[str] = []

    # Try NxGraph first
    try:
        g = nx_graph.get_graph_readonly()
        if g.has_node(source_id):
            for succ in g.successors(source_id):
                if not succ.startswith("nix:"):
                    continue
                if g.has_edge(source_id, succ):
                    data = g[source_id][succ]
                    edges = data.get("edges", {})
                    # Only consider imports edges
                    has_imports = any(e.type.value == "imports" for e in edges.values())
                    if not has_imports:
                        continue
                    dep_path = succ.removeprefix("nix:")
                    dep_state = db.get_file_state(dep_path)
                    if dep_state is not None and dep_state.merkle_hash is not None:
                        dep_hashes.append(dep_state.merkle_hash)
                    elif dep_state is not None and dep_state.ast_hash is not None:
                        dep_hashes.append(dep_state.ast_hash)
            return merkle_hash(file_ast_hash, dep_hashes)
    except Exception:
        pass

    # Fallback: DB scan
    for edge in db.get_all_edges():
        if edge.source == source_id and edge.type.value == "imports" and edge.target.startswith("nix:"):
            dep_path = edge.target.removeprefix("nix:")
            dep_state = db.get_file_state(dep_path)
            if dep_state is not None and dep_state.merkle_hash is not None:
                dep_hashes.append(dep_state.merkle_hash)
            elif dep_state is not None and dep_state.ast_hash is not None:
                dep_hashes.append(dep_state.ast_hash)

    return merkle_hash(file_ast_hash, dep_hashes)


class UpdateEngine:
    """Per-file incremental update orchestration."""

    def __init__(
        self,
        db: Database,
        nx_graph: NxGraph,
        builder: GraphBuilder | None = None,
        root: Path | None = None,
    ) -> None:
        self.db = db
        self.nx_graph = nx_graph
        self.builder = builder or GraphBuilder(db, nx_graph)
        self.root = Path(root) if root is not None else Path.cwd()

    # ------------------------------------------------------------------ core

    def process_file(self, path: str | Path) -> dict:
        """Process a single file on disk.

        Returns a dict with ``changed`` (bool), ``reason`` and ``affected``.
        """
        path_str = str(path)
        # Normalise path for storage: try to make it relative to root if inside
        # root, otherwise keep as given.
        store_path = path_str
        try:
            abs_path = Path(path_str)
            if not abs_path.is_absolute():
                # Resolve relative to root
                abs_path = (self.root / path_str).resolve()
            else:
                abs_path = abs_path.resolve()
            # Try to make store_path relative to root
            try:
                store_path = abs_path.relative_to(self.root.resolve()).as_posix()
            except ValueError:
                store_path = abs_path.as_posix()
            # For files inside root, use relative; for outside, use absolute as fallback
            # But if path_str was already relative and file is inside root, we already have relative
            if abs_path.is_relative_to(self.root.resolve()):
                # Keep relative
                pass
            # Read content from abs_path
            content = abs_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self.process_deleted_file(path_str)
        except Exception as exc:
            log.warning("process_file: cannot read %s: %s", path_str, exc)
            return {"changed": False, "reason": "read_error", "affected": []}

        new_content_hash = content_hash(content)
        old_state = self.db.get_file_state(store_path)

        if old_state is not None and old_state.content_hash == new_content_hash:
            return {"changed": False, "reason": "unchanged_content", "affected": []}

        # Parse
        parse_result = safe_parse(Path(store_path), content)
        new_ast_hash = ast_hash(parse_result)

        if old_state is not None and old_state.ast_hash == new_ast_hash:
            # Only formatting / comment change: update content_hash, no graph rebuild
            new_state = FileState(
                path=store_path,
                lang=_lang_for_path(store_path),
                content_hash=new_content_hash,
                ast_hash=new_ast_hash,
                merkle_hash=old_state.merkle_hash,  # keep old merkle (deps unchanged)
                dirty=False,
                last_parsed=datetime.now(UTC),
                detail_level=old_state.detail_level,
            )
            self.db.upsert_file_state(new_state)
            return {"changed": False, "reason": "formatting_only", "affected": []}

        # AST changed: rebuild graph for this file
        self.builder.build_file(store_path, parse_result)

        # Compute merkle for this file
        new_merkle = _compute_merkle(self.db, self.nx_graph, store_path, new_ast_hash)

        new_state = FileState(
            path=store_path,
            lang=_lang_for_path(store_path),
            content_hash=new_content_hash,
            ast_hash=new_ast_hash,
            merkle_hash=new_merkle,
            dirty=False,
            last_parsed=datetime.now(UTC),
            detail_level="full",
        )
        self.db.upsert_file_state(new_state)

        # Cascade to importers
        affected = cascade_dirty(self.db, self.nx_graph, store_path)

        return {"changed": True, "reason": "ast_changed", "affected": affected}

    def process_deleted_file(self, path: str | Path) -> dict:
        """Handle deletion of *path* from filesystem."""
        path_str = str(path)
        # Normalise store_path similar to process_file
        store_path = path_str
        try:
            # If path is absolute, try to relativise to root
            p = Path(path_str)
            if p.is_absolute():
                try:
                    store_path = p.relative_to(self.root.resolve()).as_posix()
                except ValueError:
                    store_path = p.as_posix()
            else:
                # For relative paths, keep as is (already store_path)
                pass
        except Exception:
            store_path = path_str

        # Remove graph nodes/edges for this file (if any)
        # Use builder's delete logic via direct DB/graph purge
        old_nodes = [n for n in self.db.get_all_nodes() if n.path == store_path]
        old_edges = self.db.get_edges_for_file(store_path)
        if old_nodes or old_edges:
            old_node_ids = {n.id for n in old_nodes}
            old_edge_ids = {e.id for e in old_edges}
            with self.db._lock, self.db.transaction():
                if old_edge_ids:
                    placeholders = ",".join("?" for _ in old_edge_ids)
                    self.db._conn.execute(
                        f"DELETE FROM edges WHERE id IN ({placeholders})",
                        tuple(old_edge_ids),
                    )
                self.db._conn.execute("PRAGMA foreign_keys=OFF")
                try:
                    self.db._conn.execute("DELETE FROM nodes WHERE path=?", (store_path,))
                finally:
                    self.db._conn.execute("PRAGMA foreign_keys=ON")
            self.nx_graph.apply_delta(
                removed_node_ids=list(old_node_ids),
                removed_edge_ids=list(old_edge_ids),
            )
            self.db.inc_generation_id()

        # Remove file_state
        with self.db._lock, self.db.transaction():
            self.db._conn.execute("DELETE FROM file_state WHERE path=?", (store_path,))

        # Cascade to importers (they now have a missing dependency)
        affected = cascade_dirty(self.db, self.nx_graph, store_path)

        return {"changed": True, "reason": "deleted", "affected": affected}

    # ------------------------------------------------------------------ async wrappers

    async def process_file_async(self, path: str | Path) -> dict:
        return self.process_file(path)

    async def process_deleted_file_async(self, path: str | Path) -> dict:
        return self.process_deleted_file(path)

    async def run(self, queue) -> None:  # queue: asyncio.Queue[list[str]]
        """Consume batches from *queue* forever (for watcher integration)."""
        import asyncio

        while True:
            batch = await queue.get()
            for p in batch:
                # Heuristic: if file exists, process_file else deleted
                if Path(p).exists():
                    self.process_file(p)
                else:
                    self.process_deleted_file(p)
            # Note: cascade is handled inside process_file
            queue.task_done()
