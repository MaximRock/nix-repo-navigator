"""Unit tests for the NetworkX wrapper (graph/nx_graph.py)."""

from __future__ import annotations

import threading

import pytest

from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.models.edges import Edge, EdgeType
from repo_navigator.models.nodes import Node, NodeType


def mod(path: str) -> Node:
    return Node(id=f"nix:{path}", type=NodeType.nix_module, name=path, path=path)


def imp(edge_id: str, src: str, dst: str, weight: float = 1.0) -> Edge:
    return Edge(
        id=edge_id,
        source=f"nix:{src}",
        target=f"nix:{dst}",
        type=EdgeType.imports,
        weight=weight,
    )


@pytest.fixture()
def graph() -> NxGraph:
    g = NxGraph()
    g.rebuild(
        nodes=[mod("a.nix"), mod("b.nix"), mod("c.nix"), mod("d.nix")],
        edges=[imp("e1", "a.nix", "b.nix"), imp("e2", "b.nix", "c.nix")],
    )
    return g


class TestRebuildAndCounts:
    def test_counts_after_rebuild(self, graph: NxGraph) -> None:
        assert graph.number_of_nodes() == 4
        assert graph.number_of_edges() == 2

    def test_rebuild_replaces_content(self, graph: NxGraph) -> None:
        graph.rebuild([mod("x.nix")], [])
        assert graph.number_of_nodes() == 1
        assert graph.number_of_edges() == 0


class TestApplyDelta:
    def test_add_node_and_edge(self, graph: NxGraph) -> None:
        graph.apply_delta(
            added_nodes=[mod("e.nix")],
            added_edges=[imp("e3", "c.nix", "e.nix")],
        )
        assert graph.number_of_nodes() == 5
        assert graph.number_of_edges() == 3

    def test_edge_to_missing_node_is_dropped(self, graph: NxGraph) -> None:
        graph.apply_delta(added_edges=[imp("bad", "a.nix", "ghost.nix")])
        assert graph.number_of_edges() == 2

    def test_remove_edge_by_id(self, graph: NxGraph) -> None:
        graph.apply_delta(removed_edge_ids=["e1"])
        assert graph.number_of_edges() == 1

    def test_remove_node_cascades_edges(self, graph: NxGraph) -> None:
        graph.apply_delta(removed_node_ids=["nix:b.nix"])
        assert graph.number_of_nodes() == 3
        assert graph.number_of_edges() == 0  # both e1 and e2 touched b.nix


class TestParallelEdges:
    def test_parallel_edges_kept_separate(self) -> None:
        g = NxGraph()
        g.rebuild([mod("a.nix"), mod("b.nix")], [])
        g.apply_delta(
            added_edges=[
                imp("p1", "a.nix", "b.nix"),
                Edge(id="p2", source="nix:a.nix", target="nix:b.nix",
                     type=EdgeType.generates),
            ]
        )
        assert g.number_of_edges() == 2

        g.apply_delta(removed_edge_ids=["p1"])
        assert g.number_of_edges() == 1
        g.apply_delta(removed_edge_ids=["p2"])
        assert g.number_of_edges() == 0


class TestBfs:
    def test_depth_limit(self, graph: NxGraph) -> None:
        found = {n.id for n in graph.bfs("nix:a.nix", depth=1, width=10)}
        assert found == {"nix:b.nix"}

    def test_transitive(self, graph: NxGraph) -> None:
        found = {n.id for n in graph.bfs("nix:a.nix", depth=5, width=10)}
        assert found == {"nix:b.nix", "nix:c.nix"}

    def test_width_limit_per_node(self) -> None:
        g = NxGraph()
        nodes = [mod("hub.nix")] + [mod(f"leaf{i}.nix") for i in range(5)]
        edges = [imp(f"i{i}", "hub.nix", f"leaf{i}.nix") for i in range(5)]
        g.rebuild(nodes, edges)
        assert len(g.bfs("nix:hub.nix", depth=1, width=2)) == 2

    def test_missing_source(self, graph: NxGraph) -> None:
        assert graph.bfs("ghost", depth=2, width=10) == []


class TestReverseBfs:
    def test_finds_dependents_transitively(self, graph: NxGraph) -> None:
        graph.apply_delta(
            added_nodes=[mod("z.nix")],
            added_edges=[imp("e9", "d.nix", "a.nix")],
        )
        dependents = {n.id for n in graph.reverse_bfs("nix:c.nix", max_depth=10)}
        # who imports c? b; and who imports b? a; and d imports a
        assert {"nix:b.nix", "nix:a.nix", "nix:d.nix"} <= dependents
        assert "nix:c.nix" not in dependents


class TestShortestPath:
    def test_weighted_shortest_path(self) -> None:
        g = NxGraph()
        g.rebuild(
            [mod("A"), mod("B"), mod("C"), mod("D")],
            [
                imp("direct", "A", "D", weight=10),
                imp("ab", "A", "B", weight=1),
                imp("bc", "B", "C", weight=1),
                imp("cd", "C", "D", weight=1),
            ],
        )
        steps = g.shortest_path("nix:A", "nix:D")
        assert [s.node.id for s in steps] == ["nix:A", "nix:B", "nix:C", "nix:D"]
        assert steps[0].depth == 0 and steps[0].edge_in is None
        assert steps[3].edge_in.id == "cd"

    def test_unreachable_empty(self, graph: NxGraph) -> None:
        graph.apply_delta(added_nodes=[mod("lonely.nix")])
        assert graph.shortest_path("nix:a.nix", "nix:lonely.nix") == []

    def test_missing_endpoints_empty(self, graph: NxGraph) -> None:
        assert graph.shortest_path("ghost", "nix:a.nix") == []


class TestReadonlyCopy:
    def test_copy_is_independent(self, graph: NxGraph) -> None:
        snapshot = graph.get_graph_readonly()
        snapshot.remove_node("nix:a.nix")
        assert graph.has_node("nix:a.nix")


class TestConcurrency:
    def test_readers_survive_writes(self, graph: NxGraph) -> None:
        errors: list[Exception] = []
        stop = threading.Event()

        def reader() -> None:
            while not stop.is_set():
                try:
                    graph.bfs("nix:a.nix", depth=3, width=10)
                    graph.get_graph_readonly()
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

        readers = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
        for t in readers:
            t.start()

        try:
            for i in range(30):
                tmp = mod(f"tmp{i}.nix")
                graph.apply_delta(added_nodes=[tmp])
                graph.apply_delta(removed_node_ids=[tmp.id])
        finally:
            stop.set()
            for t in readers:
                t.join(timeout=5)

        assert errors == []
