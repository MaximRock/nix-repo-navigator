# Future Optimizations (non-blocking, post-MVP)

## 1. blast_radius — edge collection
`blast_radius()` in `queries.py` collects edges through both DB scan and graph traversal, creating some duplication. Works correctly on < 1000 nodes. Optimize when graph grows.

## 2. find_symbol fuzzy search
Fuzzy search uses `LIKE %query%` SQL which is simple but not indexed. For large graphs (>10K nodes), could add FTS5 trigram support. For now works fine.

## 3. eval_expression — stale cache
Current behavior: stale entries are detected and re-evaluated. No bug, but `stale` status could be more aggressively cleared on cascade. For MVP this is fine.

## 4. parse_flake_nix — duck typing AST walk
`parse_flake_nix()` in `flake_parser.py` walks the AST via duck typing (`hasattr(expr, "attrs")`) instead of using `ast_extract`. Works correctly for simple cases (`inputs.foo.url = "..."`) but may miss complex flake structures. For MVP this is fine — rewrite through `ast_extract` if flake parsing becomes a priority.

## 5. Test naming: test_mcp_server_has_11_tools
Test name says "11 tools" but Phase 9 added `list_flake_inputs`, `list_packages`, `get_package` → total is now 14. The assertion is correct (expected set updated), only the test name/comment is stale. Cosmetic, fix when touching the file next.