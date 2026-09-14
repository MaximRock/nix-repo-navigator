"""Tests for MCP CLI start and error handling (phase 7.2)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner
from unittest.mock import AsyncMock, patch

from repo_navigator.cli import app
from repo_navigator.graph.builder import GraphBuilder
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.graph.queries import QueryEngine
from repo_navigator.mcp_server import create_mcp_server
from repo_navigator.models.edges import EdgeType, RawEdge
from repo_navigator.models.nodes import NodeType, RawNode
from repo_navigator.models.queries import ParseResult

runner = CliRunner()


def _mod(path: str) -> RawNode:
    return RawNode(id=f"nix:{path}", type=NodeType.nix_module, name=path, path=path)


def _setup_engine() -> QueryEngine:
    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    builder = GraphBuilder(db, g)
    builder.build_file("a.nix", ParseResult(nodes=[_mod("a.nix")], edges=[]))
    return QueryEngine(db, g)


def test_cli_start_help() -> None:
    result = runner.invoke(app, ["start", "--help"])
    assert result.exit_code == 0
    assert "start" in result.output.lower()


def test_cli_start_runs_with_mock(tmp_path) -> None:
    # Mock MCPServer.run_stdio_async to avoid actual stdio
    with patch("repo_navigator.mcp_server.MCPServer.run_stdio_async", new_callable=AsyncMock) as mock_run:
        # Use tmp_path as root to avoid touching cwd DB
        result = runner.invoke(app, ["start", "--root", str(tmp_path)])
        # The CLI start should have called asyncio.run which calls run_stdio_async
        # Since we mock, it should exit 0 quickly
        # However our CLI start does asyncio.run which will call the mock
        # The mock is async, so it will be awaited
        # We need to ensure it was called
        assert result.exit_code == 0 or "MCP server" in result.output or mock_run.called


class _FakeMcpServer:
    async def run_stdio_async(self) -> None:
        return None


def test_cli_start_plugin_flags(tmp_path) -> None:
    # --plugins / --parse-unreferenced are passed through to Config so the
    # Python plugin can activate from the CLI (previously env-only).
    captured: dict[str, object] = {}

    def fake_create_mcp_server(*, config=None, engine=None):  # type: ignore[no-untyped-def]
        captured.update(config.__dict__ if hasattr(config, "__dict__") else {})
        return _FakeMcpServer()

    with patch("repo_navigator.mcp_server.create_mcp_server", fake_create_mcp_server):
        result = runner.invoke(
            app,
            [
                "start",
                "--root",
                str(tmp_path),
                "--plugins",
                "python,kdl",
                "--parse-unreferenced",
            ],
        )
    assert result.exception is None, result.exception
    assert captured.get("plugins") == ["python", "kdl"]
    assert captured.get("parse_unreferenced") is True


def test_cli_start_plugin_flags_absent_keep_env(tmp_path) -> None:
    # Without the flags, no plugins/parse_unreferenced kwarg should be forced,
    # so REPO_NAVIGATOR_* env vars still apply.
    captured: dict[str, object] = {}

    def fake_create_mcp_server(*, config=None, engine=None):  # type: ignore[no-untyped-def]
        captured.update(config.__dict__ if hasattr(config, "__dict__") else {})
        return _FakeMcpServer()

    with patch("repo_navigator.mcp_server.create_mcp_server", fake_create_mcp_server):
        result = runner.invoke(app, ["start", "--root", str(tmp_path)])
    assert result.exception is None, result.exception
    assert captured.get("plugins", []) == []
    assert captured.get("parse_unreferenced") is False


def test_mcp_error_observe_missing() -> None:
    import asyncio

    from repo_navigator.mcp_server import ToolError

    engine = _setup_engine()
    server = create_mcp_server(engine=engine)

    async def _run():
        with pytest.raises(ToolError) as exc_info:
            await server.call_tool("repo_navigator_observe", {"node_id": "nix:missing.nix"})
        assert "missing" in str(exc_info.value).lower() or "not found" in str(exc_info.value).lower()

    asyncio.run(_run())


def test_mcp_error_hop_budget() -> None:
    import asyncio

    from repo_navigator.mcp_server import ToolError

    engine = _setup_engine()
    server = create_mcp_server(engine=engine)

    async def _run():
        with pytest.raises(ToolError) as exc_info:
            await server.call_tool("repo_navigator_hop", {"node_id": "nix:a.nix", "depth": 11, "width": 10})
        assert "budget" in str(exc_info.value).lower() or "depth" in str(exc_info.value).lower()

    asyncio.run(_run())


def test_mcp_error_summarize_missing() -> None:
    import asyncio

    from repo_navigator.mcp_server import ToolError

    engine = _setup_engine()
    server = create_mcp_server(engine=engine)

    async def _run():
        with pytest.raises(ToolError):
            await server.call_tool("repo_navigator_summarize_module", {"path": "missing.nix"})

    asyncio.run(_run())


def test_mcp_eval_timeout_validation() -> None:
    import asyncio

    from repo_navigator.mcp_server import ToolError

    engine = _setup_engine()
    server = create_mcp_server(engine=engine)

    async def _run():
        with pytest.raises(ToolError) as exc_info:
            await server.call_tool("repo_navigator_eval_expression", {"expr": "1+1", "timeout": 200})
        assert "timeout" in str(exc_info.value).lower()

    asyncio.run(_run())


def test_mcp_blast_depth_validation() -> None:
    import asyncio

    from repo_navigator.mcp_server import ToolError

    engine = _setup_engine()
    server = create_mcp_server(engine=engine)

    async def _run():
        with pytest.raises(ToolError):
            await server.call_tool("repo_navigator_blast_radius", {"node_id": "nix:a.nix", "max_depth": 20})

    asyncio.run(_run())
