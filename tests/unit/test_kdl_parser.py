"""Tests for KDL plugin (phase 10.3)."""

from __future__ import annotations

from pathlib import Path

from repo_navigator.parsers.plugins.kdl import KDLParser


def test_kdl_parser_basic() -> None:
    parser = KDLParser()
    content = '''
    bind "mod+Return" spawn "kitty"
    bind "mod+q" spawn "qutebrowser"
    rule "window" {
        spawn "xterm"
    }
    '''
    result = parser.parse(Path("config.kdl"), content)
    # Should have module + 2 binds + 3 spawns + 1 rule
    ids = {n.id for n in result.nodes}
    assert "kdl:config.kdl" in ids
    assert any("kdl_bind" in i for i in ids)
    assert any("kdl_spawn" in i for i in ids)
    assert any("kdl_rule" in i for i in ids)
    # Check edges
    assert len(result.edges) >= 3


def test_kdl_parser_registry() -> None:
    from repo_navigator.parsers.registry import get_parser_for_file, get_parser_for_language

    parser = get_parser_for_language("kdl")
    assert parser is not None
    assert parser.language == "kdl"
    assert get_parser_for_file("foo.kdl") is not None
    assert get_parser_for_file("foo.nix") is None or get_parser_for_file("foo.nix").language == "nix"


def test_kdl_should_parse_with_config(tmp_path: Path) -> None:
    from repo_navigator.config import Config
    from repo_navigator.parsers.registry import should_parse_file

    # File in .config should be parsed only if plugin enabled
    p = tmp_path / ".config" / "kdl" / "config.kdl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('bind "a" spawn "b"')
    # Without plugin enabled, should be False (Nix-first + plugins empty)
    cfg_no = Config(root=tmp_path, plugins=[])
    assert should_parse_file(p, config=cfg_no) is False
    # With plugin enabled, should be True
    cfg_yes = Config(root=tmp_path, plugins=["kdl"])
    assert should_parse_file(p, config=cfg_yes) is True
    # File not in .config and not referenced via graph, should be False even with plugin?
    # But our should_parse checks .config OR graph edge, so plain kdl in root with plugin enabled but not in .config and no graph edge -> False
    plain = tmp_path / "plain.kdl"
    plain.write_text('bind "a" spawn "b"')
    assert should_parse_file(plain, config=cfg_yes) is False
