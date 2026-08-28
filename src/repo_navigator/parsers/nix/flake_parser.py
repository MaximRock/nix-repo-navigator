"""Flake parsers: flake.lock (JSON) and flake.nix (AST)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FlakeInput:
    name: str
    url: str | None
    rev: str | None
    nar_hash: str | None = None
    type: str | None = None


def parse_flake_lock(path: str | Path) -> list[FlakeInput]:
    """Parse ``flake.lock`` and return a list of :class:`FlakeInput`.

    Skips the ``root`` node (it is the flake itself, not an input).
    For each remaining node, extracts ``locked.url``/``rev``/``narHash``
    and ``original`` URL as fallback.  Missing fields become ``None``.
    """
    p = Path(path)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []

    nodes: dict[str, Any] = data.get("nodes", {})
    if not isinstance(nodes, dict):
        return []

    result: list[FlakeInput] = []
    for name, node in nodes.items():
        if name == "root":
            continue
        if not isinstance(node, dict):
            continue
        locked: dict[str, Any] = node.get("locked", {}) if isinstance(node.get("locked"), dict) else {}
        original: dict[str, Any] = node.get("original", {}) if isinstance(node.get("original"), dict) else {}

        url = locked.get("url")
        if url is None:
            url = original.get("url")
        # For github inputs, url is often like "github:nix-community/home-manager"
        # locked may have type/owner/repo
        if url is None and locked.get("type"):
            # Construct a synthetic url from type+owner+repo
            t = locked.get("type")
            owner = locked.get("owner")
            repo = locked.get("repo")
            if owner and repo:
                url = f"{t}:{owner}/{repo}"
            else:
                url = locked.get("url") or original.get("url")

        rev = locked.get("rev")
        nar_hash = locked.get("narHash")
        type_ = locked.get("type") or original.get("type")

        result.append(FlakeInput(name=name, url=url, rev=rev, nar_hash=nar_hash, type=type_))

    # Sort for determinism
    result.sort(key=lambda x: x.name)
    return result


def parse_flake_nix(path: str | Path) -> list[str]:
    """Extract ``inputs.<name>.url`` strings from ``flake.nix`` via AST.

    Returns a list of ``(name, url)``-like strings for simple cases.
    For MVP we only handle ``inputs.<name>.url = "...";`` and
    ``inputs.<name> = { url = "..."; }`` via the existing Nix parser.
    Falls back to empty list on parse failure.
    """
    p = Path(path)
    if not p.is_file():
        return []
    try:
        from repo_navigator.parsers.nix.parser import parse

        source = p.read_text(encoding="utf-8")
        tree = parse(source)
    except Exception:
        return []

    # Very small AST walk: look for top-level AttrSet that contains `inputs`
    urls: list[str] = []

    def _walk_expr(expr: Any) -> None:
        # Use duck typing for AttrSet/AttrDef
        if hasattr(expr, "attrs"):
            for attr in getattr(expr, "attrs", []):
                name = getattr(attr, "name", None)
                value = getattr(attr, "value", None)
                if name == "inputs" and value is not None and hasattr(value, "attrs"):
                    # inputs = { foo.url = "..."; bar = { url = "..."; }; }
                    for inp in getattr(value, "attrs", []):
                        inp_name = getattr(inp, "name", None)
                        inp_val = getattr(inp, "value", None)
                        if inp_val is None:
                            continue
                        # Case: inputs.foo.url = "..."
                        # Our parser folds dotted paths? For now handle two levels
                        # If inp_name contains ".", it was already expanded? Let's handle
                        # For simplicity, look for string literals inside
                        url = _extract_url(inp_val)
                        if url:
                            urls.append(f"{inp_name}:{url}")
                        # If value is AttrSet, look deeper
                        if hasattr(inp_val, "attrs"):
                            for sub in getattr(inp_val, "attrs", []):
                                sub_name = getattr(sub, "name", None)
                                if sub_name == "url":
                                    sub_url = _extract_url(getattr(sub, "value", None))
                                    if sub_url:
                                        urls.append(f"{inp_name}:{sub_url}")
                # Recurse
                if value is not None:
                    _walk_expr(value)
        # Also handle LetIn etc. - recurse into body if present
        for field in ("body", "value", "expr"):
            child = getattr(expr, field, None)
            if child is not None and child is not expr:
                _walk_expr(child)

    def _extract_url(node: Any) -> str | None:
        if node is None:
            return None
        # Literal string
        if hasattr(node, "value") and hasattr(node, "value_type"):
            vt = getattr(node, "value_type", None)
            if vt == "string":
                return str(getattr(node, "value", ""))
        # Interpolation or other
        return None

    try:
        _walk_expr(tree)
    except Exception:
        return []

    return sorted(set(urls))
