"""Unit tests for cascade engine (phase 5.2)."""

from __future__ import annotations

from pathlib import Path

from repo_navigator.config import Config
from repo_navigator.graph.builder import GraphBuilder
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.indexer.cascade import cascade_dirty
from repo_navigator.indexer.scan import index_repo


def _setup_chain(tmp_path: Path) -> tuple[Database, NxGraph]:
    # a imports b, b imports c - use UpdateEngine to create file_state
    from repo_navigator.graph.builder import GraphBuilder
    from repo_navigator.indexer.update_engine import UpdateEngine

    (tmp_path / "a.nix").write_text('{ imports = [ ./b.nix ]; }')
    (tmp_path / "b.nix").write_text('{ imports = [ ./c.nix ]; }')
    (tmp_path / "c.nix").write_text('{ config.x = 1; }')
    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    engine = UpdateEngine(db, g, root=tmp_path)
    for p in ["c.nix", "b.nix", "a.nix"]:
        engine.process_file(p)
        st = db.get_file_state(p)
        assert st is not None
        st.dirty = False
        db.upsert_file_state(st)
    return db, g


def test_cascade_direct_importer(tmp_path: Path) -> None:
    db, g = _setup_chain(tmp_path)
    # Change c.nix should dirty b and a (transitive)
    affected = cascade_dirty(db, g, "c.nix")
    assert "b.nix" in affected
    assert "a.nix" in affected
    # Order: b before a (closest first)
    assert affected.index("b.nix") < affected.index("a.nix")
    # Check dirty flags
    assert db.get_file_state("b.nix").dirty is True
    assert db.get_file_state("a.nix").dirty is True
    # c itself not in affected (changed file)
    assert "c.nix" not in affected


def test_cascade_no_importers(tmp_path: Path) -> None:
    db, g = _setup_chain(tmp_path)
    # a has no importers
    affected = cascade_dirty(db, g, "a.nix")
    assert affected == []
    # No dirty flags set beyond maybe none
    assert db.get_file_state("a.nix").dirty is False


def test_cascade_invalidate_option_values(tmp_path: Path) -> None:
    from repo_navigator.indexer.update_engine import UpdateEngine

    (tmp_path / "a.nix").write_text('{ config.foo = 1; }')
    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    engine = UpdateEngine(db, g, root=tmp_path)
    engine.process_file("a.nix")
    # Insert an option value that mentions "a"
    from repo_navigator.models.option_value import OptionValue, ValueStatus

    db.upsert_option_value(
        OptionValue(key="foo", expr="config.services.a.foo", value_json={"v": 1}, status=ValueStatus.ok)
    )
    # Change a.nix -> should invalidate
    affected = cascade_dirty(db, g, "a.nix")
    # Even though a has no importers, cascade should still invalidate? 
    # Our cascade invalidates for changed_path + affected, but if affected empty,
    # changed_path is still included in invalidate call (via cascade).
    # Check that option value is now stale
    ov = db.get_option_value("foo")
    # Since we called cascade on a.nix, it will invalidate option values mentioning "a"
    # The file_state for a.nix is "a.nix", stem "a" matches expr "config.services.a.foo"
    assert ov is not None
    assert ov.status.value == "stale"


def test_cascade_merkle_recomputed(tmp_path: Path) -> None:
    db, g = _setup_chain(tmp_path)
    old_merkle_b = db.get_file_state("b.nix").merkle_hash
    old_merkle_a = db.get_file_state("a.nix").merkle_hash
    # Modify c.nix content to change its ast_hash (and merkle) - use structural change
    from repo_navigator.indexer.hash_engine import content_hash, ast_hash, merkle_hash
    from repo_navigator.parsers.nix_parser import NixParser
    from pathlib import Path as P

    parser = NixParser()
    new_content = "{ config.y = 1; }"
    pr = parser.parse(P("c.nix"), new_content)
    new_ast = ast_hash(pr)
    # Build new merkle for c (no deps)
    new_merkle_c = merkle_hash(new_ast, [])
    # Update c's state
    from repo_navigator.models.file_state import FileState
    from datetime import UTC, datetime

    st_c = db.get_file_state("c.nix")
    assert st_c is not None
    db.upsert_file_state(
        FileState(
            path="c.nix",
            lang="nix",
            content_hash=content_hash(new_content),
            ast_hash=new_ast,
            merkle_hash=new_merkle_c,
            dirty=False,
            last_parsed=datetime.now(UTC),
        )
    )
    # Now cascade from c should recompute b and a merkle
    cascade_dirty(db, g, "c.nix")
    new_merkle_b = db.get_file_state("b.nix").merkle_hash
    new_merkle_a = db.get_file_state("a.nix").merkle_hash
    assert new_merkle_b != old_merkle_b
    assert new_merkle_a != old_merkle_a
