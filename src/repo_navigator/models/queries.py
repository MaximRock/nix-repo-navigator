"""Response models returned by the Query Engine / MCP tools.

Every response carries ``generation_id`` so agents can invalidate their
client-side caches (spec v3 §7).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from repo_navigator.models.edges import Edge, RawEdge
from repo_navigator.models.nodes import Node, RawNode
from repo_navigator.models.option_value import ValueStatus


class RiskLevel(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class SyncMode(StrEnum):
    static = "static"
    hybrid = "hybrid"


class Neighbor(BaseModel):
    """An (edge, node) pair — one hop away from the observed node."""

    edge: Edge
    node: Node


class Observation(BaseModel):
    """``observe`` — direct neighborhood of a node."""

    node: Node
    neighbors: list[Neighbor] = Field(default_factory=list)
    generation_id: int


class Subgraph(BaseModel):
    """A set of nodes + edges (result of ``hop`` / ``blast_radius``)."""

    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    generation_id: int


class PathStep(BaseModel):
    """One step of a shortest path; ``edge_in`` is None for the source."""

    node: Node
    edge_in: Edge | None = None
    depth: int


class OptionInfo(BaseModel):
    """``introspect_option`` — static declaration + optional cached value."""

    option_path: str
    opt_type: str | None = None
    default: str | None = None
    example: str | None = None
    description: str | None = None
    declared_in: str | None = None
    defined_in: list[str] = Field(default_factory=list)
    conditional_sets: list[str] = Field(default_factory=list)
    value: Any | None = None
    value_status: str | None = None
    generation_id: int


class EvalResult(BaseModel):
    """``eval_expression`` — result of a lazy ``nix eval`` (possibly cached)."""

    expr: str
    value_json: Any | None = None
    status: ValueStatus = ValueStatus.unresolved
    error: str | None = None
    cached: bool = False
    generation_id: int


class ImpactReport(BaseModel):
    """``impact_analysis`` — what a change to ``target`` would affect."""

    target: str
    affected_modules: list[str] = Field(default_factory=list)
    affected_options: list[str] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.low
    generation_id: int


class ModuleSummary(BaseModel):
    """``summarize_module`` — in/out edges and key symbols of a module."""

    path: str
    incoming_edges: list[Edge] = Field(default_factory=list)
    outgoing_edges: list[Edge] = Field(default_factory=list)
    key_symbols: list[str] = Field(default_factory=list)
    generation_id: int


class StatusResponse(BaseModel):
    """``status`` / ``refresh`` — graph size, mode and sync progress."""

    mode: SyncMode
    total_nodes: int
    total_edges: int
    uptime: float
    sync_progress: tuple[int, int] | None = None  # (processed, total) during bulk sync
    generation_id: int


class ParseResult(BaseModel):
    """Output of any parser: intermediate nodes/edges, not yet in the DB."""

    nodes: list[RawNode] = Field(default_factory=list)
    edges: list[RawEdge] = Field(default_factory=list)
