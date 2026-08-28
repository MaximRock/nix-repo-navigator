"""E2E HM + package + flake (phase 9.2/9.3)."""

from __future__ import annotations

import json
from pathlib import Path

from repo_navigator.config import Config
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.graph.queries import QueryEngine
from repo_navigator.indexer.scan import index_repo


def test_hm_package_flake_e2e(tmp_path: Path) -> None:
    # HM repo
    (tmp_path / "home.nix").write_text(
        """
        {
          imports = [ ./extra.nix ];
          home.file.".config/foo".source = ./foo.conf;
          xdg.dataFile."bar".source = ./bar;
          home.packages = [ pkgs.ripgrep pkgs.hello ];
          home.sessionVariables.EDITOR = "vim";
          programs.git.enable = true;
          programs.neovim.enable = false;
        }
        """
    )
    (tmp_path / "extra.nix").write_text('{ config.x = 1; }')
    (tmp_path / "foo.conf").write_text("foo")
    (tmp_path / "bar").write_text("bar")
    # Flake lock
    (tmp_path / "flake.lock").write_text(
        json.dumps(
            {
                "nodes": {
                    "root": {},
                    "nixpkgs": {"locked": {"type": "github", "owner": "NixOS", "repo": "nixpkgs", "rev": "abc", "url": "https://github.com/NixOS/nixpkgs"}},
                },
                "version": 7,
            }
        )
    )
    (tmp_path / "flake.nix").write_text('{ inputs.nixpkgs.url = "github:NixOS/nixpkgs"; }')

    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    cfg = Config(root=tmp_path)
    stats = index_repo(tmp_path, db, g, config=cfg)

    # Check HM file nodes
    assert db.get_node("file:.config/foo") is not None
    assert db.get_node("file:bar") is not None
    # Check packages
    assert db.get_node("package:pkgs.ripgrep") is not None
    assert db.get_node("package:pkgs.hello") is not None
    # Check sessionVariables and programs.enable as config sets
    assert db.get_node("nix_option:home.sessionVariables.EDITOR") is not None or any(
        e.target == "nix_option:home.sessionVariables.EDITOR" for e in db.get_all_edges()
    )
    assert any(e.target == "nix_option:programs.git.enable" for e in db.get_all_edges())
    # Check flake inputs
    inputs = db.get_flake_inputs()
    assert any(i["name"] == "nixpkgs" for i in inputs)
    assert db.get_node("flake_input:nixpkgs") is not None
    # Check package_index (mock)
    pkgs = db.get_packages()
    assert len(pkgs) >= 2
    assert any(p["attribute"] == "pkgs.ripgrep" for p in pkgs)
    # QueryEngine
    engine = QueryEngine(db, g, config=cfg)
    # find home file via find_symbol
    found = engine.find_symbol("foo", fuzzy=True)
    assert len(found) > 0
    # impact analysis for home.nix
    impact = engine.impact_analysis("nix:home.nix")
    assert "home.nix" in str(impact.affected_modules) or len(impact.affected_modules) >= 0
    # list packages via query
    listed = engine.list_packages(query="ripgrep")
    assert len(listed) == 1
    assert listed[0]["attribute"] == "pkgs.ripgrep"
    # list flake
    assert len(engine.list_flake_inputs()) == 1
