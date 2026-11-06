"""MCP server for repo-navigator.

Exposes six graph tools for NixOS/home-manager work: ``find``, ``module``,
``impact``, ``option``, ``neighbors`` and ``status``. Generic where-is/code
search belongs to graft; this server owns graph context, option provenance and
change impact. Transport is stdio (JSON-RPC). Run with::

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
from typing import Any, Iterable, TypeAlias

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _MCPServer
    from mcp.server.mcpserver.exceptions import ToolError as ToolError
except ModuleNotFoundError:  # mcp 1.x fallback
    from mcp.server.fastmcp import FastMCP as _MCPServer
    from mcp.server.fastmcp.exceptions import ToolError as ToolError

MCPServer: TypeAlias = _MCPServer

from repo_navigator.config import Config  # noqa: E402
from repo_navigator.graph.db import Database  # noqa: E402
from repo_navigator.graph.nx_graph import NxGraph  # noqa: E402
from repo_navigator.graph.queries import QueryEngine  # noqa: E402
from repo_navigator.models.edges import Edge  # noqa: E402
from repo_navigator.models.nodes import Node  # noqa: E402
from repo_navigator.models.queries import ImpactReport, ModuleSummary  # noqa: E402

INSTRUCTIONS = (
    "NixOS/home-manager knowledge graph. Use graft for where-is and generic "
    "code search. Use this server for Nix graph context: find modules/options, "
    "module for key symbols and imports, option for declared_in/defined_in "
    "provenance, neighbors for direct edges, and impact before editing a shared "
    "module or option. Impact is static (basis=static); it cannot see dynamic "
    "imports or evaluation-time module composition. generation_id changes when "
    "the graph is re-indexed."
)

# ------------------------------------------------------------------ helpers


def _brief_node(node: Node) -> dict[str, Any]:
    """Compact node payload: no hashes or timestamps."""
    return {
        "id": node.id,
        "type": node.type.value,
        "name": node.name,
        "path": node.path,
        "lang": node.lang,
        "line": node.line,
        "snippet": node.snippet,
        "metadata": dict(node.metadata),
    }


def _brief_edge(edge: Edge) -> dict[str, Any]:
    """Compact edge payload: keep graph semantics, drop default weight."""
    payload: dict[str, Any] = {
        "id": edge.id,
        "source": edge.source,
        "target": edge.target,
        "type": edge.type.value,
        "metadata": dict(edge.metadata),
    }
    if edge.weight != 1.0:
        payload["weight"] = edge.weight
    return payload


def _module_digest(summary: ModuleSummary) -> dict[str, Any]:
    """Compact module view derived from full in/out edge lists."""
    imports = sorted(
        {
            e.target.removeprefix("nix:")
            for e in summary.outgoing_edges
            if e.type.value == "imports"
        }
    )
    imported_by = sorted(
        {
            e.source.removeprefix("nix:")
            for e in summary.incoming_edges
            if e.type.value == "imports"
        }
    )
    options_set = sorted(
        {
            e.target.removeprefix("nix_option:")
            for e in summary.outgoing_edges
            if e.type.value == "sets" and e.target.startswith("nix_option:")
        }
    )
    packages = sorted(
        {
            e.target.removeprefix("package:")
            for e in summary.outgoing_edges
            if e.type.value == "uses_package"
        }
    )
    configures = sorted(
        {
            e.target.removeprefix("file:")
            for e in summary.outgoing_edges
            if e.type.value in ("configures", "generates")
        }
    )
    return {
        "path": summary.path,
        "key_symbols": summary.key_symbols,
        "imports": imports,
        "imported_by": imported_by,
        "options_set": options_set,
        "packages": packages,
        "configures": configures,
        "dangling": summary.dangling_targets,
        "generation_id": summary.generation_id,
    }


def _impact_digest(report: ImpactReport) -> dict[str, Any]:
    """Compact impact view: no evidence chains or full node/edge objects."""
    return {
        "target": report.target,
        "affected_modules": report.affected_modules,
        "affected_files": report.affected_files,
        "affected_options": report.affected_options,
        "options_set": report.options_set,
        "risk_level": report.risk_level.value,
        "basis": report.basis,
        "generation_id": report.generation_id,
    }


def _observation_payload(observation: Any) -> dict[str, Any]:
    return {
        "node": _brief_node(observation.node),
        "neighbors": [
            {
                "edge": _brief_edge(neighbor.edge),
                "node": _brief_node(neighbor.node),
                "dangling": neighbor.dangling,
            }
            for neighbor in observation.neighbors
        ],
        "generation_id": observation.generation_id,
    }


def _option_ref(name: str | None, option_path: str | None) -> str:
    ref = name or option_path
    if not ref:
        raise ToolError("option name is required")
    return ref


def _impact_ref(target: str | None, node_id: str | None) -> str:
    ref = target or node_id
    if not ref:
        raise ToolError("target (node id or path) is required")
    return ref


def _find_nodes(nodes: Iterable[Node]) -> list[dict[str, Any]]:
    return [_brief_node(node) for node in nodes]


# ------------------------------------------------------------------ factory


def create_mcp_server(
    config: Config | None = None,
    engine: QueryEngine | None = None,
) -> MCPServer:
    """Create and return a configured :class:`MCPServer` with six tools.

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

    server = MCPServer("repo-navigator", instructions=INSTRUCTIONS)

    # ---------------------------------------------------------------- tools

    @server.tool()
    def find(
        query: str,
        lang: str | None = None,
        node_type: str | None = None,
        path_contains: str | None = None,
        id_prefix: str | None = None,
        fuzzy: bool = False,
        limit: int = 10,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Find modules/options by name or id; real nodes rank before synthetic.

        Prefer graft for generic code search. Use ``find`` to enter the Nix
        graph, then ``module``/``option``/``impact`` for context. Synthetic
        option nodes include ``metadata.declared_in`` and
        ``metadata.defined_in`` provenance.

        Args:
            query: Search query.
            lang: Language filter (e.g. ``nix``, ``python``).
            node_type: Node type filter (e.g. ``nix_module``, ``nix_option``).
            path_contains: Substring match on the file path.
            id_prefix: Filter by ID prefix (e.g. ``nix_option:``).
            fuzzy: If True use LIKE, else FTS5.
            limit: Max results (default 10).
            offset: Skip the first N results.
        """
        nodes = engine.find_symbol(
            query,
            lang=lang,
            node_type=node_type,
            path_contains=path_contains,
            id_prefix=id_prefix,
            fuzzy=fuzzy,
            limit=limit,
            offset=offset,
        )
        return _find_nodes(nodes)

    @server.tool()
    def module(path: str) -> dict[str, Any]:
        """Compact module context: key symbols, imports, options and packages.

        Cheaper than reading a Nix module and more focused than a full edge
        dump.

        Args:
            path: Module path (e.g. ``modules/nixos/desktop/polkit-agent.nix``).
        """
        try:
            summary = engine.summarize_module(path.removeprefix("nix:"))
        except (KeyError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _module_digest(summary)

    @server.tool()
    def impact(
        target: str | None = None,
        node_id: str | None = None,
        max_depth: int = 5,
    ) -> dict[str, Any]:
        """What a change to this node/path would affect.

        Call before editing a shared module or option. ``target`` accepts a
        node id or a repo-relative module path. The result is static
        (``basis=static``) and includes options the target itself sets.

        Args:
            target: Node id or repo-relative path.
            node_id: Alias for ``target`` (backward compatibility).
            max_depth: Max reverse BFS depth (default 5).
        """
        ref = _impact_ref(target, node_id)
        try:
            report = engine.impact_analysis(ref, max_depth=max_depth)
        except (KeyError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _impact_digest(report)

    @server.tool()
    def option(
        name: str | None = None,
        option_path: str | None = None,
        include_value: bool = False,
    ) -> dict[str, Any]:
        """Option provenance: declared_in, defined_in, conditional sets.

        Args:
            name: Dotted option path (e.g. ``security.polkit.enable``).
            option_path: Alias for ``name`` (backward compatibility).
            include_value: If True, also evaluate the option's current value.
        """
        ref = _option_ref(name, option_path)
        result = engine.introspect_option(ref, include_value=include_value)
        return result.model_dump(mode="json")

    @server.tool()
    def neighbors(node_id: str, depth: int = 1) -> dict[str, Any]:
        """Direct graph neighbourhood of a node (compact nodes and edges).

        Args:
            node_id: Node ID (e.g. ``nix:a.nix``).
            depth: Neighbourhood depth (max 20, default 1).
        """
        try:
            observation = engine.observe(node_id, depth=depth)
        except (KeyError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _observation_payload(observation)

    @server.tool()
    def status() -> dict[str, Any]:
        """Graph status: mode, node/edge counts and generation id."""
        return engine.status().model_dump(mode="json")

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
    run_stdio = getattr(server, "run_stdio_async", None)
    if run_stdio is not None:  # mcp 2.x
        await run_stdio()
    else:  # mcp 1.x FastMCP
        await server.run_async()  # type: ignore[attr-defined]


def main() -> None:
    from repo_navigator.logging_setup import configure_logging

    config = _parse_args()
    configure_logging(config.log_level, config.resolved_db_path.parent)
    asyncio.run(_run_stdio(config))


if __name__ == "__main__":
    main()
