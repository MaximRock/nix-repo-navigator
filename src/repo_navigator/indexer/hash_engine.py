"""Hash engine for incremental indexing.

Provides three levels of hashing:

* ``content_hash`` — raw file bytes (fast, catches any change)
* ``ast_hash`` — structural hash of :class:`ParseResult` (ignores formatting)
* ``merkle_hash`` — file's ``ast_hash`` + transitive dependency hashes
"""

from __future__ import annotations

import hashlib
import json

from repo_navigator.models.queries import ParseResult

try:
    import xxhash  # type: ignore[import-untyped]

    def _hash_str(data: str | bytes) -> str:
        if isinstance(data, str):
            data = data.encode("utf-8")
        return xxhash.xxh64(data).hexdigest()

    def _hash_bytes(data: bytes) -> str:
        return xxhash.xxh64(data).hexdigest()

except ImportError:  # pragma: no cover - fallback when xxhash not installed
    def _hash_str(data: str | bytes) -> str:
        if isinstance(data, str):
            data = data.encode("utf-8")
        return hashlib.sha256(data).hexdigest()[:16]

    def _hash_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()[:16]


def content_hash(content: str | bytes) -> str:
    """Hash raw file content (fast path for unchanged-file check)."""
    if isinstance(content, str):
        return _hash_str(content)
    return _hash_bytes(content)


def ast_hash(parse_result: ParseResult) -> str:
    """Structural hash of a :class:`ParseResult`.

    Nodes are sorted by ``id``, edges by ``(source, target, type)``.
    The JSON is canonical (sorted keys, no whitespace) so formatting-only
    changes produce the same hash.  Volatile fields (timestamps,
    ``content_hash``/``ast_hash`` on persisted nodes) are not part of
    ``ParseResult`` and thus naturally excluded.
    """
    # Sort for determinism
    nodes = sorted(parse_result.nodes, key=lambda n: n.id)
    edges = sorted(parse_result.edges, key=lambda e: (e.source, e.target, e.type.value))

    # Serialize with deterministic ordering
    data = {
        "nodes": [n.model_dump(mode="json") for n in nodes],
        "edges": [e.model_dump(mode="json") for e in edges],
    }
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return _hash_str(canonical)


def merkle_hash(file_ast_hash: str, dependency_hashes: list[str]) -> str:
    """Merkle hash for a file: its own ``ast_hash`` plus sorted dependency hashes.

    Uses ``sha256`` so the hash is stable across machines even when
    ``xxhash`` is used for the other two levels (the dependency set can be
    large and the stronger hash is intentional per spec).
    """
    combined = file_ast_hash + "".join(sorted(dependency_hashes))
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
