# Development

## Setup

```bash
uv venv
uv pip install -e ".[dev,plugins]"
# or pip
pip install -e ".[dev]"

pytest tests/ -q          # 355+ tests
pytest tests/ -q --cov
ruff check . && ruff format --check .
mypy src
hatch build && twine check dist/*
```

## Adding a Parser Plugin: new language in 5 steps

A plugin language (Tier 1+) participates only insofar as it is tied to the
Nix configuration (`.config/`, a `configures`/`generates` edge, or an explicit
single-file request). The parsing pipeline itself is Nix-agnostic: the
registry and `should_parse_file` do all the gating.

### Step 1 — Write the parser

Create `src/repo_navigator/parsers/plugins/<lang>.py`:

```python
# src/repo_navigator/parsers/plugins/my.py
from pathlib import Path

from repo_navigator.models.edges import RawEdge, EdgeType
from repo_navigator.models.nodes import RawNode, NodeType
from repo_navigator.models.queries import ParseResult
from repo_navigator.parsers.base import BaseParser
from repo_navigator.parsers.registry import LanguageConfig, register_language


@register_language(LanguageConfig(name="my", extensions=[".my"], tier=1))
class MyParser(BaseParser):
    language = "my"
    extensions = [".my"]
    tier = 1
    enabled = True

    def parse(self, path: Path, content: str) -> ParseResult:
        nodes = [
            RawNode(
                id=f"my:{path}:1",       # stable id: lang:path:symbol
                type=NodeType.heading,
                name="hello",
                path=str(path),
                lang="my",
            )
        ]
        edges: list[RawEdge] = []  # e.g. RawEdge(source=..., target=..., type=EdgeType.requires)
        return ParseResult(nodes=nodes, edges=edges)
```

Notes:
- The builder and DB serialize `RawNode`/`RawEdge` exactly as written; keep
  `id` stable — it is the identity used by queries, edges and the future
  visualizer. Prefer `lang:path:symbol`.
- `register_language` instantiates the class and overrides `language`,
  `extensions`, `tier`, `enabled` from the `LanguageConfig` — keep the class
  attrs in sync for readability.

### Step 2 — Register the plugin module

Import it in `src/repo_navigator/parsers/plugins/__init__.py` so registration
happens on import:

```python
from repo_navigator.parsers.plugins import kdl  # noqa: F401
from repo_navigator.parsers.plugins import python  # noqa: F401
from repo_navigator.parsers.plugins import my  # noqa: F401
```

### Step 3 — Add node/edge types if needed

When your language needs new kinds of nodes or relations, extend
`models/nodes.py` (`NodeType`) and `models/edges.py` (`EdgeType`). Reuse
existing enum members where possible (`references`, `requires`, `sources`,
`spawns`, `calls`, `binds_key` are already available for plugin use). The
package index only counts `package:`-prefixed `package_ref` nodes, so plugin
symbols never pollute it.

### Step 4 — Write a test

Mirror `tests/unit/test_python_parser.py`: feed a small sample, assert the
emitted `RawNode`/`RawEdge` content, plus a registry lookup through
`get_parser_for_file`. Add an end-to-end check in
`tests/integration/test_builder_flow.py` that a referenced plugin file is
discovered on a first-run two-pass index (see the Python example).

```python
def test_my_parse() -> None:
    result = MyParser().parse(Path("a.my"), "hello world")
    assert any(n.type == NodeType.heading for n in result.nodes)
```

### Step 5 — Enable and verify

```bash
REPO_NAVIGATOR_PLUGINS='["my","python","kdl"]' nix-repo-navigator index .
# or Config(plugins=["my"])
```

Then run the checks (see AGENTS.md):

```bash
.venv/bin/python -m pytest tests/ -q
ruff check . && ruff format --check .
```

Nix-first relevance: Tier 1-3 files are parsed only when
`should_parse_file(path, graph, config)` returns True — the plugin is listed
in `Config.plugins` **and** the path contains `.config/`, or there is a
`configures`/`generates` edge (or a `file:` node) referencing it, or
`Config.parse_unreferenced=True` (universal mode), or the file is passed
explicitly in single-file mode. Tier 0 (`.nix`) is always parsed.

Bulk indexing is two-pass: Tier 0 first, then plugin files. `generation_id`
is bumped exactly once per `index_repo()` call (both passes pass
`increment_generation=False`, the caller bumps once at the end).

See `src/repo_navigator/parsers/plugins/python.py` for a full AST-based
example and `src/repo_navigator/parsers/plugins/kdl.py` for a mock-style one.

## Project Layout

```
src/repo_navigator/
  config.py, cli.py, mcp_server.py
  graph/ (db.py, nx_graph.py, builder.py, queries.py)
  parsers/ (base.py, registry.py, nix/ (lexer, parser, ast_extract, module_parser, flake_parser), plugins/kdl.py)
  indexer/ (hash_engine, diff_engine, cascade, update_engine, scan, event_router, watcher.py)
  nix/ (eval.py, eval_cache.py, package_index.py)
  watcher/ (filesystem.py)
  models/ (nodes, edges, queries, file_state, option_value)
```

## Testing

- `tests/unit/` — lexer, parser, ast, builder, db, nx_graph, hash, cascade, queries, mcp, eval, flake, package, hm, kdl
- `tests/golden/` — `lexer/` (16), `parser/` (21), `extract/` (16) with `*_expected.json` (update with `--update-golden`)
- `tests/integration/` — builder flow, mcp session, nix eval, hm/package/flake, kdl

```bash
pytest tests/golden/lexer -q --update-golden
pytest tests/integration/test_kdl_flow.py -v
npx @modelcontextprotocol/inspector -- python -m repo_navigator.mcp_server --root /tmp/repo
```

## Release

```bash
hatch version 0.1.0
hatch build
twine check dist/*
# manual: hatch publish (requires PYPI_API_TOKEN)
git tag v0.1.0 && git push --tags
gh release create v0.1.0 dist/* --notes-file CHANGELOG.md
```

CI: `.github/workflows/ci.yml` (ruff, mypy, pytest, hatch build).
