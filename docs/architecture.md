# Architecture

Three layers, Nix at the root:

```
Layer 3: MCP Server (14 tools) ──→ QueryEngine
Layer 2: Query Engine + Graph (Database/NxGraph/Builder) ──→ SQLite (source of truth)
Layer 1: Parsers & Indexer (nix, registry, hash, cascade, watch)
```

## Layer 1: Parsers & Indexer

- **Nix:** `lexer.py` (IMPL, PATH) → `parser.py` (recursive descent, 21 goldens, UnresolvedExpr) → `ast_extract.py` (imports/options/config/mkIf/mkMerge/specialisation/_module.args/home.file/packages) → `module_parser.py` (RawNode/RawEdge, ids `nix:*`, `nix_option:*`, `file:*`, `package:*`) → `nix_parser.py` (orchestrator, fallback >50% Unresolved).
- **Registry:** `base.py` (`BaseParser` ABC, `tier`, `enabled`), `registry.py` (`LanguageConfig`, `register_language`, `get_parser_for_file`, `should_parse_file` Nix-first: tier1-3 only if `.config/` or `configures` edge + `plugins` enabled, `safe_parse` try/catch → `file` node).
- **Indexer:** `hash_engine` (xxhash content, ast sorted JSON, merkle sha256), `diff_engine`, `cascade` (reverse BFS imports, dirty, merkle, invalidate_option_values), `update_engine` (content→ast check, builder, cascade), `scan` (`collect_files`, `index_repo`, flake+package mock), `event_router` (debounce 500ms, batch), `watcher` (watchdog/polling).
- **Plugins:** `parsers/plugins/kdl.py` (tier 1, `kdl_bind/rule/spawn`, `@register_language`), enabled via `Config.plugins`.

## Layer 2: Graph

- **Database** (`graph/db.py`): SQLite WAL, `PRAGMA foreign_keys=ON`, `PRAGMA user_version` migrations, tables `generation`, `nodes` (id, type, name, path, lang, metadata JSON, hashes, timestamps, FTS5 triggers), `edges` (id, source, target, type, metadata, weight, indexes), `file_state`, `flake_inputs`, `package_index`, `option_values`. `Database` is `RLock`, `transaction` context.
- **NxGraph** (`graph/nx_graph.py`): `networkx.DiGraph`, `RWLock` (writer-preferring), `rebuild` (full from DB), `apply_delta` (added/removed nodes/edges, `_edge_pos`), `bfs`/`reverse_bfs`/`shortest_path` on `deepcopy` (no lock held for long traversals), parallel edges via `{"edges": {id: Edge}}`.
- **Builder** (`graph/builder.py`): `build_file` (snapshot old, `DELETE edges WHERE source path`, `DELETE nodes WHERE path` with `FK OFF` to preserve incoming imports, `upsert` new nodes+placeholders (synthetic `flake_input`, `package_ref`, `nix_option`), `apply_delta`, `inc_generation_id`), `build_all` (bulk, dedup, `rebuild` from DB). Edge ids deterministic `source->type->target[:hash(metadata w/o line)]`.
- **Queries** (`graph/queries.py`): `QueryEngine(db, nx_graph, config)` LRU cache key `(method, params, generation)`, budgets `width*depth≤100`, verbs `observe`/`hop`/`path`/`blast_radius`/`find_symbol` (FTS5/LIKE)/`summarize_module`/`impact_analysis`/`introspect_option`/`eval_expression` (via `EvalCache`)/`status`/`refresh`/`list_flake_inputs`/`list_packages`.

## Layer 3: MCP

- `mcp_server.py` (`MCPServer` from `mcp==2.x`, not `FastMCP`), `create_mcp_server(config, engine)` registers 14 tools, each `engine.<verb>` → `model_dump(mode="json")`, `ToolError` for user errors (budget/depth/not found), `run_stdio_async()` transport, `python -m repo_navigator.mcp_server --root .`.

## Config & CLI

- `config.py` (`Config` pydantic-settings, env `REPO_NAVIGATOR_*`, `.env`, `root`, `plugins`, `db_path`, `budgets`, `timeouts`, `watcher_mode`, `resolved_db_path`).
- `cli.py` (`typer`): `index`/`status`/`refresh`/`watch` (top), `dev lex/parse/extract/index/watch`, `query observe/hop/path/blast/find/summarize/option/eval/impact/status/flake-inputs/packages/package`, `start` (MCP stdio).

## Nix Eval & Cache

- `nix/eval.py` (`nix_available`, `nix_eval` async/sync, `nix_eval_option`), `nix/eval_cache.py` (`EvalCache(db, root)`, key `sha256(expr)`, `source_rev` from `flake.lock` (`nodes.root.locked.rev`), `get`/`get_or_eval`/`invalidate_for_files`/`invalidate_all`).
- `indexer/scan.py` handles `flake.lock` → `flake_inputs` + mock `package_index` (hash-derived version/storePath).

## Concurrency

- `RWLock` on `NxGraph`, `RLock` on `Database`, `generation_id` atomic `UPDATE generation SET value=value+1 RETURNING`, `event_router` debounce 500ms, `watcher` observer thread → `call_soon_threadsafe`.

## Migrations

- `db.py` `SCHEMA_V1` + `MIGRATIONS` dict, `PRAGMA user_version` check in `init_db()`.
