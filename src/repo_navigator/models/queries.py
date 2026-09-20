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
    # True when the edge target/source has no node in the DB (dangling
    # edge): *node* is then a synthetic, read-only placeholder.
    dangling: bool = False


class Observation(BaseModel):
    """``observe`` — direct neighborhood of a node."""

    node: Node
    neighbors: list[Neighbor] = Field(default_factory=list)
    generation_id: int


class Subgraph(BaseModel):
    """A set of nodes + edges (result of ``hop`` / ``blast_radius``)."""

    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    # Ids of synthetic nodes standing in for dangling edge targets.
    dangling: list[str] = Field(default_factory=list)
    generation_id: int


class PathStep(BaseModel):
    """One step of a shortest path; ``edge_in`` is None for the source."""

    node: Node
    edge_in: Edge | None = None
    depth: int


class DependencyEntry(BaseModel):
    """A node reachable in a closure query, with the evidence chain from target."""

    node: Node
    steps: list[PathStep] = Field(default_factory=list)  # target -> ... -> this node
    depth: int = 0


class DependenciesReport(BaseModel):
    """``dependencies`` — what *target* (transitively) depends on."""

    target: str
    max_depth: int
    depends_on: list[DependencyEntry] = Field(default_factory=list)
    generation_id: int


class DependentsReport(BaseModel):
    """``dependents`` — what (transitively) depends on *target*."""

    target: str
    max_depth: int
    dependents: list[DependencyEntry] = Field(default_factory=list)
    generation_id: int


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


class ImpactEvidence(BaseModel):
    """One affected node plus the edge chain explaining *why* it is impacted."""

    node_id: str
    steps: list[PathStep] = Field(default_factory=list)  # target -> ... -> node_id


class ImpactReport(BaseModel):
    """``impact_analysis`` — what a change to ``target`` would affect."""

    target: str
    affected_modules: list[str] = Field(default_factory=list)
    affected_options: list[str] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    evidence: list[ImpactEvidence] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.low
    generation_id: int


class ModuleSummary(BaseModel):
    """``summarize_module`` — in/out edges and key symbols of a module."""

    path: str
    incoming_edges: list[Edge] = Field(default_factory=list)
    outgoing_edges: list[Edge] = Field(default_factory=list)
    key_symbols: list[str] = Field(default_factory=list)
    # Edge endpoints with no node in the DB (dangling edges), if any.
    dangling_targets: list[str] = Field(default_factory=list)
    generation_id: int


class StatusResponse(BaseModel):
    """``status`` / ``refresh`` — graph size, mode and sync progress."""

    mode: SyncMode
    total_nodes: int
    total_edges: int
    uptime: float
    sync_progress: tuple[int, int] | None = None  # (processed, total) during bulk sync
    queries_served: int = 0
    tokens_estimated_saved: int = 0
    generation_id: int


class BenefitReport(BaseModel):
    """``repo_navigator_report`` — how many queries were served and tokens saved.

    Savings estimate: bytes of source files the agent did NOT have to re-read
    (deduplicated across the session), divided by 4.
    """

    queries_served: int = 0
    queries_by_tool: dict[str, int] = Field(default_factory=dict)
    files_served: int = 0  # unique source files served through queries this session
    bytes_not_reread: int = 0
    tokens_estimated_saved: int = 0  # bytes_not_reread // 4
    uptime_seconds: float = 0.0
    generation_id: int


class ParseResult(BaseModel):
    """Output of any parser: intermediate nodes/edges, not yet in the DB."""

    nodes: list[RawNode] = Field(default_factory=list)
    edges: list[RawEdge] = Field(default_factory=list)
