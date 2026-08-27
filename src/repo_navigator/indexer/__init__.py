"""Indexer package: file discovery and repository indexing."""

from repo_navigator.indexer.cascade import cascade_dirty
from repo_navigator.indexer.diff_engine import DiffReport, diff_graph
from repo_navigator.indexer.hash_engine import ast_hash, content_hash, merkle_hash
from repo_navigator.indexer.scan import collect_files, index_repo
from repo_navigator.indexer.update_engine import UpdateEngine

__all__ = [
    "DiffReport",
    "UpdateEngine",
    "ast_hash",
    "cascade_dirty",
    "collect_files",
    "content_hash",
    "diff_graph",
    "index_repo",
    "merkle_hash",
]
