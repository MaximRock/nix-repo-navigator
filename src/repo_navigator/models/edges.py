"""Graph edge models: ``Edge`` (persisted) and ``RawEdge`` (parser output)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EdgeType(StrEnum):
    """Every edge kind the graph can hold (spec v3 §5.2)."""

    # Universal NixOS/HM core
    imports = "imports"
    declares = "declares"
    sets = "sets"
    specialises = "specialises"
    passes_args = "passes_args"
    configures = "configures"
    generates = "generates"
    uses_package = "uses_package"

    # Plugin languages
    python_imports = "python_imports"
    calls = "calls"
    binds_key = "binds_key"
    spawns = "spawns"
    requires = "requires"
    sources = "sources"
    references = "references"


class Edge(BaseModel):
    """A persisted graph edge (SQLite ``edges`` table)."""

    id: str
    source: str
    target: str
    type: EdgeType
    metadata: dict[str, Any] = Field(default_factory=dict)  # line, priority, conditional…
    weight: float = 1.0


class RawEdge(BaseModel):
    """Intermediate edge emitted by a parser; the builder assigns the final id."""

    source: str
    target: str
    type: EdgeType
    metadata: dict[str, Any] = Field(default_factory=dict)
    weight: float = 1.0
