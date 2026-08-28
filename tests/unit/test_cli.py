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
    from unittest.mock import AsyncMock, patch

    with patch("repo_navigator.mcp_server.MCPServer.run_stdio_async", new_callable=AsyncMock):
        result = runner.invoke(app, ["start", "--root", "/tmp"])
        assert result.exit_code == 0


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


def test_dev_extract(tmp_path: Path) -> None:
    f = tmp_path / "m.nix"
    f.write_text("{ imports = [ ./a.nix ]; }")
    result = runner.invoke(app, ["dev", "extract", str(f)])
    assert result.exit_code == 0
    assert '"nix_module"' in result.output
    assert '"imports"' in result.output


def test_index_directory(tmp_path: Path) -> None:
    (tmp_path / "a.nix").write_text("{ imports = [ ./b.nix ]; }")
    (tmp_path / "b.nix").write_text("{ config.x = 1; }")
    db_path = tmp_path / "test.db"
    result = runner.invoke(app, ["index", str(tmp_path), "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "indexed" in result.output.lower()
    assert "nodes=" in result.output
    # DB should exist and status should report
    assert db_path.exists()
    result2 = runner.invoke(app, ["status", "--root", str(tmp_path), "--db-path", str(db_path)])
    assert result2.exit_code == 0
    assert "nodes=" in result2.output


def test_dev_index(tmp_path: Path) -> None:
    (tmp_path / "a.nix").write_text("{ config.y = 2; }")
    db_path = tmp_path / "dev.db"
    result = runner.invoke(app, ["dev", "index", str(tmp_path), "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "indexed" in result.output.lower()


def test_index_single_file(tmp_path: Path) -> None:
    f = tmp_path / "single.nix"
    f.write_text("{ options.foo = lib.mkOption {}; }")
    db_path = tmp_path / "single.db"
    result = runner.invoke(app, ["index", str(f), "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "1 files" in result.output


def test_index_idempotent_generation(tmp_path: Path) -> None:
    (tmp_path / "a.nix").write_text("{ config.a = 1; }")
    db_path = tmp_path / "gen.db"
    runner.invoke(app, ["index", str(tmp_path), "--db-path", str(db_path)])
    from repo_navigator.graph.db import Database

    db = Database(str(db_path))
    db.init_db()
    gen1 = db.get_generation_id()
    db.close()

    runner.invoke(app, ["index", str(tmp_path), "--db-path", str(db_path)])
    db = Database(str(db_path))
    db.init_db()
    gen2 = db.get_generation_id()
    db.close()
    assert gen2 == gen1 + 1


def test_watch_help() -> None:
    result = runner.invoke(app, ["watch", "--help"])
    assert result.exit_code == 0
    assert "watch" in result.output.lower()


def test_dev_watch_help() -> None:
    result = runner.invoke(app, ["dev", "watch", "--help"])
    assert result.exit_code == 0
    assert "watch" in result.output.lower()
