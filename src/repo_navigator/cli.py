"""repo-navigator CLI (Phase 1: stubs)."""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(
    name="repo-navigator",
    help="Knowledge-graph assistant for NixOS and home-manager repositories.",
    no_args_is_help=True,
)


@app.command()
def start(
    root: Path | None = typer.Option(None, help="Repository root (default: cwd)."),
) -> None:
    """Start the MCP server (stub)."""
    typer.echo(f"start: MCP server is not implemented yet (root={root or Path.cwd()})")


@app.command()
def status(
    root: Path | None = typer.Option(None, help="Repository root (default: cwd)."),
) -> None:
    """Show graph size and mode (stub)."""
    typer.echo(f"status: graph is empty, indexer not implemented yet (root={root or Path.cwd()})")


@app.command()
def refresh(
    root: Path | None = typer.Option(None, help="Repository root (default: cwd)."),
) -> None:
    """Force a full rescan (stub)."""
    typer.echo(f"refresh: rescan is not implemented yet (root={root or Path.cwd()})")


if __name__ == "__main__":
    app()
