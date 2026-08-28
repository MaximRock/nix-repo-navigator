"""Tests for flake inputs via QueryEngine and index (phase 9.1)."""

from __future__ import annotations

import json
from pathlib import Path

from repo_navigator.config import Config
from repo_navigator.graph.builder import GraphBuilder
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.graph.queries import QueryEngine
from repo_navigator.indexer.scan import index_repo


def test_index_flake_inputs(tmp_path: Path) -> None:
    # Create flake.lock
    lock = {
        "nodes": {
            "root": {},
            "nixpkgs": {"locked": {"type": "github", "owner": "NixOS", "repo": "nixpkgs", "rev": "abc", "url": "https://github.com/NixOS/nixpkgs"}},
            "hm": {"locked": {"type": "github", "owner": "nix-community", "repo": "home-manager", "rev": "def"}},
        },
        "root": "root",
        "version": 7,
    }
    (tmp_path / "flake.lock").write_text(json.dumps(lock))
    (tmp_path / "flake.nix").write_text('{ inputs.nixpkgs.url = "github:NixOS/nixpkgs"; }')
    (tmp_path / "a.nix").write_text('{ config.x = 1; }')

    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    cfg = Config(root=tmp_path)
    stats = index_repo(tmp_path, db, g, config=cfg)

    # Flake inputs should be in DB and graph
    inputs = db.get_flake_inputs()
    assert len(inputs) == 2
    names = {i["name"] for i in inputs}
    assert "nixpkgs" in names
    assert "hm" in names
    # Graph nodes
    assert db.get_node("flake_input:nixpkgs") is not None
    assert db.get_node("flake_input:hm") is not None
    # QueryEngine
    engine = QueryEngine(db, g, config=cfg)
    listed = engine.list_flake_inputs()
    assert len(listed) == 2


def test_query_flake_cli(tmp_path: Path) -> None:
    from typer.testing import CliRunner
    from repo_navigator.cli import app

    runner = CliRunner()
    lock = {"nodes": {"root": {}, "nixpkgs": {"locked": {"rev": "abc", "url": "https://example.com"}}}, "version": 7}
    (tmp_path / "flake.lock").write_text(json.dumps(lock))
    (tmp_path / "a.nix").write_text('{ config.x = 1; }')
    db_path = tmp_path / "test.db"
    result = runner.invoke(app, ["index", str(tmp_path), "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["query", "flake-inputs", "--root", str(tmp_path), "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert any(d["name"] == "nixpkgs" for d in data)


def test_mcp_flake_inputs(tmp_path: Path) -> None:
    import pytest

    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    # Manually insert flake input
    db.upsert_flake_input("nixpkgs", "https://github.com/NixOS/nixpkgs", "abc")
    from repo_navigator.models.nodes import Node, NodeType

    n = Node(id="flake_input:nixpkgs", type=NodeType.flake_input, name="nixpkgs", metadata={"url": "https://github.com/NixOS/nixpkgs"})
    db.upsert_node(n)
    g.apply_delta(added_nodes=[n])
    from repo_navigator.graph.queries import QueryEngine
    from repo_navigator.mcp_server import create_mcp_server

    engine = QueryEngine(db, g)
    server = create_mcp_server(engine=engine)

    async def _run():
        tools = await server.list_tools()
        assert any(t.name == "repo_navigator_list_flake_inputs" for t in tools)
        res = await server.call_tool("repo_navigator_list_flake_inputs", {})
        assert not res.is_error
        # Should contain nixpkgs
        assert any("nixpkgs" in str(res.structured_content) for _ in [1])

    import asyncio

    asyncio.run(_run())
