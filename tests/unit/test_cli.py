"""Unit tests for the CLI stubs."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from repo_navigator.cli import app

runner = CliRunner()


def test_status_runs() -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "status" in result.output


def test_refresh_runs() -> None:
    result = runner.invoke(app, ["refresh", "--root", "/tmp/repo"])
    assert result.exit_code == 0
    assert "/tmp/repo" in result.output


def test_start_runs() -> None:
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0
    assert "not implemented" in result.output


def test_dev_lex(tmp_path: Path) -> None:
    f = tmp_path / "m.nix"
    f.write_text("{ a = 1; }")
    result = runner.invoke(app, ["dev", "lex", str(f)])
    assert result.exit_code == 0
    assert "LBRACE" in result.output
    assert "IDENT" in result.output


def test_dev_parse(tmp_path: Path) -> None:
    f = tmp_path / "m.nix"
    f.write_text("{ a = 1; }")
    result = runner.invoke(app, ["dev", "parse", str(f)])
    assert result.exit_code == 0
    assert '"AttrSet"' in result.output
    assert '"name": "a"' in result.output


def test_dev_parse_via_instantiate_failure(tmp_path: Path) -> None:
    f = tmp_path / "m.nix"
    f.write_text("{ a = 1; }")
    with __import__(
        "unittest.mock", fromlist=["mock"]
    ).patch(
        "repo_navigator.parsers.nix.nix_instantiate.parse_via_nix_instantiate",
        return_value=None,
    ):
        result = runner.invoke(app, ["dev", "parse", "--via-instantiate", str(f)])
    assert result.exit_code == 1
    assert "fallback" in result.output.lower()


def test_dev_parse_via_instantiate_success(tmp_path: Path) -> None:
    f = tmp_path / "m.nix"
    f.write_text("{ a = 1; }")
    with __import__(
        "unittest.mock", fromlist=["mock"]
    ).patch(
        "repo_navigator.parsers.nix.nix_instantiate.parse_via_nix_instantiate",
        return_value={"AST": 1},
    ):
        result = runner.invoke(app, ["dev", "parse", "--via-instantiate", str(f)])
    assert result.exit_code == 0
    assert '"AST": 1' in result.output
