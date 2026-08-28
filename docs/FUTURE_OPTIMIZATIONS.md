# Future Optimizations (non-blocking, post-MVP)

## 1. blast_radius — edge collection
`blast_radius()` in `queries.py` collects edges through both DB scan and graph traversal, creating some duplication. Works correctly on < 1000 nodes. Optimize when graph grows.

## 2. find_symbol fuzzy search
Fuzzy search uses `LIKE %query%` SQL which is simple but not indexed. For large graphs (>10K nodes), could add FTS5 trigram support. For now works fine.

## 3. eval_expression — stale cache
Current behavior: stale entries are detected and re-evaluated. No bug, but `stale` status could be more aggressively cleared on cascade. For MVP this is fine.