"""Unit tests for hash_engine (phase 5.1)."""

from __future__ import annotations

from pathlib import Path

from repo_navigator.indexer.hash_engine import ast_hash, content_hash, merkle_hash
from repo_navigator.models.edges import EdgeType, RawEdge
from repo_navigator.models.nodes import NodeType, RawNode
from repo_navigator.models.queries import ParseResult
from repo_navigator.parsers.nix_parser import NixParser


def _mod(path: str) -> RawNode:
    return RawNode(id=f"nix:{path}", type=NodeType.nix_module, name=path, path=path)


# ---------------------------------------------------------------- content_hash


class TestContentHash:
    def test_deterministic(self) -> None:
        assert content_hash("hello") == content_hash("hello")
        assert content_hash(b"hello") == content_hash(b"hello")

    def test_str_and_bytes_same(self) -> None:
        # Same bytes should give same hash whether passed as str or bytes
        assert content_hash("hello") == content_hash(b"hello")

    def test_different_content_different_hash(self) -> None:
        assert content_hash("hello") != content_hash("world")
        assert content_hash("a") != content_hash("b")

    def test_empty(self) -> None:
        h = content_hash("")
        assert isinstance(h, str) and len(h) > 0
        assert h == content_hash(b"")


# ---------------------------------------------------------------- ast_hash


class TestAstHash:
    def test_deterministic(self) -> None:
        pr = ParseResult(nodes=[_mod("a.nix")], edges=[])
        assert ast_hash(pr) == ast_hash(pr)

    def test_order_independent(self) -> None:
        pr1 = ParseResult(nodes=[_mod("a.nix"), _mod("b.nix")], edges=[])
        pr2 = ParseResult(nodes=[_mod("b.nix"), _mod("a.nix")], edges=[])
        assert ast_hash(pr1) == ast_hash(pr2)

    def test_different_nodes_different_hash(self) -> None:
        pr1 = ParseResult(nodes=[_mod("a.nix")], edges=[])
        pr2 = ParseResult(nodes=[_mod("b.nix")], edges=[])
        assert ast_hash(pr1) != ast_hash(pr2)

    def test_formatting_same_ast_same_hash(self) -> None:
        # Same semantic content with different whitespace/comments -> same ParseResult
        parser = NixParser()
        pr1 = parser.parse(Path("a.nix"), "{ a = 1; }")
        pr2 = parser.parse(Path("a.nix"), "{\n  a=1;\n # comment\n}")
        # Both should produce same nodes/edges structure (single module, no edges for simple set)
        # For this content, the module has no imports/options, so nodes are just module
        # The ast_hash should be equal because the semantic ParseResult is same
        # (Note: if parser includes no extra nodes for simple attr, they will match)
        assert ast_hash(pr1) == ast_hash(pr2)

    def test_added_option_changes_hash(self) -> None:
        parser = NixParser()
        pr1 = parser.parse(Path("a.nix"), "{ options.foo = lib.mkOption {}; }")
        pr2 = parser.parse(Path("a.nix"), "{ options.foo = lib.mkOption {}; options.bar = lib.mkOption {}; }")
        assert ast_hash(pr1) != ast_hash(pr2)

    def test_empty_parse_result(self) -> None:
        pr = ParseResult(nodes=[], edges=[])
        h = ast_hash(pr)
        assert isinstance(h, str) and len(h) > 0


# ---------------------------------------------------------------- merkle_hash


class TestMerkleHash:
    def test_deterministic(self) -> None:
        assert merkle_hash("abc", ["x", "y"]) == merkle_hash("abc", ["x", "y"])

    def test_order_independent(self) -> None:
        assert merkle_hash("abc", ["y", "x"]) == merkle_hash("abc", ["x", "y"])

    def test_different_file_hash_different(self) -> None:
        assert merkle_hash("abc", ["x"]) != merkle_hash("def", ["x"])

    def test_different_deps_different(self) -> None:
        assert merkle_hash("abc", ["x"]) != merkle_hash("abc", ["y"])

    def test_empty_deps(self) -> None:
        h = merkle_hash("abc", [])
        assert isinstance(h, str) and len(h) == 64  # sha256 hex

    def test_known_sha256(self) -> None:
        # merkle_hash uses sha256 of file_ast_hash + sorted deps
        import hashlib

        fh = "filehash"
        deps = ["b", "a"]
        expected = hashlib.sha256((fh + "ab").encode()).hexdigest()
        assert merkle_hash(fh, deps) == expected
