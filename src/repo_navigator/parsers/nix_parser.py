"""Nix parser: orchestrates lexer → parser → extract → module_parser."""

from __future__ import annotations

import logging
from pathlib import Path

from repo_navigator.models.nodes import NodeType, RawNode
from repo_navigator.models.queries import ParseResult
from repo_navigator.parsers.base import BaseParser
from repo_navigator.parsers.nix.ast_extract import extract
from repo_navigator.parsers.nix.module_parser import parse_module
from repo_navigator.parsers.nix.nix_instantiate import parse_via_nix_instantiate
from repo_navigator.parsers.registry import register

log = logging.getLogger(__name__)


class NixParser(BaseParser):
    """Nix-language parser (tier 0)."""

    language = "nix"
    extensions = [".nix"]

    def parse(self, path: Path, content: str) -> ParseResult:
        try:
            return self._parse_inner(path, content)
        except Exception:
            log.exception("NixParser failed for %s", path)
            return self._fallback(path)

    def _parse_inner(self, path: Path, content: str) -> ParseResult:
        from repo_navigator.parsers.nix.parser import parse, UnresolvedExpr

        tree = parse(content)

        # Fallback: if >50% of nodes are UnresolvedExpr, try nix-instantiate
        unresolved_count = _count_unresolved(tree)
        total_count = _count_nodes(tree)
        if total_count > 0 and unresolved_count / total_count > 0.5:
            log.info(
                "High unresolved ratio (%d/%d) for %s, trying nix-instantiate",
                unresolved_count,
                total_count,
                path,
            )
            ni_result = parse_via_nix_instantiate(path)
            if ni_result is not None:
                log.info("nix-instantiate fallback succeeded for %s", path)
                # TODO: convert ni_result (nix-instantiate JSON AST) to our ExtractedNix
                # For now, the fallback JSON format is not yet supported.

        extracted = extract(tree)
        return parse_module(path, extracted)

    def _fallback(self, path: Path) -> ParseResult:
        path_str = str(path)
        module_id = f"nix:{path_str}"
        return ParseResult(
            nodes=[
                RawNode(
                    id=module_id,
                    type=NodeType.nix_module,
                    name=path_str,
                    path=path_str,
                    lang="nix",
                )
            ],
            edges=[],
        )


def _count_unresolved(expr: object) -> int:
    """Count UnresolvedExpr nodes in the AST."""
    if expr.__class__.__name__ == "UnresolvedExpr":
        return 1
    count = 0
    for attr in ("body", "cond", "then_", "else_", "assertion", "expr", "left", "right", "func", "arg"):
        child = getattr(expr, attr, None)
        if child is not None:
            count += _count_unresolved(child)
    if hasattr(expr, "attrs"):
        for a in expr.attrs:
            if hasattr(a, "value") and a.value is not None:
                count += _count_unresolved(a.value)
    if hasattr(expr, "items"):
        for item in expr.items:
            count += _count_unresolved(item)
    if hasattr(expr, "bindings"):
        for b in expr.bindings:
            if hasattr(b, "value") and b.value is not None:
                count += _count_unresolved(b.value)
    if hasattr(expr, "parts"):
        for p in expr.parts:
            if isinstance(p, object) and not isinstance(p, str):
                count += _count_unresolved(p)
    return count


def _count_nodes(expr: object) -> int:
    """Count all nodes in the AST."""
    count = 1
    for attr in ("body", "cond", "then_", "else_", "assertion", "expr", "left", "right", "func", "arg"):
        child = getattr(expr, attr, None)
        if child is not None:
            count += _count_nodes(child)
    if hasattr(expr, "attrs"):
        for a in expr.attrs:
            if hasattr(a, "value") and a.value is not None:
                count += _count_nodes(a.value)
    if hasattr(expr, "items"):
        for item in expr.items:
            count += _count_nodes(item)
    if hasattr(expr, "bindings"):
        for b in expr.bindings:
            if hasattr(b, "value") and b.value is not None:
                count += _count_nodes(b.value)
    if hasattr(expr, "parts"):
        for p in expr.parts:
            if isinstance(p, object) and not isinstance(p, str):
                count += _count_nodes(p)
    return count


register(NixParser())
