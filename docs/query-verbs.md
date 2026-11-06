# Query Verbs

All verbs are available through `QueryEngine` (Python) and the CLI
(`nix-repo-navigator query *`). The MCP server deliberately exposes only six
compact tools; the rest stay CLI-only to keep the agent surface small.

**Division of labour:** use `graft` for generic where-is/code search. Use
`repo-navigator` for Nix graph context: module structure, option provenance and
change impact.

Every response includes `generation_id` (SQLite `generation` table, `inc` on
each `build_file`/`build_all`).

## MCP surface (6 tools)

MCP tools use short names, so an OpenCode server named `nix-repo-navigator`
exposes `nix-repo-navigator_find`, `nix-repo-navigator_impact`, and so on.

| Tool | Params | Returns |
|------|--------|---------|
| `find` | `query`, `lang?`, `node_type?`, `path_contains?`, `id_prefix?`, `fuzzy=false`, `limit=10`, `offset=0` | `list[compact Node]`; real nodes rank before synthetic ones. Synthetic options carry `metadata.declared_in`/`metadata.defined_in`. |
| `module` | `path` | `{path, key_symbols, imports, imported_by, options_set, packages, configures, dangling, generation_id}` |
| `impact` | `target` (node id or repo path), `node_id?` (legacy alias), `max_depth=5` | `{target, affected_modules, affected_files, affected_options, options_set, risk_level, basis="static", generation_id}` |
| `option` | `name` (dotted path), `option_path?` (legacy alias), `include_value=false` | `OptionInfo` (`declared_in`, `defined_in`, `conditional_sets`, type/default/description, optional evaluated value) |
| `neighbors` | `node_id`, `depth=1` (`≤20`) | `{node, neighbors: [{edge, node, dangling}], generation_id}` |
| `status` | — | `StatusResponse` (`mode`, node/edge counts, uptime, `generation_id`) |

MCP payloads are compact: nodes omit hashes/timestamps, `module` omits raw
edge lists, `impact` omits evidence chains, and `neighbors` returns brief
nodes/edges. Full models remain available from the library and CLI.

`impact` is static graph analysis (`basis="static"`): it cannot see dynamic
imports or evaluation-time module composition. Run it before editing a shared
module or option; check `options_set` for what the target itself sets.

## CLI query verbs

| Verb | CLI | Description | Budget |
|------|-----|-------------|--------|
| `observe` | `query observe <node> --depth 1` | Neighbours (edge+node) | depth≤20 |
| `hop` | `query hop <node> --relation imports --depth 2` | BFS + filter | width*depth≤100 |
| `path` | `query path <src> <dst>` | Dijkstra shortest path | — |
| `blast` | `query blast <node> --max-depth 5` | Reverse BFS (dependents) | max_depth≤10 |
| `dependencies` | `query dependencies <node> --max-depth 5` | Forward dependency closure with evidence | max_depth≤10 |
| `dependents` | `query dependents <node> --max-depth 5` | Reverse dependency closure with evidence | max_depth≤10 |
| `find` | `query find <q> --fuzzy --lang nix` | FTS5 / LIKE | limit 10 |
| `summarize` | `query summarize <path>` | In/out edges + key symbols | — |
| `option` | `query option <path> --eval` | Static + lazy `nix eval` | — |
| `eval` | `query eval "1+1"` | Cached `nix eval` | timeout≤120 |
| `impact` | `query impact <node>` | Blast radius + risk | — |
| `flake-inputs` | `query flake-inputs` | `flake.lock` inputs | — |
| `packages` | `query packages [query]` | `package_index` (mock) | limit 50 |
| `package` | `query package <attr>` | Package metadata (mock) | — |
| `report` | `report` | Benefit report for the session | — |

### `eval` — expression syntax (CLI-only)

`query eval` evaluates via `nix eval --json --impure --expr <expr>`.
There are **no injected bindings** — the expression runs in an empty, impure
Nix namespace, so a flake URL like `nixpkgs#lib.version` fails with
`error: undefined variable 'nixpkgs'`. Use one of:

- Pure expressions: `1 + 2`, `builtins.toString 42`
- Flake attributes via the flake registry:
  `(builtins.getFlake "nixpkgs").lib.version`
- Repo-local eval is NOT wired: `eval` cannot see the repo's own `flake.nix`
  outputs. To evaluate a repo option, prefer `query option <path> --eval`,
  which builds `config.<option_path>` itself.

`builtins.getFlake` resolves through the machine's flake registry, so
`nixpkgs` must be registered or a full URI given
(e.g. `github:NixOS/nixpkgs/nixos-unstable`).

### `option` — scope (repo-local only)

`option` answers from the **repo's own graph**: the option node must exist
(declared via `options.<path> = lib.mkOption {...}` in a module of this repo)
and its `declares`/`sets` edges must target `nix_option:<path>`.

An option defined by nixpkgs itself — e.g. `system.stateVersion`, virtually
every `services.*`/`programs.*` option — is **not** in the index, so
`declared_in`/`defined_in` are empty and `opt_type`/`default`/`description`
are `null`. With `--eval`, the value is still evaluated separately. This is
expected: the verb is a repo-introspection tool, not a nixpkgs option browser
(use `manix`/`nixos-option` for that).

## Examples

```bash
nix-repo-navigator query observe nix:a.nix --depth 1
nix-repo-navigator query hop nix:a.nix --relation imports --depth 2 --width 5
nix-repo-navigator query path nix:a.nix nix:d.nix
nix-repo-navigator query blast nix:c.nix --max-depth 3
nix-repo-navigator query find "services.foo" --fuzzy --limit 5
nix-repo-navigator query summarize a.nix
nix-repo-navigator query option services.foo.enable --eval
nix-repo-navigator query eval "1+1" --timeout 10
nix-repo-navigator query impact nix:b.nix
nix-repo-navigator query dependencies nix:a.nix --max-depth 3
nix-repo-navigator query dependents nix:option.nix --max-depth 3
nix-repo-navigator query flake-inputs
nix-repo-navigator query packages ripgrep
nix-repo-navigator report
```

MCP (Inspector):

```bash
npx @modelcontextprotocol/inspector -- python -m repo_navigator.mcp_server --root .
# tools/list -> 6 tools; call_tool -> structuredContent + generation_id
```

Budgets enforced: `hop` raises `ValueError` if `width*depth>100`, `depth>10`
etc.; MCP maps these to `ToolError` (`is_error`).

Cache: `QueryEngine` LRU key `(method, params, generation_id)`, cleared on
`generation` bump.

## Semantics notes

- `observe depth=1` returns the direct neighbourhood (≤20 neighbours).
  `depth>1` is a **full incident-set expansion**: forward + reverse BFS up to
  `depth`, attaching every visited node's own edges — *without* path-anchoring
  to the start node or relevance ranking. For anchored traversal use
  `hop --relation …`, `dependencies`/`dependents`, or `blast`.
- `dependencies`/`dependents` close over the full forward edge set
  (`imports`, `requires`, `python_imports`, `references`, `uses_package`,
  `sets`, `configures`, `generates`). Structural edges (`declares`,
  `specialises`, `passes_args`) are excluded: declaring an option is not a
  dependency on it.
- `blast` returns reverse-BFS nodes but only edges lying on some path to the
  target (sibling imports of visited modules are excluded).
- `find` (non-fuzzy) is FTS5: multi-word queries are AND-ed across tokens (not
  a phrase); an empty AND falls back to OR. `fuzzy`/filtered queries use LIKE.
  Results rank real nodes before synthetic ones and prefer shorter/exact names.
- Synthetic option provenance comes from graph edges:
  `declared_in` from `declares`, `defined_in` from `sets`. Batch-loaded during
  `find_symbol`, so it does not require re-indexing.
- `option --eval`: `defined_in` covers sets from any static attrpath tree
  (including plain `modules.* = { … }` attrsets, not only `config` blocks).
  Eval failures surface as `value_status: "<status>: <first error line>"`
  instead of a bare status.
