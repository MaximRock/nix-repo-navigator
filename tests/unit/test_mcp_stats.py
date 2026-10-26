"""Tests for scripts/mcp_stats.py (usage report)."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "mcp_stats.py"


def _load_stats() -> Any:
    spec = importlib.util.spec_from_file_location("mcp_stats", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


stats = _load_stats()


def _tool_part(
    tool: str,
    *,
    status: str = "completed",
    args: dict[str, Any] | None = None,
    output: str = "",
    start: int = 0,
    end: int = 0,
    error: str | None = None,
) -> str:
    state: dict[str, Any] = {"status": status, "input": args or {}, "output": output}
    if start or end:
        state["time"] = {"start": start, "end": end}
    if error:
        state["error"] = error
    return json.dumps({"type": "tool", "tool": tool, "state": state})


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "opencode.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
        "time_created INTEGER, time_updated INTEGER, data TEXT)"
    )
    nav = "nix-repo-navigator_repo_navigator_"
    rows = [
        (
            "p1",
            "s1",
            1000,
            _tool_part(
                nav + "find_symbol",
                args={"query": "foo"},
                output="x" * 40,
                start=100,
                end=110,
            ),
        ),
        ("p2", "s1", 2000, _tool_part("read", output="y" * 400)),
        (
            "p3",
            "s1",
            3000,
            _tool_part(nav + "observe", output="z" * 80, start=200, end=220),
        ),
        (
            "p4",
            "s1",
            4000,
            _tool_part(
                nav + "get_package",
                args={"attribute": "nixos-rebuild"},
                status="error",
                start=300,
                end=305,
                error="package not found: nixos-rebuild",
            ),
        ),
        ("p5", "s1", 5000, _tool_part("edit", output="w" * 100)),
        (
            "p6",
            "s1",
            6000,
            _tool_part(nav + "status", output="s" * 200, start=400, end=430),
        ),
        ("p7", "s2", 7000, json.dumps({"type": "text", "text": "not a tool"})),
        ("p8", "s2", 8000, _tool_part("bash", output="b" * 100)),
    ]
    con.executemany(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        [(pid, sid, sid, ts, ts, data) for pid, sid, ts, data in rows],
    )
    con.commit()
    con.close()
    return path


def test_usage_aggregates(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        stats,
        "registered_tools",
        lambda: ["find_symbol", "observe", "get_package", "status", "path"],
    )
    report = stats.build_report(db)

    assert report.calls == 4
    assert report.sessions == 1
    assert set(report.by_tool) == {"find_symbol", "observe", "get_package", "status"}
    assert report.by_tool["find_symbol"]["p50_ms"] == 10.0
    assert report.by_tool["get_package"]["errors"] == 1
    assert report.dead_tools == ["path"]
    assert len(report.errors) == 1
    assert "package not found" in report.errors[0]["error"]


def test_usage_effectiveness(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stats, "registered_tools", lambda: ["find_symbol"])
    report = stats.build_report(db)

    # Only find_symbol is followed by a read within the window.
    assert report.fallback_calls == 1
    assert report.nav_tokens == 80
    assert report.followup_tokens == 100

    data = stats.report_to_dict(report)
    assert data["effectiveness"]["fallback_rate"] == 0.25
    assert data["effectiveness"]["followup_ratio"] == 1.25


def test_usage_since_keeps_sequence_positions(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stats, "registered_tools", lambda: ["find_symbol"])
    report = stats.build_report(db, since=3999)

    assert report.calls == 2
    assert set(report.by_tool) == {"get_package", "status"}
    # get_package is followed by edit + status: not a fallback read.
    assert report.fallback_calls == 0
    assert report.nav_tokens == 50


def test_cli_json(db: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setattr(stats, "registered_tools", lambda: ["find_symbol"])
    assert stats.main(["usage", "--db", str(db), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["calls"] == 4
    assert payload["registered_tools"] == 1


def test_registered_tools_from_real_source() -> None:
    names = stats.registered_tools()
    assert "observe" in names
    assert "report" in names
    assert len(names) >= 10
