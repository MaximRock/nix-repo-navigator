"""Tests for package index (phase 9.2, mock)."""

from __future__ import annotations

from pathlib import Path

from repo_navigator.graph.builder import GraphBuilder
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.models.edges import EdgeType, RawEdge
from repo_navigator.models.nodes import NodeType, RawNode
from repo_navigator.models.queries import ParseResult
from repo_navigator.nix.package_index import PackageIndexBuilder, resolve_package


def _mod(path: str) -> RawNode:
    return RawNode(id=f"nix:{path}", type=NodeType.nix_module, name=path, path=path)


def test_resolve_package_mock() -> None:
    info = resolve_package("pkgs.ripgrep")
    assert info is not None
    assert info["name"] == "ripgrep"
    assert "store_path" in info
    assert info["meta"]["mock"] is True
    # Deterministic
    assert resolve_package("pkgs.ripgrep") == info
    assert resolve_package("pkgs.fd")["name"] == "fd"


def test_package_index_builder(tmp_path: Path) -> None:
    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    builder = GraphBuilder(db, g)
    # Two modules using ripgrep and fd
    builder.build_file(
        "a.nix",
        ParseResult(
            nodes=[_mod("a.nix")],
            edges=[
                RawEdge(source="nix:a.nix", target="package:pkgs.ripgrep", type=EdgeType.uses_package),
                RawEdge(source="nix:a.nix", target="package:pkgs.fd", type=EdgeType.uses_package),
            ],
        ),
    )
    builder.build_file(
        "b.nix",
        ParseResult(
            nodes=[_mod("b.nix")],
            edges=[RawEdge(source="nix:b.nix", target="package:pkgs.ripgrep", type=EdgeType.uses_package)],
        ),
    )
    # Create package_ref nodes for the builder's placeholders
    from repo_navigator.models.nodes import Node

    for attr in ["pkgs.ripgrep", "pkgs.fd"]:
        nid = f"package:{attr}"
        if db.get_node(nid) is None:
            db.upsert_node(Node(id=nid, type=NodeType.package_ref, name=attr))

    pkg_builder = PackageIndexBuilder(db, root=tmp_path)
    count = pkg_builder.refresh()
    assert count == 2
    pkgs = db.get_packages()
    assert len(pkgs) == 2
    ripgrep = next(p for p in pkgs if p["attribute"] == "pkgs.ripgrep")
    assert ripgrep["name"] == "ripgrep"
    # used_by should contain both a.nix and b.nix for ripgrep
    import json

    # Check used_by via direct DB query
    row = db._conn.execute("SELECT used_by FROM package_index WHERE attribute='pkgs.ripgrep'").fetchone()
    used_by = json.loads(row[0]) if row and row[0] else []
    assert "a.nix" in used_by
    assert "b.nix" in used_by


def test_package_index_query(tmp_path: Path) -> None:
    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    from repo_navigator.graph.queries import QueryEngine

    # Insert mock packages directly
    db.upsert_package("pkgs.ripgrep", "ripgrep", "1.0", "/nix/store/abc-ripgrep", {"desc": "x"})
    db.upsert_package("pkgs.hello", "hello", "2.0", "/nix/store/def-hello", {"desc": "y"})
    engine = QueryEngine(db, g)
    all_pkgs = engine.list_packages()
    assert len(all_pkgs) == 2
    filtered = engine.list_packages(query="ripgrep")
    assert len(filtered) == 1
    assert filtered[0]["attribute"] == "pkgs.ripgrep"
    assert engine.get_package("pkgs.hello") is not None
    assert engine.get_package("missing") is None
