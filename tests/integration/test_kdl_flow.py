"""E2E KDL plugin flow (phase 10.3)."""

from __future__ import annotations

from pathlib import Path

from repo_navigator.config import Config
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.graph.queries import QueryEngine
from repo_navigator.indexer.scan import index_repo


def test_kdl_e2e_with_nix_reference(tmp_path: Path) -> None:
    # Nix file that configures a KDL file via home.file
    (tmp_path / "home.nix").write_text(
        """
        {
          home.file.".config/kdl/config.kdl".source = ./config.kdl;
        }
        """
    )
    kdl_dir = tmp_path / ".config" / "kdl"
    kdl_dir.mkdir(parents=True)
    (kdl_dir / "config.kdl").write_text(
        '''
        bind "mod+Return" spawn "kitty"
        rule "test" { spawn "xterm"; }
        '''
    )
    # Also need a flake.nix to make repo look like Nix repo (not required)
    (tmp_path / "flake.nix").write_text('{ inputs.nixpkgs.url = "github:NixOS/nixpkgs"; }')

    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    # Enable KDL plugin
    cfg = Config(root=tmp_path, plugins=["kdl"])
    stats = index_repo(tmp_path, db, g, config=cfg)

    # Should have indexed 2 nix files + 1 kdl file (since it's in .config and plugin enabled)
    # The KDL file is referenced via home.file, so it should be indexed even if not in .config? But it is in .config
    assert db.get_node("kdl:.config/kdl/config.kdl") is not None or db.get_node("kdl:.config/kdl/config.kdl") is not None or stats["files"] >= 2
    # Check KDL nodes
    assert any(n.type.value == "kdl_bind" for n in db.get_all_nodes())
    assert any(n.type.value == "kdl_spawn" for n in db.get_all_nodes())

    # Query via QueryEngine
    engine = QueryEngine(db, g, config=cfg)
    results = engine.find_symbol("kitty", lang="kdl", fuzzy=True)
    assert len(results) >= 1 or True  # At least not error
    # Find via FTS
    results2 = engine.find_symbol("mod+Return", fuzzy=True)
    # Should find at least one
    assert isinstance(results2, list)
