#!/usr/bin/env python3
"""Read-only statistics for the repo-navigator MCP server.

``usage`` reads the OpenCode session database (SQLite, opened read-only) and
reports how the MCP tools were actually used: call counts, latency, output
size, errors, dead tools, query arguments and the fallback-to-read rate.

Nothing is uploaded or written. Standard library only::

    python scripts/mcp_stats.py usage
    python scripts/mcp_stats.py usage --json
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_OPENCODE_DB = Path.home() / ".local/share/opencode/opencode.db"
SERVER_SOURCE = (
    Path(__file__).resolve().parents[1] / "src" / "repo_navigator" / "mcp_server.py"
)
TOOL_PREFIX = "nix-repo-navigator_"
SHORT_PREFIX = "repo_navigator_"
FALLBACK_WINDOW = 5
FALLBACK_TOOLS = frozenset({"read", "grep", "glob", "bash"})
# Tools whose output is source material, i.e. what the navigator aimed to replace.
READ_TOOLS = frozenset({"read", "grep", "glob"})
ARG_KEYS = ("query", "node_id", "source", "target", "option_path", "path", "attribute")
MAX_ERROR_CHARS = 300


@dataclass
class Call:
    session: str
    ts: int
    tool: str
    args: dict[str, Any]
    status: str
    latency_ms: float | None
    out_tokens: int
    seq_index: int = -1
    error: str | None = None


@dataclass
class UsageReport:
    db: str
    sessions: int
    calls: int
    first_ts: int
    last_ts: int
    at: str = ""
    by_tool: dict[str, dict[str, Any]] = field(default_factory=dict)
    dead_tools: list[str] = field(default_factory=list)
    registered_tools: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    top_args: dict[str, list[list[Any]]] = field(default_factory=dict)
    fallback_calls: int = 0
    nav_tokens: int = 0
    followup_tokens: int = 0


def _connect(db: Path) -> sqlite3.Connection:
    if not db.is_file():
        raise SystemExit(f"opencode db not found: {db}")
    return sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)


def _parse_part(data: str) -> dict[str, Any] | None:
    try:
        part = json.loads(data)
    except (TypeError, ValueError):
        return None
    return part if isinstance(part, dict) and part.get("type") == "tool" else None


def _output_tokens(part: dict[str, Any]) -> int:
    output = (part.get("state") or {}).get("output")
    return len(output) // 4 if isinstance(output, str) else 0


def _to_call(
    session: str, ts: int, part: dict[str, Any], seq_index: int
) -> Call | None:
    tool = str(part.get("tool") or "")
    if not tool.startswith(TOOL_PREFIX):
        return None
    state = part.get("state") or {}
    timing = state.get("time") or {}
    start, end = timing.get("start"), timing.get("end")
    latency = float(end - start) if start and end else None
    error = state.get("error")
    if error is None and state.get("status") == "error":
        error = state.get("output")
    if error is not None:
        error = str(error)[:MAX_ERROR_CHARS]
    return Call(
        session=session,
        ts=ts,
        tool=tool.removeprefix(TOOL_PREFIX).removeprefix(SHORT_PREFIX),
        args=state.get("input") or {},
        status=str(state.get("status") or "unknown"),
        latency_ms=latency,
        out_tokens=_output_tokens(part),
        seq_index=seq_index,
        error=error,
    )


def load_calls(db: Path) -> tuple[list[Call], dict[str, list[tuple[str, int]]]]:
    """Return nav calls and per-session ``(tool, out_tokens)`` sequences."""
    con = _connect(db)
    try:
        rows = con.execute(
            "SELECT session_id, time_created, data FROM part "
            "WHERE data LIKE '%\"tool\"%' "
            "ORDER BY session_id, time_created"
        ).fetchall()
    finally:
        con.close()

    calls: list[Call] = []
    sequences: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for session, ts, data in rows:
        part = _parse_part(data)
        if part is None:
            continue
        seq = sequences[session]
        call = _to_call(session, ts, part, len(seq))
        seq.append((str(part.get("tool") or ""), _output_tokens(part)))
        if call is not None:
            calls.append(call)
    return calls, sequences


def registered_tools(source: Path = SERVER_SOURCE) -> list[str]:
    """Tool names declared with ``@server.tool()`` in mcp_server.py (AST only)."""
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
    except OSError:
        return []
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if (
                isinstance(deco, ast.Call)
                and isinstance(deco.func, ast.Attribute)
                and deco.func.attr == "tool"
            ):
                names.append(node.name.removeprefix(SHORT_PREFIX))
    return sorted(set(names))


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, math.ceil(pct / 100 * len(ordered)) - 1)
    return ordered[rank]


def _arg_label(args: dict[str, Any]) -> str | None:
    for key in ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            return value if len(value) <= 60 else value[:57] + "..."
    return None


def build_report(db: Path, since: int | None = None, limit: int = 5) -> UsageReport:
    calls, sequences = load_calls(db)
    if since is not None:
        calls = [c for c in calls if c.ts >= since]
        allowed = {c.session for c in calls}
        sequences = {s: q for s, q in sequences.items() if s in allowed}
    if not calls:
        raise SystemExit(f"no {TOOL_PREFIX}* tool calls found in {db}")

    now = datetime.now()
    report = UsageReport(
        db=str(db),
        sessions=len({c.session for c in calls}),
        calls=len(calls),
        first_ts=min(c.ts for c in calls),
        last_ts=max(c.ts for c in calls),
        at=now.isoformat(timespec="seconds"),
        registered_tools=len(registered_tools()),
    )

    by_tool: dict[str, dict[str, Any]] = {}
    args_by_tool: dict[str, Counter[str]] = defaultdict(Counter)
    for call in calls:
        bucket = by_tool.setdefault(
            call.tool, {"calls": 0, "latencies": [], "tokens": 0, "errors": 0}
        )
        bucket["calls"] += 1
        bucket["tokens"] += call.out_tokens
        bucket["errors"] += call.status == "error"
        if call.latency_ms is not None:
            bucket["latencies"].append(call.latency_ms)
        label = _arg_label(call.args)
        if label:
            args_by_tool[call.tool][label] += 1
        if call.status == "error":
            report.errors.append(
                {"tool": call.tool, "args": call.args, "error": call.error}
            )

    for tool in sorted(by_tool, key=lambda t: (-by_tool[t]["calls"], t)):
        bucket = by_tool[tool]
        latencies: list[float] = bucket["latencies"]
        report.by_tool[tool] = {
            "calls": bucket["calls"],
            "p50_ms": round(_percentile(latencies, 50), 1),
            "p95_ms": round(_percentile(latencies, 95), 1),
            "max_ms": round(max(latencies), 1) if latencies else 0.0,
            "out_tokens": bucket["tokens"],
            "errors": bucket["errors"],
        }

    seen = set(by_tool)
    report.dead_tools = [t for t in registered_tools() if t not in seen]
    for tool in sorted(args_by_tool):
        report.top_args[tool] = [list(x) for x in args_by_tool[tool].most_common(limit)]

    for call in calls:
        report.nav_tokens += call.out_tokens
        seq = sequences.get(call.session, [])
        window = seq[call.seq_index + 1 : call.seq_index + 1 + FALLBACK_WINDOW]
        if any(tool in FALLBACK_TOOLS for tool, _ in window):
            report.fallback_calls += 1
        report.followup_tokens += sum(
            tokens for tool, tokens in window if tool in READ_TOOLS
        )
    return report


def report_to_dict(report: UsageReport) -> dict[str, Any]:
    def iso(ts: int) -> str:
        return datetime.fromtimestamp(ts / 1000).isoformat(timespec="seconds")

    fallback_rate = report.fallback_calls / report.calls if report.calls else 0.0
    return {
        "db": report.db,
        "generated_at": report.at,
        "period": {"first": iso(report.first_ts), "last": iso(report.last_ts)},
        "sessions": report.sessions,
        "calls": report.calls,
        "registered_tools": report.registered_tools,
        "by_tool": report.by_tool,
        "dead_tools": report.dead_tools,
        "errors": report.errors,
        "top_args": report.top_args,
        "effectiveness": {
            "fallback_calls": report.fallback_calls,
            "fallback_rate": round(fallback_rate, 3),
            "nav_tokens": report.nav_tokens,
            "followup_tokens": report.followup_tokens,
            "followup_ratio": round(
                report.followup_tokens / max(report.nav_tokens, 1), 3
            ),
        },
    }


def print_report(report: UsageReport) -> None:
    period = (
        f"{datetime.fromtimestamp(report.first_ts / 1000):%Y-%m-%d}"
        f" .. {datetime.fromtimestamp(report.last_ts / 1000):%Y-%m-%d}"
    )
    print(f"repo-navigator MCP usage  ({report.db}, read-only)")
    print(
        f"period: {period}   sessions: {report.sessions}   "
        f"calls: {report.calls}   registered tools: {report.registered_tools}"
    )
    print()
    print(
        f"{'tool':<22}{'calls':>7}{'p50':>8}{'p95':>8}{'max':>8}"
        f"{'out_tok':>10}{'err':>6}"
    )
    for tool, row in report.by_tool.items():
        print(
            f"{tool:<22}{row['calls']:>7}{row['p50_ms']:>8.0f}{row['p95_ms']:>8.0f}"
            f"{row['max_ms']:>8.0f}{row['out_tokens']:>10}{row['errors']:>6}"
        )
    print()
    print(f"never called ({len(report.dead_tools)}): " + ", ".join(report.dead_tools))

    if report.errors:
        print(f"\nerrors ({len(report.errors)}):")
        for err in report.errors:
            print(f"  {err['tool']} {json.dumps(err['args'])} — {err['error']}")

    if report.top_args:
        print("\ntop arguments:")
        for tool, items in report.top_args.items():
            rendered = ", ".join(f"{value} ({count})" for value, count in items)
            print(f"  {tool}: {rendered}")

    rate = report.fallback_calls / report.calls if report.calls else 0.0
    ratio = report.followup_tokens / max(report.nav_tokens, 1)
    print("\neffectiveness:")
    print(
        f"  fallback to read/grep/glob/bash within {FALLBACK_WINDOW} calls: "
        f"{report.fallback_calls}/{report.calls} ({rate:.0%})"
    )
    print(f"  navigator output: {report.nav_tokens:,} tokens")
    print(
        f"  follow-up read/grep/glob: {report.followup_tokens:,} tokens "
        f"({ratio:.2f}x navigator output)"
    )


def _since_to_ms(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(
            f"--since must be YYYY-MM-DD or ISO datetime: {value}"
        ) from exc
    return int(parsed.timestamp() * 1000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only statistics for the repo-navigator MCP server."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    usage = sub.add_parser("usage", help="MCP tool usage from the OpenCode session DB")
    usage.add_argument("--db", type=Path, default=DEFAULT_OPENCODE_DB)
    usage.add_argument("--since", help="Only calls on/after this date (YYYY-MM-DD)")
    usage.add_argument("--limit", type=int, default=5, help="Arguments per tool")
    usage.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)

    since = _since_to_ms(args.since) if args.since else None
    report = build_report(args.db, since=since, limit=args.limit)
    if args.json:
        json.dump(report_to_dict(report), sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
