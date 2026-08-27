"""Unit tests for parser registry (phase 4.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_navigator.config import Config
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.models.edges import Edge, EdgeType
from repo_navigator.models.nodes import Node, NodeType
from repo_navigator.models.queries import ParseResult
from repo_navigator.parsers.base import BaseParser
from repo_navigator.parsers.registry import (
    LanguageConfig,
    clear_registry,
    get_all_parsers,
    get_parser_for_file,
    get_parser_for_language,
    register,
    register_language,
    safe_parse,
    should_parse_file,
)


# ---------------------------------------------------------------- helpers


class DummyTier1Parser(BaseParser):
    language = "dummy1"
    extensions = [".dummy1"]
    tier = 1

    def parse(self, path: Path, content: str) -> ParseResult:
        return ParseResult(nodes=[], edges=[])


class ExplodingParser(BaseParser):
    language = "exploding"
    extensions = [".boom"]
    tier = 1

    def parse(self, path: Path, content: str) -> ParseResult:
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Save/restore global registry around each test."""
    from repo_navigator.parsers import registry as reg

    saved = dict(reg._registry)
    yield
    reg._registry.clear()
    reg._registry.update(saved)


# ---------------------------------------------------------------- registration


class TestRegistration:
    def test_nix_parser_registered_by_default(self) -> None:
        assert get_parser_for_language("nix") is not None
        assert get_parser_for_file("foo.nix") is not None

    def test_register_and_resolve_by_extension(self) -> None:
        p = DummyTier1Parser()
        register(p)
        assert get_parser_for_file("a.dummy1") is p
        assert get_parser_for_file(Path("b.dummy1")) is p

    def test_unknown_extension_returns_none(self) -> None:
        assert get_parser_for_file("foo.unknown_xyz") is None

    def test_get_all_parsers_contains_nix(self) -> None:
        langs = {p.language for p in get_all_parsers()}
        assert "nix" in langs

    def test_register_language_decorator(self) -> None:
        cfg = LanguageConfig(name="decorated", extensions=[".dec"], tier=2)

        @register_language(cfg)
        class DecoratedParser(BaseParser):
            language = "decorated"
            extensions = [".dec"]
            tier = 2

            def parse(self, path: Path, content: str) -> ParseResult:
                return ParseResult(nodes=[], edges=[])

        assert get_parser_for_language("decorated") is not None
        assert get_parser_for_file("x.dec") is not None
        assert get_parser_for_language("decorated").tier == 2  # type: ignore[union-attr]

    def test_clear_registry(self) -> None:
        clear_registry()
        assert get_all_parsers() == []
        assert get_parser_for_file("a.nix") is None


# ---------------------------------------------------------------- should_parse_file


class TestShouldParseFile:
    def test_tier0_always_true(self) -> None:
        assert should_parse_file("anything.nix") is True
        assert should_parse_file(Path("a/b/c.nix")) is True

    def test_tier0_disabled_returns_false(self) -> None:
        nix = get_parser_for_language("nix")
        assert nix is not None
        orig = nix.enabled
        nix.enabled = False  # type: ignore[attr-defined]
        try:
            assert should_parse_file("a.nix") is False
        finally:
            nix.enabled = orig  # type: ignore[attr-defined]

    def test_tier1_without_conditions_returns_false(self) -> None:
        register(DummyTier1Parser())
        cfg = Config(root=Path("/tmp"), plugins=["dummy1"])
        assert should_parse_file("foo.dummy1", config=cfg) is False
        assert should_parse_file("foo.dummy1", graph=NxGraph(), config=cfg) is False

    def test_tier1_dotconfig_returns_true(self) -> None:
        register(DummyTier1Parser())
        cfg = Config(root=Path("/tmp"), plugins=["dummy1"])
        assert should_parse_file(".config/foo.dummy1", config=cfg) is True
        assert should_parse_file(Path("a/.config/b.dummy1"), config=cfg) is True

    def test_tier1_graph_reference_returns_true(self) -> None:
        register(DummyTier1Parser())
        cfg = Config(root=Path("/tmp"), plugins=["dummy1"])
        g = NxGraph()
        # Module that configures file:scripts/foo.dummy1
        mod = Node(id="nix:modules/a.nix", type=NodeType.nix_module, name="a.nix", path="modules/a.nix")
        fnode = Node(id="file:scripts/foo.dummy1", type=NodeType.file, name="foo.dummy1", path="scripts/foo.dummy1")
        edge = Edge(
            id="nix:modules/a.nix->configures->file:scripts/foo.dummy1",
            source=mod.id,
            target=fnode.id,
            type=EdgeType.configures,
        )
        g.rebuild(nodes=[mod, fnode], edges=[edge])
        assert should_parse_file("scripts/foo.dummy1", graph=g, config=cfg) is True

    def test_tier1_disabled_plugin_returns_false_even_with_dotconfig(self) -> None:
        p = DummyTier1Parser()
        p.enabled = False  # type: ignore[attr-defined]
        register(p)
        cfg = Config(root=Path("/tmp"), plugins=["dummy1"])
        assert should_parse_file(".config/foo.dummy1", config=cfg) is False

    def test_tier1_not_in_plugins_returns_false(self) -> None:
        register(DummyTier1Parser())
        cfg = Config(root=Path("/tmp"), plugins=[])  # nix only
        assert should_parse_file(".config/foo.dummy1", config=cfg) is False
        cfg2 = Config(root=Path("/tmp"), plugins=["other"])
        assert should_parse_file(".config/foo.dummy1", config=cfg2) is False

    def test_tier1_no_config_dotconfig_still_true(self) -> None:
        register(DummyTier1Parser())
        # No Config provided: Nix-first .config still qualifies (enabled check passes)
        assert should_parse_file(".config/foo.dummy1") is True

    def test_unknown_extension_returns_false(self) -> None:
        assert should_parse_file("foo.xyz_unknown") is False


# ---------------------------------------------------------------- safe_parse


class TestSafeParse:
    def test_safe_parse_isolates_exception(self) -> None:
        register(ExplodingParser())
        result = safe_parse(Path("a.boom"), "content")
        # Should not raise, should return file node fallback
        assert len(result.nodes) == 1
        assert result.nodes[0].type == NodeType.file
        assert result.nodes[0].id == "file:a.boom"
        assert result.edges == []

    def test_safe_parse_unknown_extension_returns_empty(self) -> None:
        result = safe_parse(Path("a.unknown_xyz"), "content")
        assert result.nodes == []
        assert result.edges == []

    def test_safe_parse_success(self) -> None:
        register(DummyTier1Parser())
        result = safe_parse(Path("a.dummy1"), "content")
        # Dummy returns empty ParseResult
        assert result.nodes == []
