# repo-navigator

Knowledge-graph assistant for NixOS and home-manager repositories. Builds an
incremental, multi-level graph with Nix at the root and exposes an MCP interface
for AI agents.

> Work in progress. See `repo-navigator-spec-v3.md` for the specification and
> `IMPLEMENTATION_PLAN.md` for the implementation plan.

## Development

```bash
uv venv
uv pip install -e ".[dev]"
pytest tests/ -v
```
