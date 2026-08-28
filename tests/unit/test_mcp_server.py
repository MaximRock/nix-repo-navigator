"""Unit tests for MCP server (phase 7.1)."""

from __future__ import annotations

import pytest

from repo_navigator.graph.builder import GraphBuilder
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.graph.queries import QueryEngine
from repo_navigator.mcp_server import create_mcp_server
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
    builder.build_file("a.nix", ParseResult(nodes=[_mod("a.nix")], edges=[RawEdge(source="nix:a.nix", target="nix:b.nix", type=EdgeType.imports)]))
    builder.build_file("b.nix", ParseResult(nodes=[_mod("b.nix")], edges=[]))
    # Add option
    builder.build_file(
        "b.nix",
        ParseResult(
            nodes=[_mod("b.nix"), RawNode(id="nix_option:services.foo.enable", type=NodeType.nix_option, name="services.foo.enable", metadata={"description": "foo"})],
            edges=[RawEdge(source="nix:b.nix", target="nix_option:services.foo.enable", type=EdgeType.declares)],
        ),
    )
    return QueryEngine(db, g)


@pytest.mark.asyncio
async def test_mcp_server_has_11_tools() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    tools = await server.list_tools()
    names = {t.name for t in tools}
    expected = {
        "repo_navigator_observe",
        "repo_navigator_hop",
        "repo_navigator_path",
        "repo_navigator_blast_radius",
        "repo_navigator_find_symbol",
        "repo_navigator_summarize_module",
        "repo_navigator_introspect_option",
        "repo_navigator_eval_expression",
        "repo_navigator_impact_analysis",
        "repo_navigator_status",
        "repo_navigator_refresh",
        "repo_navigator_list_flake_inputs",
        "repo_navigator_list_packages",
        "repo_navigator_get_package",
    }
    assert names == expected


@pytest.mark.asyncio
async def test_mcp_observe() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    result = await server.call_tool("repo_navigator_observe", {"node_id": "nix:a.nix", "depth": 1})
    assert not result.is_error
    # structured_content should contain node
    assert result.structured_content is not None
    assert "node" in result.structured_content


@pytest.mark.asyncio
async def test_mcp_hop() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    result = await server.call_tool("repo_navigator_hop", {"node_id": "nix:a.nix", "depth": 1, "width": 10})
    assert not result.is_error
    assert "nodes" in result.structured_content


@pytest.mark.asyncio
async def test_mcp_path() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    result = await server.call_tool("repo_navigator_path", {"source": "nix:a.nix", "target": "nix:b.nix"})
    assert not result.is_error
    # Should return list
    assert isinstance(result.structured_content, dict)
    assert "result" in result.structured_content or isinstance(result.structured_content, list)


@pytest.mark.asyncio
async def test_mcp_blast_radius() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    result = await server.call_tool("repo_navigator_blast_radius", {"node_id": "nix:b.nix", "max_depth": 2})
    assert not result.is_error


@pytest.mark.asyncio
async def test_mcp_find_symbol() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    result = await server.call_tool("repo_navigator_find_symbol", {"query": "a.nix"})
    assert not result.is_error


@pytest.mark.asyncio
async def test_mcp_summarize_module() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    result = await server.call_tool("repo_navigator_summarize_module", {"path": "a.nix"})
    assert not result.is_error
    assert result.structured_content["path"] == "a.nix"


@pytest.mark.asyncio
async def test_mcp_introspect_option() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    result = await server.call_tool("repo_navigator_introspect_option", {"option_path": "services.foo.enable"})
    assert not result.is_error
    assert result.structured_content["option_path"] == "services.foo.enable"


@pytest.mark.asyncio
async def test_mcp_eval_expression_mocked() -> None:
    from unittest.mock import patch
    import json

    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    with patch("repo_navigator.nix.eval.subprocess.run") as mock_run, patch(
        "repo_navigator.nix.eval.shutil.which", return_value="/nix/bin/nix"
    ):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = json.dumps(123)
        mock_run.return_value.stderr = ""
        result = await server.call_tool("repo_navigator_eval_expression", {"expr": "1+1"})
        assert not result.is_error
        assert result.structured_content["value_json"] == 123


@pytest.mark.asyncio
async def test_mcp_impact_analysis() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    result = await server.call_tool("repo_navigator_impact_analysis", {"node_id": "nix:b.nix"})
    assert not result.is_error
    assert "affected_modules" in result.structured_content


@pytest.mark.asyncio
async def test_mcp_status_and_refresh() -> None:
    engine = _setup_engine()
    server = create_mcp_server(engine=engine)
    result = await server.call_tool("repo_navigator_status", {})
    assert not result.is_error
    assert "total_nodes" in result.structured_content
    result2 = await server.call_tool("repo_navigator_refresh", {})
    assert not result2.is_error
    assert "generation_id" in result2.structured_content
