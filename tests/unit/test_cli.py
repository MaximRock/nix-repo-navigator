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
