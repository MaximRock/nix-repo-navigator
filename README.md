# nix-repo-navigator

[![CI](https://github.com/anomalyco/nix-repo-navigator/actions/workflows/ci.yml/badge.svg)](https://github.com/anomalyco/nix-repo-navigator/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/nix-repo-navigator)](https://pypi.org/project/nix-repo-navigator/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP](https://img.shields.io/badge/MCP-2.x-green)](https://modelcontextprotocol.io)

Knowledge-graph assistant for **NixOS** and **home-manager** repositories. Builds an incremental, multi-level graph with Nix at the root and exposes an MCP interface for AI agents.

> **Status:** MVP 0.1.0 — 370 tests, 14 MCP tools, incremental indexing, KDL plugin.

## Quickstart (30s)

```bash
# Install (with pipx recommended)
pipx install nix-repo-navigator
# or: pip install nix-repo-navigator
# or dev: uv venv && uv pip install -e ".[dev]"

# Index your repo (creates .repo-navigator.db)
nix-repo-navigator index .

# Query the graph (CLI)
nix-repo-navigator query find "services.foo" --limit 5
nix-repo-navigator query observe nix:a.nix --depth 1
nix-repo-navigator query path nix:a.nix nix:b.nix
nix-repo-navigator query impact nix:b.nix

# Option introspection (static + lazy nix eval)
nix-repo-navigator query option services.foo.enable --eval

# Flake & packages (mock)
nix-repo-navigator query flake-inputs
nix-repo-navigator query packages --query ripgrep

# Status / refresh
nix-repo-navigator status
nix-repo-navigator refresh

# Watch (incremental, debounced)
nix-repo-navigator watch .

# MCP Inspector (for agents)
npx @modelcontextprotocol/inspector -- python -m repo_navigator.mcp_server --root .
# or via CLI:
nix-repo-navigator start --root .
```

### Dev (Nix parser)

```bash
uv venv
uv pip install -e ".[dev]"
pytest tests/ -q          # 355 tests
nix-repo-navigator dev lex ./a.nix
nix-repo-navigator dev parse ./a.nix
nix-repo-navigator dev extract ./a.nix
```

## Architecture (3 layers)

```
┌─────────────────────────────────────────────┐
│ Layer 3: MCP Server (11+3 tools)            │  repo_navigator/mcp_server.py
│  observe/hop/path/blast/find/summarize/     │  → QueryEngine
│  introspect/eval/impact/status/refresh/     │
│  flake-inputs/packages                      │
├─────────────────────────────────────────────┤
│ Layer 2: Query Engine + Graph               │  graph/queries.py (LRU, budgets)
│  Database (SQLite, source of truth)         │  graph/db.py (WAL, migrations)
│  NxGraph (NetworkX, RWLock, derived)        │  graph/nx_graph.py
│  Builder (ParseResult → Node/Edge)         │  graph/builder.py
├─────────────────────────────────────────────┤
│ Layer 1: Parsers & Indexer                  │  parsers/ (nix, registry, plugins)
│  Nix: lexer → parser → ast_extract →        │  parsers/nix/ (mkIf/mkMerge, home.file)
│        module_parser (imports/declares/sets)│  parsers/nix/flake_parser.py
│  Indy: hash_engine (xxhash) / diff /        │  indexer/ (hash, cascade, update, watch)
│        cascade / update_engine / watch      │
│  Plugins: KDL (tier 1, mock)                │  parsers/plugins/kdl.py
└─────────────────────────────────────────────┘
```

* **Nix-first:** Only `.nix` is always parsed. Other languages (KDL, python…) are parsed only if referenced via `home.file`/`configures` or `.config/` and enabled in `Config.plugins`.
* **Incremental:** `content_hash` (xxhash) → `ast_hash` (ParseResult) → `merkle_hash` (deps). `UpdateEngine` + `cascade_dirty` + `watcher` (watchdog/polling, debounce 500ms).
* **MCP:** `mcp>=2.0` `MCPServer`, `stdio` transport, 14 tools, `generation_id` in every response.

## Query verbs (CLI `query *` / MCP `repo_navigator_*`)

| Verb | CLI | Description | Budget |
|------|-----|-------------|--------|
| `observe` | `query observe <node> --depth 1` | Neighbours (edge+node) | depth≤20 |
| `hop` | `query hop <node> --relation imports --depth 2` | BFS + filter | width*depth≤100 |
| `path` | `query path <src> <dst>` | Dijkstra shortest path | — |
| `blast` | `query blast <node>` | Reverse BFS (dependents) | max_depth≤10 |
| `find` | `query find <q> --fuzzy` | FTS5 / LIKE | limit 10 |
| `summarize` | `query summarize <path>` | In/out edges + key symbols | — |
| `option` | `query option <path> --eval` | Static + lazy `nix eval` | — |
| `eval` | `query eval "1+1"` | Cached `nix eval` | timeout≤120 |
| `impact` | `query impact <node>` | Blast + risk (low/med/high) | — |
| `flake-inputs` | `query flake-inputs` | `flake.lock` inputs | — |
| `packages` | `query packages [query]` | `package_index` (mock) | limit 50 |

All responses include `generation_id`.

## MCP (for agents)

```bash
# Via CLI (stdio)
nix-repo-navigator start --root . --db-path .repo-navigator.db

# Direct
python -m repo_navigator.mcp_server --root . --db-path .repo-navigator.db

# Inspector
npx @modelcontextprotocol/inspector -- python -m repo_navigator.mcp_server --root .
```

Tools: `observe`, `hop`, `path`, `blast_radius`, `find_symbol`, `summarize_module`, `introspect_option`, `eval_expression`, `impact_analysis`, `status`, `refresh`, `list_flake_inputs`, `list_packages`, `get_package` (14).

See `docs/query-verbs.md` and `docs/architecture.md`.

## Configuration

Env `REPO_NAVIGATOR_*` or `.env`:

```bash
REPO_NAVIGATOR_ROOT=.
REPO_NAVIGATOR_PLUGINS='["kdl"]'   # enable KDL tier 1
REPO_NAVIGATOR_WATCHER_MODE=auto   # auto|inotify|polling
REPO_NAVIGATOR_TIMEOUTS='{"debounce_ms":500,"nix_eval":60}'
```

`Config` (`pydantic-settings`) → `resolved_db_path` defaults to `<root>/.repo-navigator.db`.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
ruff check . && ruff format --check .
mypy src
pytest tests/ -q --cov
hatch build && twine check dist/*
```

Add a parser:

```python
from repo_navigator.parsers.base import BaseParser
from repo_navigator.parsers.registry import LanguageConfig, register_language
from repo_navigator.models.queries import ParseResult

@register_language(LanguageConfig(name="myLang", extensions=[".my"], tier=1))
class MyParser(BaseParser):
    language = "myLang"
    extensions = [".my"]
    tier = 1
    def parse(self, path, content) -> ParseResult: ...
```

See `docs/development.md`.

## License

MIT — see `LICENSE`.

## Spec

- `repo-navigator-spec-v3.md` (main)
- `IMPLEMENTATION_PLAN.md`
- `PARSER_BUGFIX_PLAN.md`
