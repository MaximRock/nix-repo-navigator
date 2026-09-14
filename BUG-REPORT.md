# Bug report: nix-repo-navigator 0.2.0

**Tested against:** flake rev `0d7233de` / `/nix/store/lf1a73jnkcw2jlsr5syc8rpc4pm8am31-python3.13-nix-repo-navigator-0.2.0`
**Repo:** `/home/max/.dotfiles` — canonical NixOS flake, 141 `.nix` files
**Command:** `nix-repo-navigator start --root /home/max/.dotfiles`

## Status: ✅ All bugs fixed (stages 1–6)

### Fixes applied

| Bug | Description | Fix | Stage |
|-----|-------------|-----|-------|
| BUG 1 | bare `import ./x` produces no imports edges | Added `_collect_bare_imports()` in `ast_extract.py`; walks `FunctionCall` nodes calling bare `import` with relative path argument | 1 |
| BUG 2 | `blast_radius` duplicates edges | Fixed dedup in `queries.py::blast_radius` with `_add()` helper and `edge_ids` set | 2 |
| BUG 3 | `files_tracked` misleading metric | Renamed to `files_served` in `models/queries.py`, `graph/queries.py`, `mcp_server.py`; documented semantics | 3 |
| Python plugin | never activates in this repo | Added `--plugins`/`--parse-unreferenced` flags to `cli.py::start`; env vars still apply when flags omitted | 4 |
| Python smells | `heading` type, `package_ref` imports, truncated call chains | Added `python_module` node type; import targets use `python_module`; `_CallCollector` preserves full dotted chains | 4 |
| Docs gaps | `eval_expression` / `introspect_option` behavior undocumented | Documented in `docs/query-verbs.md` (syntax, scope, examples) | 5 |

After `repo_navigator_refresh` (generation 2):

- DB nodes: 140 `nix_module` / 286 `nix_option` / 5 `nix_function` / 33 `flake_input`
- DB edges: 434 (55 `imports`, 59 `declares`, 279 `sets`, 22 `configures`, 19 `uses_package`)
- `find_symbol` finds `mkNixosConfiguration`, `mkCodeOptions`, `mkTerminalOptions`

The index is full. The problem is that import edges are only extracted from the
NixOS `imports = [ ... ]` attribute, so the top of the reference chain
(flake → lib → modules/) is invisible to graph traversal.

## BUG 1 (major): ✅ FIXED — bare `import ./x` expressions produce no imports edges

**Root cause (was):** `parsers/nix/ast_extract.py::_process_imports` (L323-366) only handles
the `imports` attribute. A bare `import ./foo` call — used by `flake.nix`
(`import ./lib`) and `lib/default.nix` (`import ./overlays.nix`,
`import ./qtile/theme.nix`, `import ../modules/nixos`, …) — was never captured.

**Fix:** Added `_collect_bare_imports()` (ast_extract.py) which walks
`FunctionCall` nodes where the callable is a bare `Import` with a relative
path argument. Also added `_normalise_import()` (module_parser.py) to
resolve directory paths to `default.nix`. Bare imports now emit `imports`
edges and register `file:*` nodes for the imported files.

## BUG 2 (minor): ✅ FIXED — blast_radius duplicates edges

**Root cause (was):** The first loop in `blast_radius` added edges to the graph without
registering their IDs in `edge_ids`, so the second dedup loop couldn't
filter them out. The same `declares` edge appeared twice in `edges[]`.

**Fix:** Extracted a local `_add()` helper that registers the edge in `edge_ids`
before appending; the second loop now correctly skips already-seen edges.
Test: `test_blast_no_duplicate_edges` (test_query_navigation.py).

## BUG 3 (minor, misleading metric): ✅ FIXED — report.files_tracked renamed to files_served

**Was:** `files_tracked: 2` while the index has 140 modules — the counter in
`graph/queries.py` counts distinct paths served through path-based queries this
session, not indexed files.

**Fix:** Renamed `files_tracked` → `files_served` in `BenefitReport`
(`models/queries.py`), `graph/queries.py`, `mcp_server.py` docstring.
Documented semantics in `docs/query-verbs.md`:
"`files_served` counts distinct source-file paths served through queries
this session (deduplicated) — it is *not* the number of indexed files."

## Python plugin (tier 1): ✅ FIXED — activation + smells

**Root cause (was):** Two gates blocked every `.py` file:
1. `plugins=[]` default with no CLI flag to enable plugins.
2. Nix-first reference rule: `.py` files parsed only if `.config` in path or `configures` edge targets exact file.

**Fix:** Added `--plugins` and `--parse-unreferenced` flags to `cli.py::start`.
Env vars (`REPO_NAVIGATOR_PLUGINS`, `REPO_NAVIGATOR_PARSE_UNREFERENCED`)
still apply when flags are not passed. Smells fixed:

- Added `python_module` to `NodeType`; module nodes now typed `python_module`.
- Import targets use `python_module` (not `package_ref`) — stdlib/third-party
  imports don't pollute the package index.
- `_CallCollector` preserves full dotted chains: `qtile.lazy.spawn(...)` recorded
  as `qtile.lazy.spawn`; resolution requires full chain match.
- E2E test `test_e2e_python_plugin_activation_qtile_scenario` verifies all
  three gates (no plugin → no py-nodes; plugin without parse_unreferenced → no
  py-nodes; full activation → `python_module`/`py_function` + `python_imports`
  edges without `package_ref`).

**Usage:**
```bash
nix-repo-navigator start --plugins python,kdl --parse-unreferenced
```

## Docs gaps (not bugs): ✅ FIXED

- Documented in `docs/query-verbs.md` (new sections + expanded table).
- `eval_expression`: documented that it uses `nix eval --json --impure --expr`
  with **no injected bindings**; `nixpkgs#lib.version` is expected to fail.
  Examples added: `builtins.getFlake "nixpkgs"`, `builtins.toString 42`.
- `introspect_option`: documented scope (repo-local options only; nixpkgs
  options → `null` by default; `include_value=true` evaluates separately).
  Recommends `manix`/`nixos-option` for nixpkgs options.
- Added `dependencies`/`dependents`/`report` to the verbs table.
- Tool count corrected: 17 (was documented as 14).

## Repro (for verification)

```bash
# BUG 1: bare imports should now produce edges
nix-repo-navigator start --root /home/max/.dotfiles
# MCP: repo_navigator_refresh → repo_navigator_observe("nix:flake.nix")
# expect neighbors: [nix:lib/default.nix] (imports edge)

# Python plugin: activate with CLI flags
nix-repo-navigator start --root /home/max/.dotfiles \
  --plugins python --parse-unreferenced
# MCP: repo_navigator_refresh → repo_navigator_find_symbol("autostart_apps", lang="python")
# expect: py_function:... node found

# BUG 2: blast_radius should have no duplicate edges
# (tested via test_blast_no_duplicate_edges)

# BUG 3: report.files_served (not files_tracked)
# CLI: nix-repo-navigator report
# expect: files_served (distinct source paths served this session)
```