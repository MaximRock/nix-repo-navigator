# Query Verbs

All verbs are available via `QueryEngine` (Python), `CLI` (`repo-navigator query *`), and `MCP` (`repo_navigator_*`).

Every response includes `generation_id` (SQLite `generation` table, `inc` on each `build_file`/`build_all`).

## Navigation

| Verb | CLI | MCP Tool | Params | Budget | Returns |
|------|-----|----------|--------|--------|---------|
| `observe` | `query observe <node> --depth 1` | `repo_navigator_observe` | `node_id`, `depth≤20` | 20 neighbours | `Observation{node, neighbors: Neighbor[edge,node], generation_id}` |
| `hop` | `query hop <node> --relation imports --depth 2 --width 10` | `repo_navigator_hop` | `node_id`, `relation?`, `depth≤10`, `width` | `width*depth≤100` | `Subgraph{nodes, edges, generation_id}` |
| `path` | `query path <src> <dst>` | `repo_navigator_path` | `source`, `target` | — | `list[PathStep{node, edge_in, depth}]` (Dijkstra, weight) |
| `blast_radius` | `query blast <node> --max-depth 5` | `repo_navigator_blast_radius` | `node_id`, `max_depth≤10` | — | `Subgraph` (reverse BFS) |
| `find_symbol` | `query find <q> --fuzzy --lang nix` | `repo_navigator_find_symbol` | `query`, `lang?`, `fuzzy`, `limit=10` | — | `list[Node]` (FTS5 or LIKE) |
| `summarize_module` | `query summarize <path>` | `repo_navigator_summarize_module` | `path` (e.g. `a.nix`) | — | `ModuleSummary{incoming_edges, outgoing_edges, key_symbols, generation_id}` |
| `impact_analysis` | `query impact <node>` | `repo_navigator_impact_analysis` | `node_id`, `max_depth` | — | `ImpactReport{target, affected_modules/options/files, risk_level, generation_id}` |

## Introspection & Eval

| `introspect_option` | `query option <path> --eval` | `repo_navigator_introspect_option` | `option_path`, `include_value` | — | `OptionInfo{opt_type,default,example,description,declared_in,defined_in,conditional_sets,value,value_status,generation_id}` |
| `eval_expression` | `query eval "1+1"` | `repo_navigator_eval_expression` | `expr`, `timeout≤120` | — | `EvalResult{expr,value_json,status,error,cached,generation_id}` (cache `option_values`, `source_rev` from `flake.lock`) |

## System

| `status` | `query status` / `status` | `repo_navigator_status` | — | — | `StatusResponse{mode: static|hybrid (which nix), total_nodes, total_edges, uptime, sync_progress, generation_id}` |
| `refresh` | `refresh` / `query status` | `repo_navigator_refresh` | — | — | `StatusResponse` after `index_repo` |
| `flake-inputs` | `query flake-inputs` | `repo_navigator_list_flake_inputs` | — | — | `list[{name,url,rev}]` |
| `packages` | `query packages [query]` | `repo_navigator_list_packages` | `query?`, `limit=50` | — | `list[{attribute,name,version,store_path,meta}]` (mock) |
| `package` | `query package <attr>` | `repo_navigator_get_package` | `attribute` | — | `dict` or 404 |

## Examples

```bash
repo-navigator query observe nix:a.nix --depth 1
repo-navigator query hop nix:a.nix --relation imports --depth 2 --width 5
repo-navigator query path nix:a.nix nix:d.nix
repo-navigator query blast nix:c.nix --max-depth 3
repo-navigator query find "services.foo" --fuzzy --limit 5
repo-navigator query summarize a.nix
repo-navigator query option services.foo.enable --eval
repo-navigator query eval "1+1" --timeout 10
repo-navigator query impact nix:b.nix
repo-navigator query flake-inputs
repo-navigator query packages ripgrep
```

MCP (Inspector):

```bash
npx @modelcontextprotocol/inspector -- python -m repo_navigator.mcp_server --root .
# tools/list -> 14 tools, call_tool -> structuredContent + generation_id
```

Budgets enforced: `hop` raises `ValueError` if `width*depth>100`, `depth>10` etc. → MCP `ToolError` (`is_error`).

Cache: `QueryEngine` LRU key `(method, params, generation_id)`, cleared on `generation` bump.
