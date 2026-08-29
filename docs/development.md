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

## Adding a Parser Plugin (Tier 1)

```python
# src/repo_navigator/parsers/plugins/my.py
from pathlib import Path
from repo_navigator.parsers.base import BaseParser
from repo_navigator.parsers.registry import LanguageConfig, register_language
from repo_navigator.models.queries import ParseResult
from repo_navigator.models.nodes import RawNode, NodeType
from repo_navigator.models.edges import RawEdge, EdgeType

@register_language(LanguageConfig(name="myLang", extensions=[".my"], tier=1))
class MyParser(BaseParser):
    language = "myLang"
    extensions = [".my"]
    tier = 1
    enabled = True

    def parse(self, path: Path, content: str) -> ParseResult:
        # Simple regex or tree-sitter
        nodes = [RawNode(id=f"my:{path}:{1}", type=NodeType.heading, name="hello", path=str(path))]
        edges = []
        return ParseResult(nodes=nodes, edges=edges)
```

Enable:

```bash
REPO_NAVIGATOR_PLUGINS='["myLang","kdl"]' nix-repo-navigator index .
# or Config(plugins=["myLang"])
```

Nix-first: Tier 1 is indexed only if `should_parse_file(path, graph, config)` is True (`.config/` or `configures` edge + `plugins`).

See `src/repo_navigator/parsers/plugins/kdl.py` (KDL bind/rule/spawn, mock without tree-sitter).

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
