#!/usr/bin/env python3
"""Read-only statistics for the repo-navigator MCP server.

``usage`` reads the OpenCode session database (SQLite, opened read-only) and
reports how the MCP tools were actually used: call counts, latency, output
size, errors, dead tools, query arguments and the fallback-to-read rate.

``health`` inspects an indexed repository (graph DB + files on disk) and
reports graph quality: synthetic nodes, dangling edges, coverage gaps,
incremental-index state and parse errors.

Nothing is uploaded. Standard library only::

    python scripts/mcp_stats.py usage
    python scripts/mcp_stats.py usage --json
    python scripts/mcp_stats.py health --root .
    python scripts/mcp_stats.py health --root . --baseline scripts/baselines/graph-health.json
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import os
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

DB_REL = Path(".repo-navigator") / "repo-navigator.db"
BASELINE_REL = Path(".repo-navigator") / "health.json"
PARSE_LOG_NAME = "parse_errors.log"
# Kept in sync with repo_navigator/indexer/scan.py (standalone script: no imports).
SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        ".repo-navigator",
        ".direnv",
        "__pycache__",
        "result",
        ".mypy_cache",
        ".pytest_cache",
        "target",
        "node_modules",
    }
)
LANG_EXTENSIONS = {"nix": frozenset({".nix"}), "python": frozenset({".py"})}
# Quality metrics where growth is a regression (size metrics are informational).
REGRESSION_KEYS = (
    "dangling_edges",
    "option_errors",
    "unindexed_files",
    "missing_files",
    "parse_errors",
)
SYNTHETIC_RATIO_TOLERANCE = 0.02


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


# ------------------------------------------------------------------ health


def _health_connect(db: Path) -> sqlite3.Connection:
    if not db.is_file():
        raise SystemExit(
            f"graph db not found: {db} (run: nix-repo-navigator index <root>)"
        )
    return sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)


def _counts(con: sqlite3.Connection, sql: str) -> dict[str, int]:
    try:
        rows = con.execute(sql).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {str(name): int(count) for name, count in rows}


def _scalar(con: sqlite3.Connection, sql: str) -> int:
    try:
        row = con.execute(sql).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row and row[0] is not None else 0


def _scan_disk(root: Path, extensions: frozenset[str]) -> set[str]:
    """Repo files matching *extensions*, using the indexer's skip rules."""
    found: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in SKIP_DIRS and (not name.startswith(".") or name == ".config")
        )
        for name in filenames:
            if name.startswith(".") or Path(name).suffix not in extensions:
                continue
            found.add((Path(dirpath) / name).relative_to(root).as_posix())
    return found


def _parse_error_count(db: Path) -> int:
    log = db.parent / PARSE_LOG_NAME
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line[:4].isdigit())


def collect_health(
    root: Path, db: Path, langs: set[str] | None = None
) -> dict[str, Any]:
    """Snapshot graph quality for *root* indexed into *db* (read-only)."""
    root = root.resolve()
    con = _health_connect(db)
    try:
        generation = _scalar(con, "SELECT value FROM generation LIMIT 1")
        total_nodes = _scalar(con, "SELECT COUNT(*) FROM nodes")
        total_edges = _scalar(con, "SELECT COUNT(*) FROM edges")
        nodes_by_type = _counts(
            con, "SELECT type, COUNT(*) FROM nodes GROUP BY type ORDER BY 2 DESC"
        )
        edges_by_type = _counts(
            con, "SELECT type, COUNT(*) FROM edges GROUP BY type ORDER BY 2 DESC"
        )
        node_langs = {
            str(row[0]) for row in con.execute("SELECT DISTINCT lang FROM nodes")
        }
        synthetic = _scalar(
            con,
            """SELECT COUNT(*) FROM nodes WHERE metadata LIKE '%"synthetic": true%'""",
        )
        dangling_sources = _scalar(
            con,
            "SELECT COUNT(*) FROM edges e LEFT JOIN nodes n ON n.id = e.source "
            "WHERE n.id IS NULL",
        )
        dangling_targets = _scalar(
            con,
            "SELECT COUNT(*) FROM edges e LEFT JOIN nodes n ON n.id = e.target "
            "WHERE n.id IS NULL",
        )
        option_status = _counts(
            con, "SELECT status, COUNT(*) FROM option_values GROUP BY status"
        )
        file_state_rows = _scalar(con, "SELECT COUNT(*) FROM file_state")
        file_state_dirty = _scalar(
            con, "SELECT COUNT(*) FROM file_state WHERE dirty = 1"
        )
        flake_inputs = _scalar(con, "SELECT COUNT(*) FROM flake_inputs")
        flake_no_rev = _scalar(
            con,
            "SELECT COUNT(*) FROM flake_inputs WHERE rev IS NULL OR rev = ''",
        )
        graph_paths = {
            str(row[0])
            for row in con.execute(
                "SELECT DISTINCT path FROM nodes WHERE path IS NOT NULL AND path <> ''"
            )
        }
    finally:
        con.close()

    if langs is None:
        langs = {"nix"} | (node_langs & set(LANG_EXTENSIONS))
    extensions = frozenset().union(
        *(LANG_EXTENSIONS[lang] for lang in langs if lang in LANG_EXTENSIONS)
    )
    disk_paths = _scan_disk(root, extensions) if extensions else set()
    graph_files = (
        {path for path in graph_paths if Path(path).suffix in extensions}
        if extensions
        else set()
    )
    unindexed = sorted(disk_paths - graph_files)
    missing = sorted(path for path in graph_files if not (root / path).is_file())
    try:
        db_size = db.stat().st_size
    except OSError:
        db_size = 0

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(root),
        "db": str(db),
        "db_size_bytes": db_size,
        "generation": generation,
        "nodes": total_nodes,
        "edges": total_edges,
        "nodes_by_type": nodes_by_type,
        "edges_by_type": edges_by_type,
        "synthetic_nodes": synthetic,
        "synthetic_ratio": round(synthetic / total_nodes, 4) if total_nodes else 0.0,
        "dangling_edges": dangling_sources + dangling_targets,
        "dangling_sources": dangling_sources,
        "dangling_targets": dangling_targets,
        "option_values": option_status,
        "option_errors": option_status.get("error", 0),
        "file_state_rows": file_state_rows,
        "file_state_dirty": file_state_dirty,
        "flake_inputs": flake_inputs,
        "flake_inputs_without_rev": flake_no_rev,
        "parse_errors": _parse_error_count(db),
        "disk_files": len(disk_paths),
        "graph_files": len(graph_files),
        "unindexed_files": len(unindexed),
        "unindexed_sample": unindexed[:10],
        "missing_files": len(missing),
        "missing_sample": missing[:10],
    }


def compare_health(current: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Lists metrics that got worse than *baseline* (empty means OK)."""
    problems: list[str] = []
    for key in REGRESSION_KEYS:
        old, new = baseline.get(key), current.get(key)
        if isinstance(old, int) and isinstance(new, int) and new > old:
            problems.append(f"{key}: {old} -> {new}")
    old_ratio = baseline.get("synthetic_ratio")
    new_ratio = current.get("synthetic_ratio")
    if (
        isinstance(old_ratio, (int, float))
        and isinstance(new_ratio, (int, float))
        and new_ratio - old_ratio > SYNTHETIC_RATIO_TOLERANCE
    ):
        problems.append(f"synthetic_ratio: {old_ratio:.3f} -> {new_ratio:.3f}")
    return problems


def print_health(
    snapshot: dict[str, Any],
    baseline_path: Path,
    baseline_loaded: bool,
    regressions: list[str],
    updated: bool,
) -> None:
    print(
        f"graph health  {snapshot['root']}  "
        f"(generation {snapshot['generation']}, "
        f"db {snapshot['db_size_bytes'] / 1048576:.2f} MiB)"
    )
    print(
        f"nodes: {snapshot['nodes']:,}   "
        f"synthetic: {snapshot['synthetic_nodes']:,} "
        f"({snapshot['synthetic_ratio']:.1%})"
    )
    print(
        f"edges: {snapshot['edges']:,}   "
        f"dangling: {snapshot['dangling_edges']} "
        f"(sources {snapshot['dangling_sources']}, "
        f"targets {snapshot['dangling_targets']})"
    )
    print(
        f"files: disk {snapshot['disk_files']:,}   "
        f"in graph {snapshot['graph_files']:,}   "
        f"unindexed {snapshot['unindexed_files']}   "
        f"missing on disk {snapshot['missing_files']}"
    )
    print(
        f"file_state: {snapshot['file_state_rows']:,} rows, "
        f"dirty {snapshot['file_state_dirty']}"
    )
    if snapshot["file_state_rows"] == 0 and snapshot["nodes"]:
        print("  WARNING: empty incremental metadata (bulk index writes no file_state)")
    option_values = snapshot["option_values"]
    rendered = ", ".join(f"{k}={v}" for k, v in sorted(option_values.items())) or "none"
    print(f"option_values: {rendered}")
    print(
        f"flake_inputs: {snapshot['flake_inputs']:,}   "
        f"without rev: {snapshot['flake_inputs_without_rev']}"
    )
    print(f"parse_errors.log: {snapshot['parse_errors']} line(s)")
    print()
    print(
        "nodes by type: "
        + ", ".join(f"{k} {v}" for k, v in snapshot["nodes_by_type"].items())
    )
    print(
        "edges by type: "
        + ", ".join(f"{k} {v}" for k, v in snapshot["edges_by_type"].items())
    )
    if snapshot["unindexed_sample"]:
        print("unindexed sample: " + ", ".join(snapshot["unindexed_sample"]))
    if updated:
        print(f"baseline: updated {baseline_path}")
    elif not baseline_loaded:
        print(f"baseline: none at {baseline_path} (use --update-baseline)")
    elif regressions:
        print(f"baseline {baseline_path}: REGRESSED")
        for problem in regressions:
            print(f"  {problem}")
    else:
        print(f"baseline {baseline_path}: OK")


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

    health = sub.add_parser("health", help="Graph quality snapshot for a repository")
    health.add_argument("--root", type=Path, default=Path.cwd())
    health.add_argument(
        "--db", type=Path, default=None, help=f"Graph DB (default: <root>/{DB_REL})"
    )
    health.add_argument(
        "--langs",
        default=None,
        help="Comma-separated languages to count on disk (default: nix + graph langs)",
    )
    health.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help=f"Baseline snapshot (default: <root>/{BASELINE_REL})",
    )
    health.add_argument(
        "--update-baseline", action="store_true", help="Overwrite the baseline file"
    )
    health.add_argument(
        "--fail-on-regress", action="store_true", help="Exit 1 if metrics regressed"
    )
    health.add_argument("--json", action="store_true", help="Machine-readable output")

    args = parser.parse_args(argv)

    if args.command == "usage":
        since = _since_to_ms(args.since) if args.since else None
        report = build_report(args.db, since=since, limit=args.limit)
        if args.json:
            json.dump(report_to_dict(report), sys.stdout, indent=2, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            print_report(report)
        return 0

    root = args.root.resolve()
    db: Path = args.db or (root / DB_REL)
    langs = (
        {lang.strip() for lang in args.langs.split(",") if lang.strip()}
        if args.langs
        else None
    )
    snapshot = collect_health(root, db, langs)
    baseline_path: Path = args.baseline or (root / BASELINE_REL)

    if args.update_baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        portable = {
            key: value
            for key, value in snapshot.items()
            if key not in ("root", "db", "generated_at")
        }
        baseline_path.write_text(
            json.dumps(portable, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    baseline: dict[str, Any] | None = None
    if baseline_path.is_file():
        try:
            loaded = json.loads(baseline_path.read_text(encoding="utf-8"))
            baseline = loaded if isinstance(loaded, dict) else None
        except ValueError:
            baseline = None
    regressions = compare_health(snapshot, baseline) if baseline else []

    if args.json:
        payload = dict(snapshot)
        payload["baseline"] = str(baseline_path) if baseline else None
        payload["regressions"] = regressions
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print_health(
            snapshot,
            baseline_path,
            baseline_loaded=baseline is not None,
            regressions=regressions,
            updated=args.update_baseline,
        )
    if args.fail_on_regress and regressions:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
