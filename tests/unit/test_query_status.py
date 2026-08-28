"""Tests for QueryEngine status/refresh (phase 6.3)."""

from __future__ import annotations

from pathlib import Path

from repo_navigator.graph.builder import GraphBuilder
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.graph.queries import QueryEngine
from repo_navigator.models.edges import EdgeType, RawEdge
from repo_navigator.models.nodes import NodeType, RawNode
from repo_navigator.models.queries import ParseResult


def _mod(path: str) -> RawNode:
    return RawNode(id=f"nix:{path}", type=NodeType.nix_module, name=path, path=path)


def test_status_empty(tmp_path: Path) -> None:
    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    engine = QueryEngine(db, g)
    status = engine.status()
    assert status.total_nodes == 0
    assert status.total_edges == 0
    assert status.generation_id == 0
    assert status.uptime >= 0
    assert status.mode in ("static", "hybrid")


def test_status_with_data(tmp_path: Path) -> None:
    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    builder = GraphBuilder(db, g)
    builder.build_file("a.nix", ParseResult(nodes=[_mod("a.nix")], edges=[]))
    engine = QueryEngine(db, g)
    status = engine.status()
    assert status.total_nodes == 1
    assert status.total_edges == 0
    assert status.generation_id == 1


def test_refresh_rescans(tmp_path: Path) -> None:
    # Create a repo with two files
    (tmp_path / "a.nix").write_text('{ imports = [ ./b.nix ]; }')
    (tmp_path / "b.nix").write_text('{ config.x = 1; }')
    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    from repo_navigator.config import Config

    cfg = Config(root=tmp_path)
    engine = QueryEngine(db, g, config=cfg)
    # First refresh should index both
    status = engine.refresh()
    assert status.total_nodes >= 2
    assert status.generation_id >= 1
    # Second refresh should be idempotent (still same generation +1)
    gen1 = status.generation_id
    status2 = engine.refresh()
    assert status2.generation_id == gen1 + 1
