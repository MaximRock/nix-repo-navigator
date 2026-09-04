"""repo-navigator CLI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

app = typer.Typer(
    name="repo-navigator",
    help="Knowledge-graph assistant for NixOS and home-manager repositories.",
    no_args_is_help=True,
)

dev_app = typer.Typer(
    name="dev",
    help="Developer utilities: lex / parse a Nix file.",
    no_args_is_help=True,
)
app.add_typer(dev_app, name="dev")

query_app = typer.Typer(
    name="query",
    help="Query the knowledge graph.",
    no_args_is_help=True,
)
app.add_typer(query_app, name="query")


@app.command()
def start(
    root: Path | None = typer.Option(None, help="Repository root (default: cwd)."),
    db_path: Path | None = typer.Option(None, help="SQLite DB path (default: <root>/.repo-navigator/repo-navigator.db)."),
) -> None:
    """Start the MCP server (stdio transport)."""
    import asyncio

    from repo_navigator.config import Config
    from repo_navigator.mcp_server import create_mcp_server

    cfg = Config(root=root or Path.cwd(), db_path=db_path)  # type: ignore[arg-type]
    server = create_mcp_server(config=cfg)

    async def _run() -> None:
        await server.run_stdio_async()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        typer.echo("MCP server stopped.")
    except Exception as exc:
        typer.echo(f"MCP server error: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command()
def status(
    root: Path | None = typer.Option(None, help="Repository root (default: cwd)."),
    db_path: Path | None = typer.Option(None, help="SQLite DB path (default: <root>/.repo-navigator/repo-navigator.db)."),
) -> None:
    """Show graph size and generation."""
    from repo_navigator.config import Config
    from repo_navigator.graph.db import Database

    cfg = Config(root=root or Path.cwd(), db_path=db_path)  # type: ignore[arg-type]
    db_file = cfg.resolved_db_path
    if not db_file.exists():
        typer.echo(f"status: no DB at {db_file} (root={cfg.root})")
        typer.echo("  nodes=0 edges=0 generation=0")
        return
    db = Database(str(db_file))
    try:
        db.init_db()
        nodes = db.count_nodes()
        edges = db.count_edges()
        gen = db.get_generation_id()
        typer.echo(f"status: root={cfg.root} db={db_file}")
        typer.echo(f"  nodes={nodes} edges={edges} generation={gen}")
    finally:
        db.close()


@app.command()
def refresh(
    root: Path | None = typer.Option(None, help="Repository root (default: cwd)."),
    db_path: Path | None = typer.Option(None, help="SQLite DB path (default: <root>/.repo-navigator/repo-navigator.db)."),
) -> None:
    """Force a full rescan (alias for index)."""
    _run_index(root or Path.cwd(), db_path)


@app.command("index")
def index_cmd(
    path: Path = typer.Argument(Path("."), help="Repository root or file to index."),
    db_path: Path | None = typer.Option(None, help="SQLite DB path (default: <root>/.repo-navigator/repo-navigator.db)."),
) -> None:
    """Index a repository (or single file) into the graph."""
    _run_index(path, db_path)


@dev_app.command("index")
def dev_index(
    path: Path = typer.Argument(Path("."), help="Repository root or file to index."),
    db_path: Path | None = typer.Option(None, help="SQLite DB path (default: <root>/.repo-navigator/repo-navigator.db)."),
) -> None:
    """(dev) Index a repository (or single file) into the graph."""
    _run_index(path, db_path)


@app.command("watch")
def watch_cmd(
    path: Path = typer.Argument(Path("."), help="Repository root to watch."),
    db_path: Path | None = typer.Option(None, help="SQLite DB path (default: <root>/.repo-navigator/repo-navigator.db)."),
) -> None:
    """Watch a repository for changes and incrementally update the graph."""
    asyncio.run(_run_watch(path, db_path))


@dev_app.command("watch")
def dev_watch(
    path: Path = typer.Argument(Path("."), help="Repository root to watch."),
    db_path: Path | None = typer.Option(None, help="SQLite DB path (default: <root>/.repo-navigator/repo-navigator.db)."),
) -> None:
    """(dev) Watch a repository for changes."""
    asyncio.run(_run_watch(path, db_path))


def _ensure_db_dir(db_file: Path) -> None:
    db_file.parent.mkdir(parents=True, exist_ok=True)


def _run_index(path: Path, db_path: Path | None) -> None:
    from repo_navigator.config import Config
    from repo_navigator.graph.builder import GraphBuilder
    from repo_navigator.graph.db import Database
    from repo_navigator.graph.nx_graph import NxGraph
    from repo_navigator.indexer.scan import index_repo
    from repo_navigator.parsers.registry import safe_parse

    p = Path(path)

    # Single file fast-path
    if p.is_file():
        try:
            cwd = Path.cwd().resolve()
            if p.resolve().is_relative_to(cwd):
                cfg_root = cwd
                rel = p.resolve().relative_to(cwd).as_posix()
            else:
                cfg_root = p.parent.resolve()
                rel = p.name
        except Exception:
            cfg_root = p.parent.resolve()
            rel = p.name

        cfg = Config(root=cfg_root, db_path=db_path)  # type: ignore[arg-type]
        db_file = cfg.resolved_db_path
        _ensure_db_dir(db_file)
        db = Database(str(db_file))
        nx_graph = NxGraph()
        try:
            db.init_db()
            existing_nodes = db.get_all_nodes()
            existing_edges = db.get_all_edges()
            if existing_nodes or existing_edges:
                nx_graph.rebuild(nodes=existing_nodes, edges=existing_edges)
            content = p.read_text(encoding="utf-8")
            parse_result = safe_parse(Path(rel), content)
            builder = GraphBuilder(db, nx_graph)
            builder.build_file(rel, parse_result)
            typer.echo(
                f"indexed 1 files -> "
                f"nodes={db.count_nodes()} edges={db.count_edges()} "
                f"generation={db.get_generation_id()} db={db_file}"
            )
        finally:
            db.close()
        return

    # Directory: full repo index
    cfg_root = p.resolve()
    cfg = Config(root=cfg_root, db_path=db_path)  # type: ignore[arg-type]
    db_file = cfg.resolved_db_path
    _ensure_db_dir(db_file)
    db = Database(str(db_file))
    nx_graph = NxGraph()
    try:
        db.init_db()
        existing_nodes = db.get_all_nodes()
        existing_edges = db.get_all_edges()
        if existing_nodes or existing_edges:
            nx_graph.rebuild(nodes=existing_nodes, edges=existing_edges)

        stats = index_repo(cfg_root, db, nx_graph, config=cfg)
        typer.echo(
            f"indexed {stats['files']} files -> "
            f"nodes={stats['nodes']} edges={stats['edges']} "
            f"generation={stats['generation']} "
            f"({stats['elapsed_ms']:.0f}ms) db={db_file}"
        )
    finally:
        db.close()


async def _run_watch(path: Path, db_path: Path | None) -> None:
    from repo_navigator.config import Config
    from repo_navigator.graph.builder import GraphBuilder
    from repo_navigator.graph.db import Database
    from repo_navigator.graph.nx_graph import NxGraph
    from repo_navigator.indexer.event_router import EventRouter
    from repo_navigator.indexer.update_engine import UpdateEngine
    from repo_navigator.watcher.filesystem import RepoWatcher

    root = Path(path).resolve()
    cfg = Config(root=root, db_path=db_path)  # type: ignore[arg-type]
    db_file = cfg.resolved_db_path
    _ensure_db_dir(db_file)

    db = Database(str(db_file))
    db.init_db()
    nx_graph = NxGraph()
    # Preload existing graph
    nodes = db.get_all_nodes()
    edges = db.get_all_edges()
    if nodes or edges:
        nx_graph.rebuild(nodes=nodes, edges=edges)

    builder = GraphBuilder(db, nx_graph)
    update_engine = UpdateEngine(db, nx_graph, builder=builder, root=root)
    event_router = EventRouter(debounce_ms=float(cfg.timeouts.get("debounce_ms", 500)))

    watcher = RepoWatcher(root, event_router, config=cfg)
    mode = watcher.start(loop=asyncio.get_running_loop())
    typer.echo(f"watch: {root} (mode={mode}, debounce={cfg.timeouts.get('debounce_ms', 500)}ms) db={db_file}")
    typer.echo("Press Ctrl+C to stop.")

    # Initial index if DB is empty
    if db.count_nodes() == 0:
        from repo_navigator.indexer.scan import index_repo

        stats = index_repo(root, db, nx_graph, config=cfg)
        typer.echo(f"initial index: {stats['files']} files, {stats['nodes']} nodes")

    try:
        while True:
            batch = await event_router.queue.get()
            for file_path in batch:
                p = Path(file_path)
                # file_path may be absolute (watcher) or relative; normalise
                if not p.is_absolute():
                    p = root / file_path
                if p.exists():
                    result = update_engine.process_file(p)
                    typer.echo(f"watch: {p} -> {result['reason']} affected={result['affected']}")
                else:
                    # Deleted
                    result = update_engine.process_deleted_file(file_path)
                    typer.echo(f"watch: deleted {file_path} -> {result['reason']}")
            event_router.queue.task_done()
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        watcher.stop()
        db.close()
        typer.echo("watch: stopped")


def _get_query_engine(
    root: Path | None = None, db_path: Path | None = None
):
    from repo_navigator.config import Config
    from repo_navigator.graph.db import Database
    from repo_navigator.graph.nx_graph import NxGraph
    from repo_navigator.graph.queries import QueryEngine

    cfg = Config(root=root or Path.cwd(), db_path=db_path)  # type: ignore[arg-type]
    db_file = cfg.resolved_db_path
    db = Database(str(db_file))
    db.init_db()
    g = NxGraph()
    nodes = db.get_all_nodes()
    edges = db.get_all_edges()
    if nodes or edges:
        g.rebuild(nodes=nodes, edges=edges)
    engine = QueryEngine(db, g, config=cfg)
    return engine, db


@query_app.command("observe")
def query_observe(
    node_id: str = typer.Argument(..., help="Node ID (e.g. nix:a.nix)"),
    depth: int = typer.Option(1, help="Depth (max 20)"),
    root: Path | None = typer.Option(None, help="Repository root"),
    db_path: Path | None = typer.Option(None, help="DB path"),
) -> None:
    """Observe direct neighbourhood of a node."""
    engine, db = _get_query_engine(root, db_path)
    try:
        result = engine.observe(node_id, depth=depth)
        typer.echo(json.dumps(result.model_dump(), indent=2, default=str))
    except KeyError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1)
    finally:
        db.close()


@query_app.command("hop")
def query_hop(
    node_id: str = typer.Argument(..., help="Node ID"),
    relation: str | None = typer.Option(None, help="Edge type filter (e.g. imports)"),
    depth: int = typer.Option(1, help="Depth (max 10)"),
    width: int = typer.Option(10, help="Width per level"),
    root: Path | None = typer.Option(None, help="Repository root"),
    db_path: Path | None = typer.Option(None, help="DB path"),
) -> None:
    """BFS hop with optional relation filter."""
    engine, db = _get_query_engine(root, db_path)
    try:
        result = engine.hop(node_id, relation=relation, depth=depth, width=width)
        typer.echo(json.dumps(result.model_dump(), indent=2, default=str))
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1)
    finally:
        db.close()


@query_app.command("path")
def query_path(
    source: str = typer.Argument(..., help="Source node ID"),
    target: str = typer.Argument(..., help="Target node ID"),
    root: Path | None = typer.Option(None, help="Repository root"),
    db_path: Path | None = typer.Option(None, help="DB path"),
) -> None:
    """Shortest path between two nodes."""
    engine, db = _get_query_engine(root, db_path)
    try:
        result = engine.path(source, target)
        typer.echo(json.dumps([r.model_dump() for r in result], indent=2, default=str))
    finally:
        db.close()


@query_app.command("blast")
def query_blast(
    node_id: str = typer.Argument(..., help="Node ID"),
    max_depth: int = typer.Option(5, help="Max depth (max 10)"),
    root: Path | None = typer.Option(None, help="Repository root"),
    db_path: Path | None = typer.Option(None, help="DB path"),
) -> None:
    """Reverse BFS: who depends on this node."""
    engine, db = _get_query_engine(root, db_path)
    try:
        result = engine.blast_radius(node_id, max_depth=max_depth)
        typer.echo(json.dumps(result.model_dump(), indent=2, default=str))
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1)
    finally:
        db.close()


@query_app.command("find")
def query_find(
    query: str = typer.Argument(..., help="Search query"),
    lang: str | None = typer.Option(None, help="Language filter"),
    fuzzy: bool = typer.Option(False, help="Fuzzy LIKE search"),
    limit: int = typer.Option(10, help="Max results"),
    root: Path | None = typer.Option(None, help="Repository root"),
    db_path: Path | None = typer.Option(None, help="DB path"),
) -> None:
    """Full-text search for symbols."""
    engine, db = _get_query_engine(root, db_path)
    try:
        results = engine.find_symbol(query, lang=lang, fuzzy=fuzzy, limit=limit)
        typer.echo(json.dumps([r.model_dump() for r in results], indent=2, default=str))
    finally:
        db.close()


@query_app.command("summarize")
def query_summarize(
    path: str = typer.Argument(..., help="Module path (e.g. a.nix)"),
    root: Path | None = typer.Option(None, help="Repository root"),
    db_path: Path | None = typer.Option(None, help="DB path"),
) -> None:
    """Summarize a module."""
    engine, db = _get_query_engine(root, db_path)
    try:
        result = engine.summarize_module(path)
        typer.echo(json.dumps(result.model_dump(), indent=2, default=str))
    except KeyError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1)
    finally:
        db.close()


@query_app.command("option")
def query_option(
    option_path: str = typer.Argument(..., help="Option path (e.g. services.foo.enable)"),
    eval: bool = typer.Option(False, "--eval", help="Include evaluated value"),
    root: Path | None = typer.Option(None, help="Repository root"),
    db_path: Path | None = typer.Option(None, help="DB path"),
) -> None:
    """Introspect a Nix option."""
    engine, db = _get_query_engine(root, db_path)
    try:
        result = engine.introspect_option(option_path, include_value=eval)
        typer.echo(json.dumps(result.model_dump(), indent=2, default=str))
    finally:
        db.close()


@query_app.command("eval")
def query_eval(
    expr: str = typer.Argument(..., help="Nix expression"),
    timeout: int = typer.Option(60, help="Timeout seconds (max 120)"),
    root: Path | None = typer.Option(None, help="Repository root"),
    db_path: Path | None = typer.Option(None, help="DB path"),
) -> None:
    """Evaluate a Nix expression (cached)."""
    engine, db = _get_query_engine(root, db_path)
    try:
        result = engine.eval_expression(expr, timeout=timeout)
        typer.echo(json.dumps(result.model_dump(), indent=2, default=str))
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1)
    finally:
        db.close()


@query_app.command("impact")
def query_impact(
    node_id: str = typer.Argument(..., help="Node ID"),
    max_depth: int = typer.Option(5, help="Max depth"),
    root: Path | None = typer.Option(None, help="Repository root"),
    db_path: Path | None = typer.Option(None, help="DB path"),
) -> None:
    """Impact analysis for a node."""
    engine, db = _get_query_engine(root, db_path)
    try:
        result = engine.impact_analysis(node_id, max_depth=max_depth)
        typer.echo(json.dumps(result.model_dump(), indent=2, default=str))
    finally:
        db.close()


@query_app.command("status")
def query_status(
    root: Path | None = typer.Option(None, help="Repository root"),
    db_path: Path | None = typer.Option(None, help="DB path"),
) -> None:
    """Show graph status (query engine)."""
    engine, db = _get_query_engine(root, db_path)
    try:
        result = engine.status()
        typer.echo(json.dumps(result.model_dump(), indent=2, default=str))
    finally:
        db.close()


@query_app.command("flake-inputs")
def query_flake_inputs(
    root: Path | None = typer.Option(None, help="Repository root"),
    db_path: Path | None = typer.Option(None, help="DB path"),
) -> None:
    """List flake inputs from flake.lock."""
    engine, db = _get_query_engine(root, db_path)
    try:
        results = engine.list_flake_inputs()
        typer.echo(json.dumps(results, indent=2, default=str))
    finally:
        db.close()


@query_app.command("packages")
def query_packages(
    query: str | None = typer.Argument(None, help="Filter query (attribute substring)"),
    limit: int = typer.Option(50, help="Max results"),
    root: Path | None = typer.Option(None, help="Repository root"),
    db_path: Path | None = typer.Option(None, help="DB path"),
) -> None:
    """List packages from package_index (mock)."""
    engine, db = _get_query_engine(root, db_path)
    try:
        results = engine.list_packages(query=query, limit=limit)
        typer.echo(json.dumps(results, indent=2, default=str))
    finally:
        db.close()


@query_app.command("package")
def query_package(
    attribute: str = typer.Argument(..., help="Package attribute (e.g. ripgrep)"),
    root: Path | None = typer.Option(None, help="Repository root"),
    db_path: Path | None = typer.Option(None, help="DB path"),
) -> None:
    """Get a single package by attribute."""
    engine, db = _get_query_engine(root, db_path)
    try:
        result = engine.get_package(attribute)
        if result is None:
            typer.echo(f"package not found: {attribute}", err=True)
            raise typer.Exit(code=1)
        typer.echo(json.dumps(result, indent=2, default=str))
    finally:
        db.close()


@dev_app.command("lex")
def dev_lex(path: Path) -> None:
    """Tokenize a .nix file and print the token table."""
    from repo_navigator.parsers.nix.lexer import tokenize

    source = path.read_text()
    for tok in tokenize(source):
        typer.echo(
            f"{tok.type:<18} value={tok.value!r:<26} "
            f"line={tok.line} col={tok.col}"
        )


@dev_app.command("parse")
def dev_parse(
    path: Path,
    via_instantiate: bool = typer.Option(
        False, "--via-instantiate", help="Use nix-instantiate fallback instead of the built-in parser."
    ),
) -> None:
    """Parse a .nix file and print the AST."""
    if via_instantiate:
        from repo_navigator.parsers.nix.nix_instantiate import parse_via_nix_instantiate

        result = parse_via_nix_instantiate(path)
        if result is None:
            typer.echo("nix-instantiate fallback unavailable or failed.", err=True)
            raise typer.Exit(code=1)
        typer.echo(json.dumps(result, indent=2, default=str))
        return

    from repo_navigator.parsers.nix.parser import parse_to_dict

    source = path.read_text()
    tree = parse_to_dict(source)
    typer.echo(json.dumps(tree, indent=2, default=str))


@dev_app.command("extract")
def dev_extract(path: Path) -> None:
    """Parse a .nix file and print the extracted graph (nodes + edges)."""
    from repo_navigator.parsers.nix_parser import NixParser

    parser = NixParser()
    source = path.read_text()
    result = parser.parse(path, source)
    typer.echo(json.dumps(result.model_dump(), indent=2, default=str))


if __name__ == "__main__":
    app()
