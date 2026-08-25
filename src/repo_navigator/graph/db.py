"""SQLite persistence layer — the single source of truth for the graph.

NetworkX (``nx_graph.py``) is strictly derived: all writes go through this
module. Schema versioning uses ``PRAGMA user_version``; migrations are plain
SQL scripts applied in order.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from repo_navigator.models.edges import Edge, EdgeType
from repo_navigator.models.file_state import FileState
from repo_navigator.models.nodes import Node, NodeType
from repo_navigator.models.option_value import OptionValue, ValueStatus

CURRENT_SCHEMA_VERSION = 1

# MIGRATIONS[n] upgrades the schema from version n-1 to version n.
MIGRATIONS: dict[int, str] = {
    # 2: "ALTER TABLE nodes ADD COLUMN foo TEXT;",
}

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS generation (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    value INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    path TEXT,
    lang TEXT NOT NULL,
    metadata JSON NOT NULL DEFAULT '{}',
    content_hash TEXT,
    ast_hash TEXT,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    metadata JSON NOT NULL DEFAULT '{}',
    weight REAL NOT NULL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
CREATE INDEX IF NOT EXISTS idx_edges_type   ON edges(type);

CREATE TABLE IF NOT EXISTS file_state (
    path TEXT PRIMARY KEY,
    lang TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    ast_hash TEXT,
    merkle_hash TEXT,
    dirty BOOLEAN NOT NULL DEFAULT 0,
    last_parsed TIMESTAMP,
    detail_level TEXT
);

CREATE TABLE IF NOT EXISTS flake_inputs (
    name TEXT PRIMARY KEY,
    url TEXT,
    rev TEXT,
    last_modified TIMESTAMP,
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS package_index (
    attribute TEXT PRIMARY KEY,
    name TEXT,
    version TEXT,
    store_path TEXT,
    meta JSON,
    used_by JSON,
    first_seen TIMESTAMP,
    last_updated TIMESTAMP
);

CREATE TABLE IF NOT EXISTS option_values (
    key TEXT PRIMARY KEY,
    expr TEXT NOT NULL,
    value_json JSON,
    status TEXT NOT NULL,
    error TEXT,
    computed_at TIMESTAMP,
    source_rev TEXT
);

-- External-content FTS5 index over nodes, kept in sync by triggers.
CREATE VIRTUAL TABLE IF NOT EXISTS node_search USING fts5(
    id, name, type, lang, content=nodes
);

CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
    INSERT INTO node_search(rowid, id, name, type, lang)
    VALUES (new.rowid, new.id, new.name, new.type, new.lang);
END;

CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
    INSERT INTO node_search(node_search, rowid, id, name, type, lang)
    VALUES ('delete', old.rowid, old.id, old.name, old.type, old.lang);
END;

CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN
    INSERT INTO node_search(node_search, rowid, id, name, type, lang)
    VALUES ('delete', old.rowid, old.id, old.name, old.type, old.lang);
    INSERT INTO node_search(rowid, id, name, type, lang)
    VALUES (new.rowid, new.id, new.name, new.type, new.lang);
END;
"""

_SCHEMA_BY_VERSION = {1: SCHEMA_V1}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _dt_to_str(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt_from_str(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


class Database:
    """Thin typed wrapper over the SQLite graph store."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")

    # ------------------------------------------------------------ lifecycle

    def init_db(self) -> None:
        """Create or migrate the schema; safe to call multiple times."""
        with self._lock, self.transaction():
            self._conn.execute("PRAGMA journal_mode=WAL")
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                self._conn.executescript(SCHEMA_V1)
                version = CURRENT_SCHEMA_VERSION
            else:
                while version < CURRENT_SCHEMA_VERSION:
                    version += 1
                    script = MIGRATIONS.get(version)
                    if script is None and version not in _SCHEMA_BY_VERSION:
                        raise RuntimeError(f"no migration to schema v{version}")
                    if script:
                        self._conn.executescript(script)
            if version > 0:
                self._conn.execute(f"PRAGMA user_version={version}")
            self._conn.execute(
                "INSERT OR IGNORE INTO generation (id, value) VALUES (1, 0)"
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ------------------------------------------------------------- generation

    def get_generation_id(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT value FROM generation WHERE id=1").fetchone()
            return int(row[0])

    def inc_generation_id(self) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE generation SET value=value+1 WHERE id=1 RETURNING value"
            )
            value = int(cur.fetchone()[0])
            self._conn.commit()
            return value

    # ----------------------------------------------------------------- nodes

    def upsert_node(self, node: Node) -> None:
        with self._lock, self.transaction():
            self._conn.execute(
                """
                INSERT INTO nodes (id, type, name, path, lang, metadata,
                                   content_hash, ast_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    type=excluded.type, name=excluded.name, path=excluded.path,
                    lang=excluded.lang, metadata=excluded.metadata,
                    content_hash=excluded.content_hash, ast_hash=excluded.ast_hash,
                    updated_at=excluded.updated_at
                """,
                (
                    node.id,
                    node.type.value,
                    node.name,
                    node.path,
                    node.lang,
                    json.dumps(node.metadata),
                    node.content_hash,
                    node.ast_hash,
                    _dt_to_str(node.created_at),
                    _now_iso(),
                ),
            )

    def get_node(self, node_id: str) -> Node | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM nodes WHERE id=?", (node_id,)
            ).fetchone()
            return _row_to_node(row) if row else None

    def delete_file_nodes(self, path: str) -> None:
        """Remove every node of a file; edges cascade via foreign keys."""
        with self._lock, self.transaction():
            self._conn.execute("DELETE FROM nodes WHERE path=?", (path,))

    def get_all_nodes(self) -> list[Node]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM nodes ORDER BY id").fetchall()
            return [_row_to_node(r) for r in rows]

    def count_nodes(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])

    # ----------------------------------------------------------------- edges

    def upsert_edge(self, edge: Edge) -> None:
        with self._lock, self.transaction():
            self._conn.execute(
                """
                INSERT INTO edges (id, source, target, type, metadata, weight)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source=excluded.source, target=excluded.target,
                    type=excluded.type, metadata=excluded.metadata,
                    weight=excluded.weight
                """,
                (
                    edge.id,
                    edge.source,
                    edge.target,
                    edge.type.value,
                    json.dumps(edge.metadata),
                    edge.weight,
                ),
            )

    def get_edge(self, edge_id: str) -> Edge | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM edges WHERE id=?", (edge_id,)
            ).fetchone()
            return _row_to_edge(row) if row else None

    def get_edges_for_node(self, node_id: str) -> list[Edge]:
        """All edges touching ``node_id`` (as source or as target)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM edges WHERE source=? OR target=? ORDER BY id",
                (node_id, node_id),
            ).fetchall()
            return [_row_to_edge(r) for r in rows]

    def get_edges_for_file(self, path: str) -> list[Edge]:
        """Edges declared by the module(s) of ``path`` (source-side ownership)."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT e.* FROM edges e JOIN nodes n ON n.id = e.source
                WHERE n.path=? ORDER BY e.id
                """,
                (path,),
            ).fetchall()
            return [_row_to_edge(r) for r in rows]

    def get_all_edges(self) -> list[Edge]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM edges ORDER BY id").fetchall()
            return [_row_to_edge(r) for r in rows]

    def count_edges(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0])

    # ------------------------------------------------------------ file_state

    def upsert_file_state(self, fs: FileState) -> None:
        with self._lock, self.transaction():
            self._conn.execute(
                """
                INSERT INTO file_state (path, lang, content_hash, ast_hash,
                                        merkle_hash, dirty, last_parsed, detail_level)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    lang=excluded.lang, content_hash=excluded.content_hash,
                    ast_hash=excluded.ast_hash, merkle_hash=excluded.merkle_hash,
                    dirty=excluded.dirty, last_parsed=excluded.last_parsed,
                    detail_level=excluded.detail_level
                """,
                (
                    fs.path,
                    fs.lang,
                    fs.content_hash,
                    fs.ast_hash,
                    fs.merkle_hash,
                    int(fs.dirty),
                    _dt_to_str(fs.last_parsed),
                    fs.detail_level,
                ),
            )

    def get_file_state(self, path: str) -> FileState | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM file_state WHERE path=?", (path,)
            ).fetchone()
            return _row_to_file_state(row) if row else None

    def get_dirty_files(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT path FROM file_state WHERE dirty=1 ORDER BY path"
            ).fetchall()
            return [r[0] for r in rows]

    def mark_dirty(self, path: str) -> None:
        with self._lock, self.transaction():
            self._conn.execute(
                "UPDATE file_state SET dirty=1 WHERE path=?", (path,)
            )

    def mark_clean(self, path: str) -> None:
        with self._lock, self.transaction():
            self._conn.execute(
                "UPDATE file_state SET dirty=0 WHERE path=?", (path,)
            )

    # ----------------------------------------------------------- flake_inputs

    def upsert_flake_input(self, name: str, url: str, rev: str) -> None:
        with self._lock, self.transaction():
            self._conn.execute(
                """
                INSERT INTO flake_inputs (name, url, rev, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    url=excluded.url, rev=excluded.rev, updated_at=excluded.updated_at
                """,
                (name, url, rev, _now_iso()),
            )

    def get_flake_inputs(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, url, rev FROM flake_inputs ORDER BY name"
            ).fetchall()
            return [{"name": r[0], "url": r[1], "rev": r[2]} for r in rows]

    # ---------------------------------------------------------- package_index

    def upsert_package(
        self,
        attribute: str,
        name: str,
        version: str,
        store_path: str | None,
        meta: dict[str, Any],
    ) -> None:
        with self._lock, self.transaction():
            existing = self._conn.execute(
                "SELECT used_by FROM package_index WHERE attribute=?", (attribute,)
            ).fetchone()
            used_by = json.loads(existing[0]) if existing and existing[0] else []
            now = _now_iso()
            self._conn.execute(
                """
                INSERT INTO package_index (attribute, name, version, store_path,
                                           meta, used_by, first_seen, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(attribute) DO UPDATE SET
                    name=excluded.name, version=excluded.version,
                    store_path=excluded.store_path, meta=excluded.meta,
                    used_by=excluded.used_by, last_updated=excluded.last_updated
                """,
                (
                    attribute,
                    name,
                    version,
                    store_path,
                    json.dumps(meta),
                    json.dumps(used_by),
                    now,
                    now,
                ),
            )

    def get_packages(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT attribute, name, version, store_path, meta"
                " FROM package_index ORDER BY attribute"
            ).fetchall()
            return [
                {
                    "attribute": r[0],
                    "name": r[1],
                    "version": r[2],
                    "store_path": r[3],
                    "meta": json.loads(r[4] or "{}"),
                }
                for r in rows
            ]

    # ---------------------------------------------------------- option_values

    def upsert_option_value(self, ov: OptionValue) -> None:
        with self._lock, self.transaction():
            self._conn.execute(
                """
                INSERT INTO option_values (key, expr, value_json, status,
                                           error, computed_at, source_rev)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    expr=excluded.expr, value_json=excluded.value_json,
                    status=excluded.status, error=excluded.error,
                    computed_at=excluded.computed_at, source_rev=excluded.source_rev
                """,
                (
                    ov.key,
                    ov.expr,
                    json.dumps(ov.value_json),
                    ov.status.value,
                    ov.error,
                    _dt_to_str(ov.computed_at),
                    ov.source_rev,
                ),
            )

    def get_option_value(self, key: str) -> OptionValue | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM option_values WHERE key=?", (key,)
            ).fetchone()
            return _row_to_option_value(row) if row else None

    def invalidate_option_values(self, file_paths: list[str]) -> None:
        """Mark stale cached evals whose expr mentions any affected file.

        Heuristic match: expr must contain the basename-without-extension of
        one of the paths (e.g. 'modules/nginx.nix' matches exprs about nginx).
        Precise invalidation lives in the EvalCache (phase 8).
        """
        stems = {Path(p).stem for p in file_paths} - {""}
        if not stems:
            return
        with self._lock, self.transaction():
            rows = self._conn.execute("SELECT key, expr FROM option_values").fetchall()
            stale_keys = [
                r[0]
                for r in rows
                if any(stem in (r[1] or "") for stem in stems)
            ]
            self._conn.executemany(
                "UPDATE option_values SET status='stale' WHERE key=?",
                [(k,) for k in stale_keys],
            )

    def invalidate_all_option_values(self) -> None:
        with self._lock, self.transaction():
            self._conn.execute("UPDATE option_values SET status='stale'")

    # ---------------------------------------------------------------- search

    def search_fts5(self, query: str, limit: int = 10) -> list[Node]:
        tokens = [t.replace('"', '""') for t in query.split() if t]
        if not tokens:
            return []
        match_expr = " ".join(f'"{t}"' for t in tokens)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT n.* FROM node_search s
                JOIN nodes n ON n.rowid = s.rowid
                WHERE node_search MATCH ?
                LIMIT ?
                """,
                (match_expr, limit),
            ).fetchall()
            return [_row_to_node(r) for r in rows]


# --------------------------------------------------------------- row helpers


def _row_to_node(row: sqlite3.Row) -> Node:
    return Node(
        id=row["id"],
        type=NodeType(row["type"]),
        name=row["name"],
        path=row["path"],
        lang=row["lang"],
        metadata=json.loads(row["metadata"] or "{}"),
        content_hash=row["content_hash"],
        ast_hash=row["ast_hash"],
        created_at=_dt_from_str(row["created_at"]) or datetime.now(UTC),
        updated_at=_dt_from_str(row["updated_at"]) or datetime.now(UTC),
    )


def _row_to_edge(row: sqlite3.Row) -> Edge:
    return Edge(
        id=row["id"],
        source=row["source"],
        target=row["target"],
        type=EdgeType(row["type"]),
        metadata=json.loads(row["metadata"] or "{}"),
        weight=row["weight"],
    )


def _row_to_file_state(row: sqlite3.Row) -> FileState:
    return FileState(
        path=row["path"],
        lang=row["lang"],
        content_hash=row["content_hash"],
        ast_hash=row["ast_hash"],
        merkle_hash=row["merkle_hash"],
        dirty=bool(row["dirty"]),
        last_parsed=_dt_from_str(row["last_parsed"]),
        detail_level=row["detail_level"],
    )


def _row_to_option_value(row: sqlite3.Row) -> OptionValue:
    return OptionValue(
        key=row["key"],
        expr=row["expr"],
        value_json=json.loads(row["value_json"]) if row["value_json"] else None,
        status=ValueStatus(row["status"]),
        error=row["error"],
        computed_at=_dt_from_str(row["computed_at"]) or datetime.now(UTC),
        source_rev=row["source_rev"],
    )
