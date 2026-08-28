"""MCP server for repo-navigator.

Exposes 11 tools, each directly mapped to a :class:`QueryEngine` method.
Transport is stdio (JSON-RPC).  Run with::

    python -m repo_navigator.mcp_server [--root PATH] [--db-path PATH]

or via the CLI::

    repo-navigator start [--root PATH] [--db-path PATH]

For tests the factory :func:`create_mcp_server` can be called with a
pre-built :class:`QueryEngine` so no filesystem access is needed.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from repo_navigator.config import Config
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.graph.queries import QueryEngine

# ------------------------------------------------------------------ factory


def create_mcp_server(
    config: Config | None = None,
    engine: QueryEngine | None = None,
) -> MCPServer:
    """Create and return a configured :class:`MCPServer` with 11 tools.

    If *engine* is provided it is used directly (useful for tests).
    Otherwise a new :class:`QueryEngine` is built from *config* (or a
    default :class:`Config` that reads ``REPO_NAVIGATOR_*`` env vars).
    """
    cfg = config or Config()
    if engine is None:
        db_path = cfg.resolved_db_path
        # Ensure parent exists for file-based DB (memory DB has no parent)
        if str(db_path) != ":memory:":
            try:
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        db = Database(str(db_path))
        db.init_db()
        g = NxGraph()
        nodes = db.get_all_nodes()
        edges = db.get_all_edges()
        if nodes or edges:
            g.rebuild(nodes=nodes, edges=edges)
        engine = QueryEngine(db, g, config=cfg)

    server = MCPServer("repo-navigator")

    # ---------------------------------------------------------------- tools

    @server.tool()
    def repo_navigator_observe(node_id: str, depth: int = 1) -> dict[str, Any]:
        """Observe direct neighbourhood of a node.

        Args:
            node_id: Node ID (e.g. ``nix:a.nix`` or ``nix_option:services.foo.enable``).
            depth: Neighbourhood depth (max 20, default 1).

        Returns:
            Observation dict with ``node``, ``neighbors`` and ``generation_id``.
        """
        try:
            result = engine.observe(node_id, depth=depth)
            return result.model_dump(mode="json")
        except (KeyError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool()
    def repo_navigator_hop(
        node_id: str,
        relation: str | None = None,
        depth: int = 1,
        width: int = 10,
    ) -> dict[str, Any]:
        """BFS hop with optional relation filter.

        Args:
            node_id: Start node ID.
            relation: Edge type to follow (e.g. ``imports``).  ``None`` follows all.
            depth: BFS depth (max 10, default 1).
            width: Max neighbours per level (budget ``width*depth <=100``).

        Returns:
            Subgraph dict with ``nodes``, ``edges`` and ``generation_id``.
        """
        try:
            result = engine.hop(node_id, relation=relation, depth=depth, width=width)
            return result.model_dump(mode="json")
        except (ValueError, KeyError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool()
    def repo_navigator_path(source: str, target: str) -> list[dict[str, Any]]:
        """Shortest path (Dijkstra) between two nodes.

        Args:
            source: Source node ID.
            target: Target node ID.

        Returns:
            List of PathStep dicts (empty if unreachable).
        """
        steps = engine.path(source, target)
        return [s.model_dump(mode="json") for s in steps]

    @server.tool()
    def repo_navigator_blast_radius(node_id: str, max_depth: int = 5) -> dict[str, Any]:
        """Reverse BFS: who depends on this node.

        Args:
            node_id: Node ID.
            max_depth: Max reverse depth (max 10, default 5).

        Returns:
            Subgraph dict of dependents.
        """
        try:
            result = engine.blast_radius(node_id, max_depth=max_depth)
            return result.model_dump(mode="json")
        except (ValueError, KeyError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool()
    def repo_navigator_find_symbol(
        query: str,
        lang: str | None = None,
        fuzzy: bool = False,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Full-text search for symbols.

        Args:
            query: Search query.
            lang: Language filter (e.g. ``nix``).
            fuzzy: If True use LIKE, else FTS5.
            limit: Max results (default 10).

        Returns:
            List of Node dicts.
        """
        nodes = engine.find_symbol(query, lang=lang, fuzzy=fuzzy, limit=limit)
        return [n.model_dump(mode="json") for n in nodes]

    @server.tool()
    def repo_navigator_summarize_module(path: str) -> dict[str, Any]:
        """Summarize a module.

        Args:
            path: Module path (e.g. ``a.nix``).

        Returns:
            ModuleSummary dict.
        """
        try:
            result = engine.summarize_module(path)
            return result.model_dump(mode="json")
        except (KeyError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool()
    def repo_navigator_introspect_option(
        option_path: str, include_value: bool = False
    ) -> dict[str, Any]:
        """Introspect a Nix option.

        Args:
            option_path: Dotted option path (e.g. ``services.foo.enable``).
            include_value: If True, also evaluate the option's current value.

        Returns:
            OptionInfo dict.
        """
        result = engine.introspect_option(option_path, include_value=include_value)
        return result.model_dump(mode="json")

    @server.tool()
    def repo_navigator_eval_expression(expr: str, timeout: int = 60) -> dict[str, Any]:
        """Evaluate a Nix expression (cached).

        Args:
            expr: Nix expression string.
            timeout: Timeout in seconds (max 120, default 60).

        Returns:
            EvalResult dict.
        """
        try:
            result = engine.eval_expression(expr, timeout=timeout)
            return result.model_dump(mode="json")
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool()
    def repo_navigator_impact_analysis(
        node_id: str, max_depth: int = 5
    ) -> dict[str, Any]:
        """Impact analysis for a node.

        Args:
            node_id: Node ID.
            max_depth: Max depth for blast radius (default 5).

        Returns:
            ImpactReport dict.
        """
        result = engine.impact_analysis(node_id, max_depth=max_depth)
        return result.model_dump(mode="json")

    @server.tool()
    def repo_navigator_status() -> dict[str, Any]:
        """Graph status.

        Returns:
            StatusResponse dict with ``mode``, ``total_nodes``, ``total_edges``,
            ``uptime``, ``sync_progress`` and ``generation_id``.
        """
        result = engine.status()
        return result.model_dump(mode="json")

    @server.tool()
    def repo_navigator_refresh() -> dict[str, Any]:
        """Full rescan of the repository.

        Returns:
            StatusResponse dict after refresh.
        """
        result = engine.refresh()
        return result.model_dump(mode="json")

    @server.tool()
    def repo_navigator_list_flake_inputs() -> list[dict[str, Any]]:
        """List flake inputs from flake.lock.

        Returns:
            List of dicts with ``name``, ``url`` and ``rev``.
        """
        return engine.list_flake_inputs()

    @server.tool()
    def repo_navigator_list_packages(
        query: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """List packages from package_index (mock).

        Args:
            query: Filter substring (attribute or name).
            limit: Max results (default 50).

        Returns:
            List of package dicts.
        """
        return engine.list_packages(query=query, limit=limit)

    @server.tool()
    def repo_navigator_get_package(attribute: str) -> dict[str, Any] | None:
        """Get a single package by attribute.

        Args:
            attribute: Package attribute (e.g. ``ripgrep`` or ``pkgs.ripgrep``).

        Returns:
            Package dict or None if not found.
        """
        result = engine.get_package(attribute)
        if result is None:
            raise ToolError(f"package not found: {attribute}")
        return result

    return server


# ------------------------------------------------------------------ CLI entry

def _parse_args() -> Config:
    parser = argparse.ArgumentParser(description="repo-navigator MCP server (stdio)")
    parser.add_argument("--root", type=Path, default=None, help="Repository root")
    parser.add_argument("--db-path", type=Path, default=None, help="SQLite DB path")
    args = parser.parse_args()
    cfg_kwargs: dict[str, Any] = {}
    if args.root is not None:
        cfg_kwargs["root"] = args.root
    if args.db_path is not None:
        cfg_kwargs["db_path"] = args.db_path
    return Config(**cfg_kwargs)


async def _run_stdio(config: Config) -> None:
    server = create_mcp_server(config=config)
    await server.run_stdio_async()


def main() -> None:
    config = _parse_args()
    asyncio.run(_run_stdio(config))


if __name__ == "__main__":
    main()
