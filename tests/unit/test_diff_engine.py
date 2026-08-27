"""Unit tests for diff_engine (phase 5.1)."""

from __future__ import annotations

from repo_navigator.indexer.diff_engine import diff_graph
from repo_navigator.models.edges import EdgeType, RawEdge
from repo_navigator.models.nodes import NodeType, RawNode
from repo_navigator.models.queries import ParseResult


def _mod(path: str) -> RawNode:
    return RawNode(id=f"nix:{path}", type=NodeType.nix_module, name=path, path=path)


def _opt(attr: str, desc: str = "") -> RawNode:
    return RawNode(id=f"nix_option:{attr}", type=NodeType.nix_option, name=attr, metadata={"description": desc})


def _imports(src: str, dst: str) -> RawEdge:
    return RawEdge(source=f"nix:{src}", target=f"nix:{dst}", type=EdgeType.imports)


def _sets(src: str, attr: str, cond: bool = False) -> RawEdge:
    return RawEdge(source=f"nix:{src}", target=f"nix_option:{attr}", type=EdgeType.sets, metadata={"conditional": cond})


class TestDiffGraph:
    def test_no_changes(self) -> None:
        pr = ParseResult(nodes=[_mod("a.nix")], edges=[_imports("a.nix", "b.nix")])
        diff = diff_graph(pr, pr)
        assert not diff.has_changes
        assert diff.added_nodes == []
        assert diff.removed_nodes == []
        assert diff.added_edges == []

    def test_added_node(self) -> None:
        old = ParseResult(nodes=[_mod("a.nix")], edges=[])
        new = ParseResult(nodes=[_mod("a.nix"), _mod("b.nix")], edges=[])
        diff = diff_graph(old, new)
        assert len(diff.added_nodes) == 1
        assert diff.added_nodes[0].id == "nix:b.nix"
        assert diff.removed_nodes == []
        assert diff.has_changes

    def test_removed_node(self) -> None:
        old = ParseResult(nodes=[_mod("a.nix"), _mod("b.nix")], edges=[])
        new = ParseResult(nodes=[_mod("a.nix")], edges=[])
        diff = diff_graph(old, new)
        assert len(diff.removed_nodes) == 1
        assert diff.removed_nodes[0].id == "nix:b.nix"

    def test_changed_node(self) -> None:
        old = ParseResult(nodes=[_opt("foo", "old")], edges=[])
        new = ParseResult(nodes=[_opt("foo", "new")], edges=[])
        diff = diff_graph(old, new)
        assert len(diff.changed_nodes) == 1
        assert diff.changed_nodes[0].id == "nix_option:foo"
        assert diff.added_nodes == []
        assert diff.removed_nodes == []

    def test_added_edge(self) -> None:
        old = ParseResult(nodes=[_mod("a.nix")], edges=[])
        new = ParseResult(nodes=[_mod("a.nix")], edges=[_imports("a.nix", "b.nix")])
        diff = diff_graph(old, new)
        assert len(diff.added_edges) == 1
        assert diff.added_edges[0].target == "nix:b.nix"

    def test_removed_edge(self) -> None:
        old = ParseResult(nodes=[_mod("a.nix")], edges=[_imports("a.nix", "b.nix")])
        new = ParseResult(nodes=[_mod("a.nix")], edges=[])
        diff = diff_graph(old, new)
        assert len(diff.removed_edges) == 1
        assert diff.removed_edges[0].target == "nix:b.nix"

    def test_metadata_change_is_edge_removed_added(self) -> None:
        old = ParseResult(nodes=[_mod("a.nix")], edges=[_sets("a.nix", "x", cond=False)])
        new = ParseResult(nodes=[_mod("a.nix")], edges=[_sets("a.nix", "x", cond=True)])
        diff = diff_graph(old, new)
        # metadata differs -> should be reported as removed+added, not changed
        assert len(diff.added_edges) == 1
        assert len(diff.removed_edges) == 1
        assert diff.added_edges[0].metadata["conditional"] is True
        assert diff.removed_edges[0].metadata["conditional"] is False

    def test_line_ignored(self) -> None:
        # line numbers should be ignored for diff
        old = ParseResult(
            nodes=[_mod("a.nix")],
            edges=[RawEdge(source="nix:a.nix", target="nix:b.nix", type=EdgeType.imports, metadata={"line": 1})],
        )
        new = ParseResult(
            nodes=[_mod("a.nix")],
            edges=[RawEdge(source="nix:a.nix", target="nix:b.nix", type=EdgeType.imports, metadata={"line": 99})],
        )
        diff = diff_graph(old, new)
        assert not diff.has_changes
