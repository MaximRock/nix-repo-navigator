"""Unit tests for update_engine (phase 5.2)."""

from __future__ import annotations

from pathlib import Path

from repo_navigator.graph.builder import GraphBuilder
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.indexer.update_engine import UpdateEngine


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


class TestUpdateEngine:
    def test_initial_index(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.nix", "{ config.x = 1; }")
        db = Database(":memory:")
        db.init_db()
        g = NxGraph()
        engine = UpdateEngine(db, g, root=tmp_path)
        result = engine.process_file("a.nix")
        assert result["changed"] is True
        assert db.get_file_state("a.nix") is not None
        assert db.count_nodes() >= 1
        assert db.get_generation_id() == 1

    def test_unchanged_content_skips(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.nix", "{ config.x = 1; }")
        db = Database(":memory:")
        db.init_db()
        g = NxGraph()
        engine = UpdateEngine(db, g, root=tmp_path)
        engine.process_file("a.nix")
        gen1 = db.get_generation_id()
        # Second call with same content -> unchanged
        result = engine.process_file("a.nix")
        assert result["changed"] is False
        assert result["reason"] == "unchanged_content"
        assert db.get_generation_id() == gen1

    def test_formatting_only(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.nix", "{ config.x=1; }")
        db = Database(":memory:")
        db.init_db()
        g = NxGraph()
        engine = UpdateEngine(db, g, root=tmp_path)
        engine.process_file("a.nix")
        gen1 = db.get_generation_id()
        # Change formatting only (whitespace + comment)
        _write(tmp_path / "a.nix", "{\n  config.x = 1; # comment\n}")
        result = engine.process_file("a.nix")
        assert result["changed"] is False
        assert result["reason"] == "formatting_only"
        assert db.get_generation_id() == gen1
        # file_state content_hash should be updated, but ast_hash same
        st = db.get_file_state("a.nix")
        assert st is not None
        # content changed, so content_hash different from first, but ast same
        assert st.ast_hash is not None

    def test_ast_changed_rebuilds(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.nix", "{ config.x = 1; }")
        db = Database(":memory:")
        db.init_db()
        g = NxGraph()
        engine = UpdateEngine(db, g, root=tmp_path)
        engine.process_file("a.nix")
        gen1 = db.get_generation_id()
        # Change AST: add new option
        _write(tmp_path / "a.nix", "{ config.x = 1; config.y = 2; }")
        result = engine.process_file("a.nix")
        assert result["changed"] is True
        assert result["reason"] == "ast_changed"
        assert db.get_generation_id() == gen1 + 1
        # New edge should exist
        assert any(e.target == "nix_option:y" for e in db.get_all_edges())

    def test_deleted_file(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.nix", "{ imports = [ ./b.nix ]; }")
        _write(tmp_path / "b.nix", "{ config.x = 1; }")
        db = Database(":memory:")
        db.init_db()
        g = NxGraph()
        engine = UpdateEngine(db, g, root=tmp_path)
        engine.process_file("a.nix")
        engine.process_file("b.nix")
        assert db.get_node("nix:b.nix") is not None
        # Delete b.nix
        (tmp_path / "b.nix").unlink()
        result = engine.process_file("b.nix")
        # Should be treated as deleted
        assert result["reason"] == "deleted"
        assert db.get_node("nix:b.nix") is None
        assert db.get_file_state("b.nix") is None

    def test_cascade_on_change(self, tmp_path: Path) -> None:
        # a imports b
        _write(tmp_path / "a.nix", "{ imports = [ ./b.nix ]; }")
        _write(tmp_path / "b.nix", "{ config.x = 1; }")
        db = Database(":memory:")
        db.init_db()
        g = NxGraph()
        engine = UpdateEngine(db, g, root=tmp_path)
        engine.process_file("b.nix")
        engine.process_file("a.nix")
        # Clear dirty flags
        for p in ["a.nix", "b.nix"]:
            st = db.get_file_state(p)
            assert st is not None
            st.dirty = False
            db.upsert_file_state(st)
        # Change b.nix AST (structural: new attr)
        _write(tmp_path / "b.nix", "{ config.y = 1; }")
        result = engine.process_file("b.nix")
        assert result["changed"] is True
        # a should be marked dirty via cascade
        st_a = db.get_file_state("a.nix")
        assert st_a is not None
        assert st_a.dirty is True
        assert "a.nix" in result["affected"]

    def test_process_file_absolute_path(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.nix", "{ config.x = 1; }")
        db = Database(":memory:")
        db.init_db()
        g = NxGraph()
        engine = UpdateEngine(db, g, root=tmp_path)
        abs_path = (tmp_path / "a.nix").resolve()
        result = engine.process_file(abs_path)
        assert result["changed"] is True
        # Stored path should be relative "a.nix"
        assert db.get_file_state("a.nix") is not None
