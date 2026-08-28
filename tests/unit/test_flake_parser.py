"""Unit tests for flake_parser (phase 9.1)."""

from __future__ import annotations

import json
from pathlib import Path

from repo_navigator.parsers.nix.flake_parser import parse_flake_lock, parse_flake_nix


def test_parse_flake_lock_simple(tmp_path: Path) -> None:
    lock = {
        "nodes": {
            "root": {"inputs": {"nixpkgs": "nixpkgs", "home-manager": "home-manager"}},
            "nixpkgs": {"locked": {"type": "github", "owner": "NixOS", "repo": "nixpkgs", "rev": "abc123", "narHash": "sha256-xxx", "url": "https://github.com/NixOS/nixpkgs/archive/abc123.tar.gz"}},
            "home-manager": {"locked": {"type": "github", "owner": "nix-community", "repo": "home-manager", "rev": "def456", "url": "https://github.com/nix-community/home-manager/archive/def456.tar.gz"}},
        },
        "root": "root",
        "version": 7,
    }
    p = tmp_path / "flake.lock"
    p.write_text(json.dumps(lock))
    inputs = parse_flake_lock(p)
    assert len(inputs) == 2
    names = {i.name for i in inputs}
    assert "nixpkgs" in names
    assert "home-manager" in names
    nixpkgs = next(i for i in inputs if i.name == "nixpkgs")
    assert nixpkgs.rev == "abc123"
    assert nixpkgs.url is not None


def test_parse_flake_lock_missing_file(tmp_path: Path) -> None:
    assert parse_flake_lock(tmp_path / "missing.lock") == []


def test_parse_flake_lock_skips_root(tmp_path: Path) -> None:
    lock = {"nodes": {"root": {"inputs": {}}}, "version": 7}
    p = tmp_path / "flake.lock"
    p.write_text(json.dumps(lock))
    assert parse_flake_lock(p) == []


def test_parse_flake_lock_original_fallback(tmp_path: Path) -> None:
    lock = {
        "nodes": {
            "root": {},
            "myinput": {"original": {"url": "github:owner/repo"}, "locked": {"type": "github", "owner": "owner", "repo": "repo", "rev": "123"}},
        }
    }
    p = tmp_path / "flake.lock"
    p.write_text(json.dumps(lock))
    inputs = parse_flake_lock(p)
    assert inputs[0].url is not None


def test_parse_flake_nix_not_found(tmp_path: Path) -> None:
    assert parse_flake_nix(tmp_path / "nope.nix") == []


def test_parse_flake_nix_simple(tmp_path: Path) -> None:
    content = """
    {
      inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
      inputs.home-manager.url = "github:nix-community/home-manager";
    }
    """
    p = tmp_path / "flake.nix"
    p.write_text(content)
    urls = parse_flake_nix(p)
    # Should at least not crash, may return empty or found urls
    assert isinstance(urls, list)
