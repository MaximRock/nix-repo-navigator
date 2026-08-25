"""Graph node models: ``Node`` (persisted) and ``RawNode`` (parser output)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class NodeType(StrEnum):
    """Every node kind the graph can hold (spec v3 §5.1)."""

    # Tier 0 — Nix core
    nix_module = "nix_module"
    nix_option = "nix_option"
    nix_function = "nix_function"
    flake_input = "flake_input"
    package_ref = "package_ref"

    # Tier 1–3 — plugin languages
    py_function = "py_function"
    py_class = "py_class"
    qtile_key = "qtile_key"
    qtile_hook = "qtile_hook"
    kdl_bind = "kdl_bind"
    kdl_rule = "kdl_rule"
    kdl_spawn = "kdl_spawn"
    sh_function = "sh_function"
    sh_command_call = "sh_command_call"
    lua_function = "lua_function"
    lua_require = "lua_require"
    vim_keymap = "vim_keymap"
    toml_section = "toml_section"
    toml_key = "toml_key"
    json_key = "json_key"

    # Fallback
    file = "file"
    heading = "heading"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Node(BaseModel):
    """A persisted graph node (single source of truth: SQLite ``nodes``)."""

    id: str  # ID scheme: "{lang}:{path}:{symbol}" or "{type}:{symbol}"
    type: NodeType
    name: str
    path: str | None = None
    lang: str = "nix"
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_hash: str | None = None
    ast_hash: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class RawNode(BaseModel):
    """Intermediate node emitted by a parser, before DB insertion.

    The builder turns these into :class:`Node` objects and assigns hashes.
    """

    id: str
    type: NodeType
    name: str
    path: str | None = None
    lang: str = "nix"
    metadata: dict[str, Any] = Field(default_factory=dict)
