"""repo-navigator CLI."""

from __future__ import annotations

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


if __name__ == "__main__":
    app()
