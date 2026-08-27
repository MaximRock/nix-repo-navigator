"""End-to-end builder flow (phase 4.3).

Tests the integration of parser -> module_parser -> GraphBuilder ->
Database/NxGraph via both direct builder API and the bulk indexer
(`index_repo`).  Uses a miniature home-manager repo.
"""

from __future__ import annotations

from pathlib import Path

from repo_navigator.config import Config
from repo_navigator.graph.builder import GraphBuilder
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.indexer.scan import index_repo
from repo_navigator.models.edges import EdgeType


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_e2e_imports_declares_sets(tmp_path: Path) -> None:
    # Repo layout:
    #   default.nix -> imports a.nix, b.nix
    #   a.nix -> declares option services.foo.enable
    #   b.nix -> sets option, home.file
    _write(
        tmp_path / "a.nix",
        """
        { lib, ... }:
        {
          options.services.foo.enable = lib.mkOption {
            type = lib.types.bool;
            default = false;
            description = "foo";
          };
        }
        """,
    )
    _write(
        tmp_path / "b.nix",
        """
        {
          config.services.foo.enable = true;
          home.file.".config/foo".source = ./foo.conf;
          home.packages = [ pkgs.ripgrep ];
        }
        """,
    )
    _write(
        tmp_path / "default.nix",
        """
        {
          imports = [ ./a.nix ./b.nix ];
        }
        """,
    )
    _write(tmp_path / "foo.conf", "hello")

    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    cfg = Config(root=tmp_path)
    stats = index_repo(tmp_path, db, g, config=cfg)

    assert stats["files"] == 3
    # Nodes: 3 modules + 1 option + 1 file + 1 package
    assert db.get_node("nix:a.nix") is not None
    assert db.get_node("nix:b.nix") is not None
    assert db.get_node("nix:default.nix") is not None
    assert db.get_node("nix_option:services.foo.enable") is not None
    assert db.get_node("file:.config/foo") is not None
    assert db.get_node("package:pkgs.ripgrep") is not None

    # Edges: default imports a,b ; a declares option ; b sets option ; b configures file ; b uses_package
    edges = db.get_all_edges()
    types = {(e.source, e.target, e.type) for e in edges}
    assert ("nix:default.nix", "nix:a.nix", EdgeType.imports) in types
    assert ("nix:default.nix", "nix:b.nix", EdgeType.imports) in types
    assert ("nix:a.nix", "nix_option:services.foo.enable", EdgeType.declares) in types
    assert ("nix:b.nix", "nix_option:services.foo.enable", EdgeType.sets) in types
    assert ("nix:b.nix", "file:.config/foo", EdgeType.configures) in types
    assert ("nix:b.nix", "package:pkgs.ripgrep", EdgeType.uses_package) in types

    # NxGraph mirrors DB
    assert g.number_of_nodes() == db.count_nodes()
    assert g.number_of_edges() == db.count_edges()
    assert g.has_node("nix:default.nix")
    # BFS from default should reach a, b, option, file, package
    reachable = {n.id for n in g.bfs("nix:default.nix", depth=3, width=10)}
    assert "nix:a.nix" in reachable
    assert "nix:b.nix" in reachable


def test_e2e_dynamic_update(tmp_path: Path) -> None:
    _write(tmp_path / "a.nix", "{ imports = [ ./b.nix ]; }")
    _write(tmp_path / "b.nix", "{ config.x = 1; }")

    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    cfg = Config(root=tmp_path)
    index_repo(tmp_path, db, g, config=cfg)
    # a imports b + b sets x => 2 edges
    assert db.count_edges() == 2
    assert any(e.target == "nix:b.nix" for e in db.get_all_edges())

    # Modify a.nix to import c.nix instead
    _write(tmp_path / "a.nix", "{ imports = [ ./c.nix ]; }")
    _write(tmp_path / "c.nix", "{ config.y = 2; }")
    # Keep b.nix on disk, but a no longer imports it
    stats = index_repo(tmp_path, db, g, config=cfg)
    assert stats["files"] == 3
    edges = db.get_all_edges()
    assert any(e.target == "nix:c.nix" for e in edges)
    assert not any(e.source == "nix:a.nix" and e.target == "nix:b.nix" for e in edges)
    # b.nix module still exists (file still on disk)
    assert db.get_node("nix:b.nix") is not None
    # BFS from a should now reach c, not b
    reachable = {n.id for n in g.bfs("nix:a.nix", depth=2, width=10)}
    assert "nix:c.nix" in reachable
    assert "nix:b.nix" not in reachable

    # Delete b.nix from filesystem and re-index -> its node should be purged
    (tmp_path / "b.nix").unlink()
    index_repo(tmp_path, db, g, config=cfg)
    assert db.get_node("nix:b.nix") is None
    # c still there
    assert db.get_node("nix:c.nix") is not None


def test_e2e_nested_import_normalisation(tmp_path: Path) -> None:
    # Ensure imports are normalised relative to file's directory
    _write(tmp_path / "modules" / "a.nix", "{ imports = [ ./b.nix ]; }")
    _write(tmp_path / "modules" / "b.nix", "{ config.foo = 1; }")
    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    cfg = Config(root=tmp_path)
    index_repo(tmp_path, db, g, config=cfg)
    # a imports b -> edge target should be nix:modules/b.nix (normalised)
    assert db.get_node("nix:modules/a.nix") is not None
    assert db.get_node("nix:modules/b.nix") is not None
    edges = db.get_all_edges()
    assert any(e.source == "nix:modules/a.nix" and e.target == "nix:modules/b.nix" for e in edges)
    # No placeholder ./b.nix
    assert db.get_node("nix:modules/./b.nix") is None
    assert db.get_node("nix:./b.nix") is None


def test_e2e_conditional_and_priority(tmp_path: Path) -> None:
    _write(tmp_path / "a.nix", "{ config = lib.mkIf true { x = 1; }; }")
    _write(tmp_path / "b.nix", "{ config.foo = lib.mkForce 42; }")
    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    cfg = Config(root=tmp_path)
    index_repo(tmp_path, db, g, config=cfg)
    edges = db.get_all_edges()
    # mkIf -> conditional True
    assert any(e.metadata.get("conditional") is True for e in edges)
    # mkForce -> priority 'force' (normalised)
    assert any(e.metadata.get("priority") == "force" for e in edges)


def test_e2e_builder_direct_api(tmp_path: Path) -> None:
    # Direct builder usage without index_repo, to test build_file + build_all
    from repo_navigator.parsers.nix_parser import NixParser

    parser = NixParser()
    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    builder = GraphBuilder(db, g)

    content = "{ imports = [ ./b.nix ]; options.test.enable = lib.mkOption {}; }"
    pr = parser.parse(Path("a.nix"), content)
    builder.build_file("a.nix", pr)
    assert db.count_nodes() >= 2
    # Rebuild same file with different content -> old option edge removed
    content2 = "{ config.test.enable = true; }"
    pr2 = parser.parse(Path("a.nix"), content2)
    builder.build_file("a.nix", pr2)
    # Option declare edge should be gone, sets edge present
    edges = db.get_all_edges()
    assert not any(e.type == EdgeType.declares for e in edges)
    assert any(e.type == EdgeType.sets for e in edges)
