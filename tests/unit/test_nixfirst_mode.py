"""Tests for Nix-first mode: parse_unreferenced flag, single-file explicit
requests, and update_engine relevance gate (Package D, part 1)."""

from __future__ import annotations

from pathlib import Path

from repo_navigator.config import Config
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.models.edges import Edge, EdgeType
from repo_navigator.models.nodes import Node, NodeType


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


class TestParseUnreferencedFlag:
    def test_flag_parses_unreferenced_file(self, tmp_path: Path) -> None:
        from repo_navigator.parsers.registry import should_parse_file

        p = tmp_path / "scripts" / "foo.py"
        _write(p, "def foo():\n    pass\n")
        # Without the flag: not in .config/, no graph edge -> False
        cfg_no = Config(root=tmp_path, plugins=["python"])
        g = NxGraph()
        assert should_parse_file(p, graph=g, config=cfg_no) is False
        # With parse_unreferenced=True -> True
        cfg_yes = Config(root=tmp_path, plugins=["python"], parse_unreferenced=True)
        assert should_parse_file(p, graph=g, config=cfg_yes) is True

    def test_flag_still_respects_plugin_enabled(self, tmp_path: Path) -> None:
        from repo_navigator.parsers.registry import should_parse_file

        p = tmp_path / "foo.py"
        _write(p, "x = 1\n")
        cfg = Config(root=tmp_path, plugins=[], parse_unreferenced=True)
        assert should_parse_file(p, graph=NxGraph(), config=cfg) is False


class TestSingleFileExplicit:
    def test_single_file_parses_without_reference(self, tmp_path: Path) -> None:
        from repo_navigator.indexer.scan import collect_files

        p = tmp_path / "scripts" / "tool.py"
        _write(p, "def run():\n    pass\n")
        cfg = Config(root=tmp_path, plugins=["python"])
        # Directory-scan would skip it (no .config/, no reference)…
        assert collect_files(tmp_path, config=cfg) == []
        # …but explicit single-file request parses it.
        assert collect_files(p, config=cfg) == [p]

    def test_single_file_respects_plugins(self, tmp_path: Path) -> None:
        from repo_navigator.indexer.scan import collect_files

        p = tmp_path / "scripts" / "tool.py"
        _write(p, "def run():\n    pass\n")
        cfg = Config(root=tmp_path, plugins=[])  # python not enabled
        assert collect_files(p, config=cfg) == []

    def test_single_file_unknown_extension_returns_empty(self, tmp_path: Path) -> None:
        from repo_navigator.indexer.scan import collect_files

        p = tmp_path / "data.unknown_xyz"
        _write(p, "x")
        assert collect_files(p, config=Config(root=tmp_path, plugins=["python"])) == []


class TestUpdateEngineRelevanceGate:
    def test_unreferenced_py_skipped(self, tmp_path: Path) -> None:
        from repo_navigator.graph.builder import GraphBuilder
        from repo_navigator.graph.db import Database
        from repo_navigator.indexer.update_engine import UpdateEngine

        p = tmp_path / "scripts" / "tool.py"
        _write(p, "def run():\n    pass\n")
        db = Database(":memory:")
        db.init_db()
        g = NxGraph()
        engine = UpdateEngine(db, g, builder=GraphBuilder(db, g), root=tmp_path)
        result = engine.process_file(p)
        assert result["changed"] is False
        assert result["reason"] == "not_relevant"
        assert db.get_file_state("scripts/tool.py") is None

    def test_referenced_py_parsed_and_dropped_on_lost_reference(
        self, tmp_path: Path
    ) -> None:
        from repo_navigator.graph.builder import GraphBuilder
        from repo_navigator.graph.db import Database
        from repo_navigator.indexer.update_engine import UpdateEngine

        p = tmp_path / "scripts" / "tool.py"
        _write(p, "def run():\n    pass\n")
        db = Database(":memory:")
        db.init_db()
        g = NxGraph()
        # Nix module configures the file -> relevant
        mod = Node(
            id="nix:modules/a.nix",
            type=NodeType.nix_module,
            name="a.nix",
            path="modules/a.nix",
        )
        fnode = Node(
            id="file:scripts/tool.py",
            type=NodeType.file,
            name="tool.py",
            path="scripts/tool.py",
        )
        edge = Edge(
            id="e1",
            source=mod.id,
            target=fnode.id,
            type=EdgeType.configures,
        )
        g.rebuild(nodes=[mod, fnode], edges=[edge])

        engine = UpdateEngine(db, g, builder=GraphBuilder(db, g), root=tmp_path)
        result = engine.process_file(p)
        assert result["changed"] is True
        assert db.get_file_state("scripts/tool.py") is not None

        # Reference disappears from the graph (edge + file node gone) ->
        # file becomes irrelevant and is dropped
        g.rebuild(nodes=[mod], edges=[])
        result2 = engine.process_file(p)
        assert result2["reason"] == "deleted"
        assert db.get_file_state("scripts/tool.py") is None
