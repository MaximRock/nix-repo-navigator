"""Unit tests for the six-tool MCP surface (stage 2)."""

from __future__ import annotations

import pytest

from repo_navigator.graph.builder import GraphBuilder
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.graph.queries import QueryEngine
from repo_navigator.mcp_server import ToolError, create_mcp_server
from repo_navigator.models.edges import EdgeType, RawEdge
from repo_navigator.models.nodes import NodeType, RawNode
from repo_navigator.models.queries import ParseResult


def _mod(path: str) -> RawNode:
    return RawNode(id=f"nix:{path}", type=NodeType.nix_module, name=path, path=path)


def _setup_engine() -> QueryEngine:
    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    builder = GraphBuilder(db, g)
    builder.build_file(
        "a.nix",
        ParseResult(
            nodes=[
                _mod("a.nix"),
                RawNode(
                    id="nix_option:services.bar.enable",
                    type=NodeType.nix_option,
                    name="services.bar.enable",
                    metadata={"synthetic": True},
                ),
            ],
            edges=[
                RawEdge(source="nix:a.nix", target="nix:b.nix", type=EdgeType.imports),
                RawEdge(
                    source="nix:a.nix",
                    target="nix_option:services.bar.enable",
                    type=EdgeType.sets,
                ),
            ],
        ),
    )
    builder.build_file("b.nix", ParseResult(nodes=[_mod("b.nix")], edges=[]))
    builder.build_file(
        "b.nix",
        ParseResult(
            nodes=[
                _mod("b.nix"),
                RawNode(
                    id="nix_option:services.foo.enable",
                    type=NodeType.nix_option,
                    name="services.foo.enable",
                    metadata={"description": "foo"},
                ),
            ],
            edges=[
                RawEdge(
                    source="nix:b.nix",
                    target="nix_option:services.foo.enable",
                    type=EdgeType.declares,
                )
            ],
        ),
    )
    return QueryEngine(db, g)


@pytest.mark.asyncio
async def test_mcp_server_has_exactly_six_short_tools() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert names == {"find", "module", "impact", "option", "neighbors", "status"}


@pytest.mark.asyncio
async def test_mcp_find_is_compact_and_has_provenance() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    result = await server.call_tool(
        "find",
        {"query": "services.bar.enable", "fuzzy": True, "limit": 5},
    )
    assert not result.is_error
    nodes = result.structured_content["result"]
    assert nodes
    node = next(n for n in nodes if n["id"] == "nix_option:services.bar.enable")
    assert node["metadata"]["defined_in"] == ["a.nix"]
    assert "content_hash" not in node
    assert "updated_at" not in node


@pytest.mark.asyncio
async def test_mcp_module_is_compact() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    result = await server.call_tool("module", {"path": "a.nix"})
    assert not result.is_error
    content = result.structured_content
    assert content["path"] == "a.nix"
    assert content["imports"] == ["b.nix"]
    assert content["options_set"] == ["services.bar.enable"]
    assert "outgoing_edges" not in content
    assert "incoming_edges" not in content


@pytest.mark.asyncio
async def test_mcp_impact_accepts_path_and_is_static() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    result = await server.call_tool("impact", {"target": "a.nix"})
    assert not result.is_error
    content = result.structured_content
    assert content["target"] == "nix:a.nix"
    assert content["basis"] == "static"
    assert content["options_set"] == ["services.bar.enable"]
    assert "evidence" not in content


@pytest.mark.asyncio
async def test_mcp_impact_accepts_legacy_node_id_alias() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    result = await server.call_tool("impact", {"node_id": "nix:b.nix"})
    assert not result.is_error
    assert result.structured_content["target"] == "nix:b.nix"


@pytest.mark.asyncio
async def test_mcp_option_accepts_name_and_alias() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    result = await server.call_tool("option", {"name": "services.foo.enable"})
    assert not result.is_error
    assert result.structured_content["option_path"] == "services.foo.enable"
    result2 = await server.call_tool("option", {"option_path": "services.foo.enable"})
    assert not result2.is_error
    assert result2.structured_content["option_path"] == "services.foo.enable"


@pytest.mark.asyncio
async def test_mcp_neighbors_is_compact() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    result = await server.call_tool("neighbors", {"node_id": "nix:a.nix"})
    assert not result.is_error
    content = result.structured_content
    assert content["node"]["id"] == "nix:a.nix"
    assert content["neighbors"]
    neighbor = content["neighbors"][0]
    assert set(neighbor) == {"edge", "node", "dangling"}
    assert "content_hash" not in neighbor["node"]


@pytest.mark.asyncio
async def test_mcp_status() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    result = await server.call_tool("status", {})
    assert not result.is_error
    assert "total_nodes" in result.structured_content


@pytest.mark.asyncio
async def test_mcp_errors_are_tool_errors() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    with pytest.raises(ToolError):
        await server.call_tool("option", {})
    with pytest.raises(ToolError):
        await server.call_tool("impact", {"target": "missing.nix"})
