"""Unit tests for QueryEngine navigation verbs (phase 6.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_navigator.config import Config
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
    builder.build_file(
        "a.nix", ParseResult(nodes=[_mod("a.nix")], edges=[_imports("a.nix", "b.nix")])
    )
    builder.build_file(
        "b.nix", ParseResult(nodes=[_mod("b.nix")], edges=[_imports("b.nix", "c.nix")])
    )
    builder.build_file(
        "c.nix", ParseResult(nodes=[_mod("c.nix")], edges=[_imports("c.nix", "d.nix")])
    )
    builder.build_file("d.nix", ParseResult(nodes=[_mod("d.nix")], edges=[]))
    # Add option declared by b, set by c
    builder.build_file(
        "b.nix",
        ParseResult(
            nodes=[
                _mod("b.nix"),
                RawNode(
                    id="nix_option:services.foo.enable",
                    type=NodeType.nix_option,
                    name="services.foo.enable",
                ),
            ],
            edges=[
                _imports("b.nix", "c.nix"),
                RawEdge(
                    source="nix:b.nix",
                    target="nix_option:services.foo.enable",
                    type=EdgeType.declares,
                ),
            ],
        ),
    )
    builder.build_file(
        "c.nix",
        ParseResult(
            nodes=[_mod("c.nix")],
            edges=[
                _imports("c.nix", "d.nix"),
                RawEdge(
                    source="nix:c.nix",
                    target="nix_option:services.foo.enable",
                    type=EdgeType.sets,
                ),
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
        assert [s.node.id for s in steps] == [
            "nix:a.nix",
            "nix:b.nix",
            "nix:c.nix",
            "nix:d.nix",
        ]
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

    def test_blast_no_duplicate_edges(self) -> None:
        # BUG 2: the first edge-scan never registered edge ids, so the
        # per-node scan re-appended the same edges -> duplicates.
        _, _, engine = _setup_chain()
        sub = engine.blast_radius("nix:c.nix", max_depth=5)
        edge_ids = [e.id for e in sub.edges]
        assert len(edge_ids) == len(set(edge_ids))
        # Intended blast subgraph edges: chain a->b->c plus the declares
        # edge leaving b (second scan reaches non-blast targets too).
        assert set(edge_ids) == {
            "nix:a.nix->imports->nix:b.nix",
            "nix:b.nix->imports->nix:c.nix",
            "nix:b.nix->declares->nix_option:services.foo.enable",
        }

    def test_blast_depth_limit(self) -> None:
        _, _, engine = _setup_chain()
        with pytest.raises(ValueError, match="max_depth must be <=10"):
            engine.blast_radius("nix:c.nix", max_depth=11)


class TestFindSymbol:
    def test_find_fts(self) -> None:
        db, g, engine = _setup_chain()
        # Add a node with distinctive name
        from repo_navigator.models.nodes import Node

        n = Node(
            id="nix_option:services.myapp.enable",
            type=NodeType.nix_option,
            name="services.myapp.enable",
            lang="nix",
        )
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

    def test_find_node_type_filter(self) -> None:
        db, g, engine = _setup_chain()
        from repo_navigator.models.nodes import Node

        for node_type, name in [
            (NodeType.py_function, "run"),
            (NodeType.py_class, "Runner"),
        ]:
            n = Node(
                id=f"python:scripts/x.py:{name}",
                type=node_type,
                name=name,
                path="scripts/x.py",
                lang="python",
            )
            db.upsert_node(n)
            g.apply_delta(added_nodes=[n])
        funcs = engine.find_symbol("x.py", node_type="py_function", fuzzy=True)
        assert all(r.type == NodeType.py_function for r in funcs)
        classes = engine.find_symbol(
            "x.py", node_type=["py_function", "py_class"], fuzzy=True
        )
        assert len(classes) == 2

    def test_find_path_contains_filter(self, tmp_path: Path) -> None:
        db, g, engine = _setup_chain()
        from repo_navigator.models.nodes import Node

        n1 = Node(
            id="py_func:modules/mod.py:f",
            type=NodeType.py_function,
            name="f",
            path="modules/mod.py",
            lang="python",
        )
        n2 = Node(
            id="py_func:scripts/tool.py:g",
            type=NodeType.py_function,
            name="g",
            path="scripts/tool.py",
            lang="python",
        )
        db.upsert_node(n1)
        db.upsert_node(n2)
        g.apply_delta(added_nodes=[n1, n2])
        results = engine.find_symbol(":", path_contains="scripts", fuzzy=True)
        assert all("scripts" in (r.path or "") for r in results)
        assert all(r.id != "py_func:modules/mod.py:f" for r in results)

    def test_find_id_prefix_filter(self) -> None:
        _, _, engine = _setup_chain()
        results = engine.find_symbol(
            "nix", id_prefix="nix_option:", fuzzy=True, limit=20
        )
        assert all(r.id.startswith("nix_option:") for r in results)

    def test_find_offset_pagination(self) -> None:
        _, _, engine = _setup_chain()
        all_results = engine.find_symbol("nix", fuzzy=True, limit=100)
        if len(all_results) >= 2:
            page2 = engine.find_symbol("nix", fuzzy=True, limit=1, offset=1)
            assert len(page2) == 1
            assert page2[0].id == all_results[1].id


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
        builder.build_file(
            "isolated.nix", ParseResult(nodes=[_mod("isolated.nix")], edges=[])
        )
        report = engine.impact_analysis("nix:isolated.nix")
        assert report.risk_level == "low"

    def test_impact_evidence_chains(self) -> None:
        _, _, engine = _setup_chain()
        report = engine.impact_analysis("nix:d.nix", max_depth=3)
        ids = [e.node_id for e in report.evidence]
        assert "nix:c.nix" in ids
        assert "nix:b.nix" in ids
        # The chain for c should start at d (root) and cross the imports edge d->c
        c_ev = next(e for e in report.evidence if e.node_id == "nix:c.nix")
        assert c_ev.steps
        assert c_ev.steps[0].node.id == "nix:d.nix"
        assert c_ev.steps[0].edge_in is None
        assert c_ev.steps[1].node.id == "nix:c.nix"
        assert c_ev.steps[1].edge_in is not None
        assert c_ev.steps[1].edge_in.type == EdgeType.imports
        assert c_ev.steps[1].depth == 1


class TestDependenciesClosure:
    def test_dependencies_forward(self) -> None:
        _, _, engine = _setup_chain()
        report = engine.dependencies("nix:a.nix", max_depth=3)
        ids = {e.node.id for e in report.depends_on}
        assert ids == {"nix:b.nix", "nix:c.nix", "nix:d.nix"}
        by_id = {e.node.id: e for e in report.depends_on}
        assert by_id["nix:b.nix"].depth == 1
        assert by_id["nix:c.nix"].depth == 2
        assert by_id["nix:d.nix"].depth == 3

    def test_dependents_reverse(self) -> None:
        _, _, engine = _setup_chain()
        report = engine.dependents("nix:d.nix", max_depth=3)
        ids = {e.node.id for e in report.dependents}
        assert ids == {"nix:c.nix", "nix:b.nix", "nix:a.nix"}

    def test_closure_depth_limit(self) -> None:
        _, _, engine = _setup_chain()
        report = engine.dependencies("nix:a.nix", max_depth=2)
        ids = {e.node.id for e in report.depends_on}
        assert "nix:c.nix" in ids
        assert "nix:d.nix" not in ids

    def test_closure_missing_node(self) -> None:
        _, _, engine = _setup_chain()
        with pytest.raises(KeyError):
            engine.dependencies("nix:missing.nix")
        with pytest.raises(ValueError):
            engine.dependents("nix:a.nix", max_depth=11)

    def test_closure_excludes_nondependency_edges(self) -> None:
        _, _, engine = _setup_chain()
        # declares edge b->option must not appear in the closure
        report = engine.dependencies("nix:b.nix", max_depth=3)
        ids = {e.node.id for e in report.depends_on}
        assert all("services.foo" not in i for i in ids)


class TestBenefitReport:
    def test_report_empty(self) -> None:
        _, _, engine = _setup_chain()
        r = engine.report()
        assert r.queries_served == 0
        assert r.queries_by_tool == {}
        assert r.files_served == 0
        assert r.bytes_not_reread == 0
        assert r.tokens_estimated_saved == 0
        assert r.generation_id >= 1

    def test_report_counts_by_tool(self) -> None:
        _, _, engine = _setup_chain()
        engine.observe("nix:b.nix")
        engine.dependencies("nix:a.nix", max_depth=2)
        engine.status()
        r = engine.report()
        assert r.queries_served == 3
        assert r.queries_by_tool["observe"] == 1
        assert r.queries_by_tool["dependencies"] == 1
        assert r.queries_by_tool["status"] == 1

    def test_report_tracks_source_bytes(self, tmp_path: Path) -> None:
        db = Database(":memory:")
        db.init_db()
        g = NxGraph()
        builder = GraphBuilder(db, g)
        content = b"option = 1; # exactly forty bytes here.........\n"
        src = tmp_path / "a.nix"
        src.write_bytes(content)
        builder.build_file("a.nix", ParseResult(nodes=[_mod("a.nix")], edges=[]))
        engine = QueryEngine(db, g, config=Config(root=tmp_path, _env_file=None))
        engine.observe("nix:a.nix")
        r = engine.report()
        assert r.files_served == 1
        assert r.bytes_not_reread == len(content)
        assert r.tokens_estimated_saved == len(content) // 4
        # Repeated lookups do not double-count the same file.
        engine.observe("nix:a.nix")
        assert engine.report().bytes_not_reread == len(content)

    def test_status_exposes_summary_line(self) -> None:
        _, _, engine = _setup_chain()
        engine.observe("nix:b.nix")
        status = engine.status()
        # status excludes itself: reports the count as of the previous call
        assert status.queries_served == 1
        assert status.tokens_estimated_saved >= 0


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
