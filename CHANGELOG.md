# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-08-29

### Added
- **Phase 1:** Project scaffold, `Config` (pydantic-settings), `Database` (SQLite WAL + migrations, FTS5), `NxGraph` (NetworkX + RWLock), `FileState`/`OptionValue` models.
- **Phase 2:** Nix lexer (`tokenize`) + parser (`parse`, `parse_to_dict`, `UnresolvedExpr` fallback) — 21 golden fixtures, 120 tests.
- **Phase 3:** `ast_extract` (imports/options/config/mkIf/mkMerge/specialisation/_module.args/home.file/packages/functions) + `module_parser` (RawNode/RawEdge) + `NixParser` (tier 0) + `nix_instantiate` fallback + `cli dev lex|parse|extract`, 166 tests.
- **Phase 4:** `BaseParser`/`LanguageConfig`/`registry` (tier, `should_parse_file`, `safe_parse`, `register_language`) + `GraphBuilder` (deterministic ids, placeholders, FK OFF, `NxGraph.apply_delta`, `generation_id`) + `index_repo`/`collect_files` + `cli index/status/refresh`, 199 tests.
- **Phase 5:** `hash_engine` (xxhash `content_hash`, `ast_hash`, `merkle_hash` sha256), `diff_engine`, `cascade_dirty` (reverse BFS, dirty, `invalidate_option_values`), `UpdateEngine` (content/ast check, builder, cascade), `EventRouter` (debounce 500ms, batch), `RepoWatcher` (watchdog + polling fallback) + `cli watch`, 255 tests.
- **Phase 6:** `QueryEngine` (LRU, budgets): `observe`/`hop`/`path`/`blast_radius`/`find_symbol`/`summarize_module`/`impact_analysis` + `introspect_option`/`eval_expression` (lazy `nix eval` + `option_values` cache) + `status`/`refresh` + CLI `query *` (11 verbs), 293 tests.
- **Phase 7:** `mcp_server.py` (`MCPServer` 14 tools) + `cli start` (stdio), error handling (`ToolError`), E2E MCP session, 311 tests.
- **Phase 8:** `nix/eval.py` (`nix_available`, `nix_eval` async/sync), `nix/eval_cache.py` (`EvalCache` with `flake.lock` rev, `get_or_eval`, `invalidate_for_files`), integration into `QueryEngine.eval_expression`, 335 tests.
- **Phase 9:** `parsers/nix/flake_parser.py` (`parse_flake_lock`/`parse_flake_nix`) + `index_repo` flake inputs → `flake_inputs` table + `flake_input` nodes, `nix/package_index.py` (mock `resolve_package`, `PackageIndexBuilder`), HM polish (`home.sessionVariables`, `home.activation`, `xdg.dataFile`, `programs.*.enable`) → `sets` edges, `QueryEngine`/`CLI`/`MCP` for `flake-inputs`/`packages`, 355 tests.
- **Phase 10:** `README`/`CHANGELOG`/`docs` (architecture, query-verbs, development), CI (GitHub Actions), `pre-commit`, `hatch` packaging, KDL plugin (`parsers/plugins/kdl.py`, tier 1, `kdl_bind`/`kdl_rule`/`kdl_spawn`).

### Changed
- `IMPORTS_LIST` fixture wrapped in braces, `LAMBDA≡COLON`/`ARROW≡IMPL` deviations documented.
- `module_parser._normalise_import` now `posixpath.normpath` (removes `./`).

### Fixed
- Lexer `IMPL` (`->`), `PATH` for `./rel`, `model_config` populate_by_name, `KW_IN` break, `pydantic model_dump` Union collapse via `ast_to_dict`.
- Parser `true`/`false`/`null` literals, list parsing (`_parse_postfix_no_funcall`), `UnresolvedExpr` counting.
- DB `value_json` int handling, `flake.lock` rev invalidation, `cascade` early-return for `invalidate`.

## [Unreleased]

### Planned
- Real `nix search` for `package_index` (currently mock).
- Additional plugins: `python`/`shell`/`lua` via `tree-sitter`.
- `systemd --user` watcher service.
