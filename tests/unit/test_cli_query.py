"""Tests for CLI query group (phase 6.3)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from repo_navigator.cli import app
from repo_navigator.graph.builder import GraphBuilder
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.models.edges import EdgeType, RawEdge
from repo_navigator.models.nodes import NodeType, RawNode
from repo_navigator.models.queries import ParseResult

runner = CliRunner()


def _mod(path: str) -> RawNode:
    return RawNode(id=f"nix:{path}", type=NodeType.nix_module, name=path, path=path)


def _setup_db(tmp_path: Path, db_path: Path) -> None:
    db = Database(str(db_path))
    db.init_db()
    g = NxGraph()
    builder = GraphBuilder(db, g)
    builder.build_file("a.nix", ParseResult(nodes=[_mod("a.nix")], edges=[RawEdge(source="nix:a.nix", target="nix:b.nix", type=EdgeType.imports)]))
    builder.build_file("b.nix", ParseResult(nodes=[_mod("b.nix")], edges=[]))
    db.close()


def test_query_observe(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _setup_db(tmp_path, db_path)
    result = runner.invoke(app, ["query", "observe", "nix:a.nix", "--root", str(tmp_path), "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["node"]["id"] == "nix:a.nix"
    assert "generation_id" in data


def test_query_hop(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _setup_db(tmp_path, db_path)
    result = runner.invoke(app, ["query", "hop", "nix:a.nix", "--depth", "2", "--root", str(tmp_path), "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "nodes" in data


def test_query_path(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _setup_db(tmp_path, db_path)
    result = runner.invoke(app, ["query", "path", "nix:a.nix", "nix:b.nix", "--root", str(tmp_path), "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) >= 2


def test_query_find(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _setup_db(tmp_path, db_path)
    result = runner.invoke(app, ["query", "find", "a.nix", "--root", str(tmp_path), "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)


def test_query_summarize(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _setup_db(tmp_path, db_path)
    result = runner.invoke(app, ["query", "summarize", "a.nix", "--root", str(tmp_path), "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["path"] == "a.nix"


def test_query_status(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _setup_db(tmp_path, db_path)
    result = runner.invoke(app, ["query", "status", "--root", str(tmp_path), "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "total_nodes" in data
    assert "generation_id" in data


def test_query_observe_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _setup_db(tmp_path, db_path)
    result = runner.invoke(app, ["query", "observe", "nix:missing.nix", "--root", str(tmp_path), "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "error" in result.output.lower()
