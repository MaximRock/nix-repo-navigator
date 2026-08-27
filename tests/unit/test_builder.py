"""Unit tests for graph builder (phase 4.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_navigator.graph.builder import GraphBuilder
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.models.edges import EdgeType, RawEdge
from repo_navigator.models.nodes import NodeType, RawNode
from repo_navigator.models.queries import ParseResult


@pytest.fixture()
def db() -> Database:
    database = Database(":memory:")
    database.init_db()
    return database


@pytest.fixture()
def graph() -> NxGraph:
    return NxGraph()


@pytest.fixture()
def builder(db: Database, graph: NxGraph) -> GraphBuilder:
    return GraphBuilder(db, graph)


def _mod(path: str) -> RawNode:
    return RawNode(id=f"nix:{path}", type=NodeType.nix_module, name=path, path=path)


def _opt(attr: str) -> RawNode:
    return RawNode(id=f"nix_option:{attr}", type=NodeType.nix_option, name=attr, lang="nix")


def _imports(src: str, dst: str, **meta) -> RawEdge:
    return RawEdge(source=f"nix:{src}", target=f"nix:{dst}", type=EdgeType.imports, metadata=dict(meta))


def _sets(src: str, attr: str, **meta) -> RawEdge:
    return RawEdge(source=f"nix:{src}", target=f"nix_option:{attr}", type=EdgeType.sets, metadata=dict(meta))


def _declares(src: str, attr: str) -> RawEdge:
    return RawEdge(source=f"nix:{src}", target=f"nix_option:{attr}", type=EdgeType.declares)


# ------------------------------------------------------------------ basic


class TestBuildFile:
    def test_creates_nodes_and_edges(self, builder: GraphBuilder, db: Database, graph: NxGraph) -> None:
        pr = ParseResult(
            nodes=[_mod("a.nix")],
            edges=[_imports("a.nix", "b.nix")],
        )
        builder.build_file("a.nix", pr)

        assert db.get_node("nix:a.nix") is not None
        # placeholder for b
        assert db.get_node("nix:b.nix") is not None
        assert db.count_nodes() == 2
        assert db.count_edges() == 1

        assert graph.has_node("nix:a.nix")
        assert graph.has_node("nix:b.nix")
        assert graph.number_of_edges() == 1

        assert db.get_generation_id() == 1

    def test_placeholder_for_option(self, builder: GraphBuilder, db: Database) -> None:
        pr = ParseResult(
            nodes=[_mod("a.nix")],
            edges=[_sets("a.nix", "services.foo.enable")],
        )
        builder.build_file("a.nix", pr)
        assert db.get_node("nix_option:services.foo.enable") is not None
        assert db.get_node("nix_option:services.foo.enable").metadata.get("synthetic") is True

    def test_placeholder_for_package_and_file(self, builder: GraphBuilder, db: Database) -> None:
        pr = ParseResult(
            nodes=[_mod("a.nix"), RawNode(id="package:ripgrep", type=NodeType.package_ref, name="ripgrep")],
            edges=[
                RawEdge(source="nix:a.nix", target="package:ripgrep", type=EdgeType.uses_package),
                RawEdge(source="nix:a.nix", target="file:.config/foo", type=EdgeType.configures),
            ],
        )
        builder.build_file("a.nix", pr)
        # package node was explicit, file placeholder synthetic
        assert db.get_node("package:ripgrep") is not None
        assert db.get_node("file:.config/foo") is not None

    def test_replaces_old_subgraph(self, builder: GraphBuilder, db: Database, graph: NxGraph) -> None:
        # First build: a imports b and c
        pr1 = ParseResult(
            nodes=[_mod("a.nix")],
            edges=[_imports("a.nix", "b.nix"), _imports("a.nix", "c.nix")],
        )
        builder.build_file("a.nix", pr1)
        assert db.count_edges() == 2

        # Rebuild a: only imports b
        pr2 = ParseResult(
            nodes=[_mod("a.nix")],
            edges=[_imports("a.nix", "b.nix")],
        )
        builder.build_file("a.nix", pr2)
        assert db.count_edges() == 1
        # c placeholder remains (global) but edge to it is gone
        # NxGraph also updated
        assert graph.number_of_edges() == 1
        # Generation incremented twice
        assert db.get_generation_id() == 2

    def test_placeholder_replaced_by_real(self, builder: GraphBuilder, db: Database, graph: NxGraph) -> None:
        # a imports b -> placeholder b
        builder.build_file("a.nix", ParseResult(nodes=[_mod("a.nix")], edges=[_imports("a.nix", "b.nix")]))
        ph = db.get_node("nix:b.nix")
        assert ph is not None
        assert ph.metadata.get("synthetic") is True

        # Now build real b
        builder.build_file("b.nix", ParseResult(nodes=[_mod("b.nix")], edges=[]))
        real = db.get_node("nix:b.nix")
        assert real is not None
        # Real should overwrite placeholder (synthetic no longer set, path still b.nix)
        # Our real RawNode has no synthetic flag, so metadata should not contain synthetic True
        assert real.metadata.get("synthetic") is not True
        # Edge a->b still valid in both DB and NxGraph
        assert db.count_edges() == 1
        assert graph.has_node("nix:b.nix")
        assert graph.number_of_edges() == 1

    def test_incoming_edge_preserved_after_target_rebuild(self, builder: GraphBuilder, db: Database, graph: NxGraph) -> None:
        # Build b first
        builder.build_file("b.nix", ParseResult(nodes=[_mod("b.nix")], edges=[]))
        # Build a that imports b
        builder.build_file("a.nix", ParseResult(nodes=[_mod("a.nix")], edges=[_imports("a.nix", "b.nix")]))
        assert db.count_edges() == 1

        # Rebuild b with new content (declares option)
        builder.build_file("b.nix", ParseResult(nodes=[_mod("b.nix"), _opt("x")], edges=[_declares("b.nix", "x")]))
        # Incoming edge a->b must survive
        assert db.get_edge("nix:a.nix->imports->nix:b.nix") is not None or db.count_edges() == 2
        # Check that a->b edge still exists
        edges = db.get_all_edges()
        assert any(e.source == "nix:a.nix" and e.target == "nix:b.nix" for e in edges)
        assert graph.has_node("nix:a.nix")
        assert graph.has_node("nix:b.nix")

    def test_generation_increments(self, builder: GraphBuilder, db: Database) -> None:
        assert db.get_generation_id() == 0
        builder.build_file("a.nix", ParseResult(nodes=[_mod("a.nix")], edges=[]))
        assert db.get_generation_id() == 1
        builder.build_file("a.nix", ParseResult(nodes=[_mod("a.nix")], edges=[]))
        assert db.get_generation_id() == 2

    def test_deduplication(self, builder: GraphBuilder, db: Database) -> None:
        # Duplicate nodes/edges in same ParseResult should not create duplicates
        pr = ParseResult(
            nodes=[_mod("a.nix"), _mod("a.nix")],
            edges=[_imports("a.nix", "b.nix"), _imports("a.nix", "b.nix")],
        )
        builder.build_file("a.nix", pr)
        assert db.count_nodes() == 2  # a + placeholder b
        assert db.count_edges() == 1

    def test_conditional_metadata_affects_edge_id(self, builder: GraphBuilder, db: Database) -> None:
        # Two edges same source->target but different conditional should be distinct
        pr = ParseResult(
            nodes=[_mod("a.nix")],
            edges=[
                _imports("a.nix", "b.nix", conditional=False),
                _imports("a.nix", "b.nix", conditional=True),
            ],
        )
        builder.build_file("a.nix", pr)
        # Should have 2 distinct edges (different hash)
        assert db.count_edges() == 2


class TestBuildAll:
    def test_bulk_build(self, builder: GraphBuilder, db: Database, graph: NxGraph) -> None:
        items = [
            (Path("a.nix"), ParseResult(nodes=[_mod("a.nix")], edges=[_imports("a.nix", "b.nix")])),
            (Path("b.nix"), ParseResult(nodes=[_mod("b.nix")], edges=[_imports("b.nix", "c.nix")])),
        ]
        builder.build_all(items)
        assert db.get_node("nix:a.nix") is not None
        assert db.get_node("nix:b.nix") is not None
        # c is placeholder
        assert db.get_node("nix:c.nix") is not None
        assert db.count_edges() == 2
        assert graph.number_of_nodes() == 3
        assert graph.number_of_edges() == 2
        # Only one generation increment for bulk
        assert db.get_generation_id() == 1

    def test_build_all_replaces(self, builder: GraphBuilder, db: Database) -> None:
        # Initial build
        builder.build_file("a.nix", ParseResult(nodes=[_mod("a.nix")], edges=[_imports("a.nix", "b.nix")]))
        assert db.count_edges() == 1
        # Bulk rebuild a with different edge
        builder.build_all([(Path("a.nix"), ParseResult(nodes=[_mod("a.nix")], edges=[_imports("a.nix", "c.nix")]))])
        edges = db.get_all_edges()
        assert any(e.target == "nix:c.nix" for e in edges)
        assert not any(e.target == "nix:b.nix" for e in edges)

    def test_build_all_empty(self, builder: GraphBuilder, db: Database) -> None:
        builder.build_all([])
        assert db.get_generation_id() == 1
        assert db.count_nodes() == 0
