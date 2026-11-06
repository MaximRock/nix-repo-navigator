"""E2E MCP session test for the six-tool surface."""

from __future__ import annotations

import pytest

from repo_navigator.graph.builder import GraphBuilder
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.graph.queries import QueryEngine
from repo_navigator.mcp_server import create_mcp_server
from repo_navigator.models.edges import EdgeType, RawEdge
from repo_navigator.models.nodes import Node, NodeType, RawNode
from repo_navigator.models.queries import ParseResult


def _mod(path: str) -> RawNode:
    return RawNode(id=f"nix:{path}", type=NodeType.nix_module, name=path, path=path)


def _setup_repo() -> tuple[QueryEngine, Database, NxGraph]:
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
                    id="nix_option:services.foo.enable",
                    type=NodeType.nix_option,
                    name="services.foo.enable",
                    metadata={"description": "foo enable", "opt_type": "bool"},
                ),
            ],
            edges=[
                RawEdge(
                    source="nix:a.nix",
                    target="nix_option:services.foo.enable",
                    type=EdgeType.declares,
                )
            ],
        ),
    )
    builder.build_file(
        "b.nix",
        ParseResult(
            nodes=[_mod("b.nix")],
            edges=[
                RawEdge(
                    source="nix:b.nix",
                    target="nix_option:services.foo.enable",
                    type=EdgeType.sets,
                    metadata={"conditional": False},
                ),
                RawEdge(
                    source="nix:b.nix",
                    target="file:.config/foo",
                    type=EdgeType.configures,
                ),
            ],
        ),
    )
    builder.build_file(
        "default.nix",
        ParseResult(
            nodes=[_mod("default.nix")],
            edges=[
                RawEdge(
                    source="nix:default.nix", target="nix:a.nix", type=EdgeType.imports
                ),
                RawEdge(
                    source="nix:default.nix", target="nix:b.nix", type=EdgeType.imports
                ),
            ],
        ),
    )
    fnode = Node(
        id="file:.config/foo", type=NodeType.file, name=".config/foo", path=None
    )
    db.upsert_node(fnode)
    g.apply_delta(added_nodes=[fnode])

    return QueryEngine(db, g), db, g


@pytest.mark.asyncio
async def test_mcp_tools_list_schema() -> None:
    engine, _, _ = _setup_repo()
    server = create_mcp_server(engine=engine)
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert names == {"find", "module", "impact", "option", "neighbors", "status"}
    for tool in tools:
        schema = getattr(tool, "input_schema", None) or getattr(
            tool, "inputSchema", None
        )
        assert schema is not None
        desc = getattr(tool, "description", None)
        assert desc is not None and len(desc) > 0


@pytest.mark.asyncio
async def test_mcp_agent_scenario() -> None:
    """Agent workflow: find -> option -> neighbors -> module -> impact."""
    engine, _, _ = _setup_repo()
    server = create_mcp_server(engine=engine)

    res = await server.call_tool(
        "find", {"query": "services.foo.enable", "fuzzy": True}
    )
    assert not res.is_error
    found = res.structured_content["result"]
    assert any(node["id"] == "nix_option:services.foo.enable" for node in found)

    res = await server.call_tool("option", {"name": "services.foo.enable"})
    assert not res.is_error
    data = res.structured_content
    assert data["option_path"] == "services.foo.enable"
    assert data["declared_in"] == "a.nix"
    assert "generation_id" in data

    res = await server.call_tool("neighbors", {"node_id": "nix:a.nix"})
    assert not res.is_error
    data = res.structured_content
    assert data["node"]["id"] == "nix:a.nix"
    assert "generation_id" in data

    res = await server.call_tool("module", {"path": "b.nix"})
    assert not res.is_error
    data = res.structured_content
    assert data["options_set"] == ["services.foo.enable"]
    assert "generation_id" in data

    res = await server.call_tool("impact", {"target": "b.nix"})
    assert not res.is_error
    data = res.structured_content
    assert data["basis"] == "static"
    assert data["options_set"] == ["services.foo.enable"]
    assert "generation_id" in data


@pytest.mark.asyncio
async def test_mcp_generation_id_present_everywhere() -> None:
    engine, _, _ = _setup_repo()
    server = create_mcp_server(engine=engine)

    checks = [
        ("neighbors", {"node_id": "nix:a.nix"}),
        ("module", {"path": "a.nix"}),
        ("option", {"name": "services.foo.enable"}),
        ("impact", {"target": "nix:b.nix"}),
        ("status", {}),
    ]
    for tool_name, args in checks:
        res = await server.call_tool(tool_name, args)
        assert not res.is_error, f"{tool_name} failed: {res}"
        data = res.structured_content
        if "result" in data and isinstance(data["result"], dict):
            data = data["result"]
        assert "generation_id" in data, f"{tool_name} missing generation_id"
