"""Unit tests for QueryEngine navigation verbs (phase 6.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_navigator.graph.builder import GraphBuilder
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.graph.queries import QueryEngine
from repo_navigator.models.edges import EdgeType, RawEdge
from repo_navigator.models.nodes import NodeType, RawNode
from repo_navigator.models.queries import ParseResult


def _mod(path: str) -> RawNode:
    return RawNode(id=f"nix:{path}", type=NodeType.nix_module, name=path, path=path)


def _imports(src: str, dst: str) -> RawEdge:
    return RawEdge(source=f"nix:{src}", target=f"nix:{dst}", type=EdgeType.imports)


def _setup_chain(tmp_path: Path | None = None) -> tuple[Database, NxGraph, QueryEngine]:
    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    builder = GraphBuilder(db, g)
    # Chain a -> b -> c -> d
    builder.build_file("a.nix", ParseResult(nodes=[_mod("a.nix")], edges=[_imports("a.nix", "b.nix")]))
    builder.build_file("b.nix", ParseResult(nodes=[_mod("b.nix")], edges=[_imports("b.nix", "c.nix")]))
    builder.build_file("c.nix", ParseResult(nodes=[_mod("c.nix")], edges=[_imports("c.nix", "d.nix")]))
    builder.build_file("d.nix", ParseResult(nodes=[_mod("d.nix")], edges=[]))
    # Add option declared by b, set by c
    builder.build_file(
        "b.nix",
        ParseResult(
            nodes=[_mod("b.nix"), RawNode(id="nix_option:services.foo.enable", type=NodeType.nix_option, name="services.foo.enable")],
            edges=[
                _imports("b.nix", "c.nix"),
                RawEdge(source="nix:b.nix", target="nix_option:services.foo.enable", type=EdgeType.declares),
            ],
        ),
    )
    builder.build_file(
        "c.nix",
        ParseResult(
            nodes=[_mod("c.nix")],
            edges=[
                _imports("c.nix", "d.nix"),
                RawEdge(source="nix:c.nix", target="nix_option:services.foo.enable", type=EdgeType.sets),
            ],
        ),
    )
    engine = QueryEngine(db, g)
    return db, g, engine


class TestObserve:
    def test_observe_direct_neighbors(self) -> None:
        _, _, engine = _setup_chain()
        obs = engine.observe("nix:b.nix", depth=1)
        # b has incoming from a (imports) and outgoing to c and declares option
        neighbor_ids = {n.node.id for n in obs.neighbors}
        assert "nix:a.nix" in neighbor_ids or "nix:c.nix" in neighbor_ids
        assert obs.generation_id >= 1
        assert obs.node.id == "nix:b.nix"

    def test_observe_depth_limit(self) -> None:
        _, _, engine = _setup_chain()
        with pytest.raises(ValueError, match="depth must be <= 20"):
            engine.observe("nix:a.nix", depth=21)

    def test_observe_missing_node(self) -> None:
        _, _, engine = _setup_chain()
        with pytest.raises(KeyError):
            engine.observe("nix:missing.nix")


class TestHop:
    def test_hop_forward(self) -> None:
        _, _, engine = _setup_chain()
        sub = engine.hop("nix:a.nix", depth=2, width=10)
        ids = {n.id for n in sub.nodes}
        assert "nix:b.nix" in ids
        assert "nix:c.nix" in ids
        assert "nix:d.nix" not in ids  # depth 2 only reaches b,c

    def test_hop_with_relation(self) -> None:
        _, _, engine = _setup_chain()
        sub = engine.hop("nix:b.nix", relation="imports", depth=1, width=10)
        # Should only follow imports, not declares
        ids = {n.id for n in sub.nodes}
        assert "nix:c.nix" in ids
        assert "nix_option:services.foo.enable" not in ids

    def test_hop_budget(self) -> None:
        _, _, engine = _setup_chain()
        with pytest.raises(ValueError, match="budget exceeded"):
            engine.hop("nix:a.nix", depth=11, width=10)  # 110 >100

    def test_hop_depth_limit(self) -> None:
        _, _, engine = _setup_chain()
        with pytest.raises(ValueError, match="depth must be <=10"):
            engine.hop("nix:a.nix", depth=11, width=5)


class TestPath:
    def test_path_exists(self) -> None:
        _, _, engine = _setup_chain()
        steps = engine.path("nix:a.nix", "nix:d.nix")
        assert [s.node.id for s in steps] == ["nix:a.nix", "nix:b.nix", "nix:c.nix", "nix:d.nix"]
        assert steps[0].edge_in is None
        assert steps[1].edge_in is not None

    def test_path_unreachable(self) -> None:
        _, _, engine = _setup_chain()
        steps = engine.path("nix:d.nix", "nix:a.nix")
        assert steps == []

    def test_path_missing(self) -> None:
        _, _, engine = _setup_chain()
        assert engine.path("nix:a.nix", "nix:ghost.nix") == []


class TestBlastRadius:
    def test_blast_simple(self) -> None:
        _, _, engine = _setup_chain()
        sub = engine.blast_radius("nix:c.nix", max_depth=5)
        ids = {n.id for n in sub.nodes}
        # Who depends on c? b and a (transitively)
        assert "nix:b.nix" in ids
        assert "nix:a.nix" in ids
        assert "nix:c.nix" not in ids  # blast excludes source

    def test_blast_depth_limit(self) -> None:
        _, _, engine = _setup_chain()
        with pytest.raises(ValueError, match="max_depth must be <=10"):
            engine.blast_radius("nix:c.nix", max_depth=11)


class TestFindSymbol:
    def test_find_fts(self) -> None:
        db, g, engine = _setup_chain()
        # Add a node with distinctive name
        from repo_navigator.models.nodes import Node
        from datetime import UTC, datetime

        n = Node(id="nix_option:services.myapp.enable", type=NodeType.nix_option, name="services.myapp.enable", lang="nix")
        db.upsert_node(n)
        g.apply_delta(added_nodes=[n])
        results = engine.find_symbol("myapp", fuzzy=False)
        assert any("myapp" in r.name for r in results)

    def test_find_fuzzy(self) -> None:
        _, _, engine = _setup_chain()
        results = engine.find_symbol("services.foo", fuzzy=True)
        assert any("services.foo" in r.name for r in results)

    def test_find_lang_filter(self) -> None:
        _, _, engine = _setup_chain()
        results = engine.find_symbol("a.nix", lang="nix", fuzzy=True, limit=5)
        assert all(r.lang == "nix" for r in results)


class TestSummarizeModule:
    def test_summarize(self) -> None:
        _, _, engine = _setup_chain()
        summary = engine.summarize_module("b.nix")
        assert summary.path == "b.nix"
        assert any(e.type == EdgeType.declares for e in summary.outgoing_edges)
        assert "services.foo.enable" in summary.key_symbols

    def test_summarize_missing(self) -> None:
        _, _, engine = _setup_chain()
        with pytest.raises(KeyError):
            engine.summarize_module("missing.nix")


class TestImpactAnalysis:
    def test_impact(self) -> None:
        _, _, engine = _setup_chain()
        report = engine.impact_analysis("nix:c.nix", max_depth=5)
        assert "b.nix" in report.affected_modules or "a.nix" in report.affected_modules
        assert report.risk_level in ("low", "medium", "high")
        assert report.generation_id >= 1

    def test_impact_low(self) -> None:
        db, g, engine = _setup_chain()
        # Isolated node
        builder = GraphBuilder(db, g)
        builder.build_file("isolated.nix", ParseResult(nodes=[_mod("isolated.nix")], edges=[]))
        report = engine.impact_analysis("nix:isolated.nix")
        assert report.risk_level == "low"


class TestCache:
    def test_cache_invalidation_on_generation(self) -> None:
        db, g, engine = _setup_chain()
        obs1 = engine.observe("nix:b.nix")
        gen1 = obs1.generation_id
        # Trigger generation bump
        builder = GraphBuilder(db, g)
        builder.build_file("a.nix", ParseResult(nodes=[_mod("a.nix")], edges=[]))
        obs2 = engine.observe("nix:b.nix")
        # Cache should have been invalidated, generation increased
        assert obs2.generation_id == gen1 + 1
        # Even if node still exists, result is fresh
        assert obs2.node.id == "nix:b.nix"
